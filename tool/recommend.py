"""Step 3: how much to spend by channel next month, and over the next 12 months.

1. Response curve per reallocated channel: new customers = A x spend^b x (month-of-year effect) x (trend).
   Four versions are tried each run; the one that predicted the last 12 months best (one month ahead) is used.
   b = how much 1% more spend grows new customers (b = 0.8: +10% spend -> about +8% customers).
2. Each month's budget is this year's spend in the same month (the brief's comparison point), so the total is fixed.
   Every channel starts at its floor (this year's spend less max_change_vs_last_year); the rest is handed out in small
   steps, each to the channel where it buys the most H-month contribution per dollar. Up to this year's spend there is
   no test. Above it a channel only gets money while its next customer pays back within the payback window, and never
   beyond this year's spend + max_change_vs_last_year or its record month + max_above_record_month.
   Channels whose response to spend can't be measured reliably are held at this year's spend.
"""
import numpy as np
import pandas as pd

from . import constants as C
from .metrics import mi_label

MODELS = {"season": (True, False), "season+trend": (True, True), "trend": (False, True), "none": (False, False)}

REASON_LABEL = {
    "grew": "up: pays back", "grew_until_payback": "up to its payback limit", "capped_limit": "up to the change limit",
    "capped_record": "up to the record-month cap", "cut_no_payback": "down: doesn't pay back",
    "cut_lower_value": "down: other channels buy more value", "unchanged_no_payback": "same: can't grow (doesn't pay back)",
    "unchanged": "same", "low_confidence": "held: uncertain response", "no_curve": "held: too little history",
    "no_value": "held: no value per customer yet", "no_spend_last_year": "held: no spend last year",
    "held_flat": "held flat: outside the acquisition budget"}


def _design(months, spend, season, trend, t0):
    m = np.asarray(months) % 12
    cols = [np.ones(len(m)), np.log(np.asarray(spend, dtype=float))]
    if season:
        cols += [(m == k).astype(float) for k in range(1, 12)]
    if trend:
        cols.append((np.asarray(months) - t0) / 12.0)
    return np.column_stack(cols)


def _fit(df, season, trend, t0):
    X = _design(df.index, df["spend"], season, trend, t0)
    y = np.log(df["new"].to_numpy(dtype=float))
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta
    dof = max(len(y) - X.shape[1], 1)
    cov = (resid @ resid / dof) * np.linalg.pinv(X.T @ X)
    return beta, float(np.sqrt(cov[1, 1]))


def fit_curves(new, realloc, s, log):
    """One response curve per reallocated channel, chosen by one-month-ahead error over the last 12 months."""
    curves = {}
    hist = s["response_history_months"]
    for ch in realloc.columns:
        if ch not in new.columns:
            log.warn(f"{ch}: no customers in customers.csv map to this channel, so its response to spend can't be "
                     "measured; it is held at this year's spend. Check that channel_mapping and spend_mapping use the "
                     "same channel name.")
            curves[ch] = None
            continue
        df = pd.DataFrame({"spend": realloc[ch], "new": new[ch]}).dropna()
        df = df[(df["spend"] > 0) & (df["new"] > 0)]
        if hist:
            df = df.iloc[-hist:]
        if len(df) < C.MIN_HISTORY_MONTHS:
            log.warn(f"{ch}: only {len(df)} months of spend history, too few to measure its response to spend. "
                     "Held at this year's spend.")
            curves[ch] = None
            continue
        t0 = int(df.index.min())
        scores = {}
        for name, (season, trend) in MODELS.items():
            if season and len(df) < C.MIN_HISTORY_MONTHS_SEASONAL:
                continue
            errs = []
            for cut in range(len(df) - 12, len(df)):
                beta, _ = _fit(df.iloc[:cut], season, trend, t0)
                row = df.iloc[cut:cut + 1]
                pred = np.exp(_design(row.index, row["spend"], season, trend, t0) @ beta)[0]
                errs.append(abs(pred - row["new"].iloc[0]) / row["new"].iloc[0])
            scores[name] = float(np.mean(errs))
        best = min(scores, key=scores.get)
        season, trend = MODELS[best]
        beta, se = _fit(df, season, trend, t0)
        b = float(np.clip(beta[1], C.RESPONSE_MIN, C.RESPONSE_MAX))
        if beta[1] > C.RESPONSE_MAX:
            log.warn(f"{ch}: fitted response above {C.RESPONSE_MAX:g} (more spend looks increasingly efficient). "
                     f"Capped at {C.RESPONSE_MAX:g}.")
        if beta[1] < C.RESPONSE_MIN:
            log.warn(f"{ch}: fitted response below {C.RESPONSE_MIN:g} (more spend barely adds customers). "
                     f"Set to {C.RESPONSE_MIN:g}.")
        spread = df["spend"].std() / df["spend"].mean()
        reasons = []
        if spread < C.LOW_CONFIDENCE_SPEND_VARIATION:
            reasons.append(f"its spend has varied only {spread:.0%} around its average")
        if se > C.LOW_CONFIDENCE_STD_ERROR:
            reasons.append(f"the estimate is noisy (b = {b:.2f} ± {se:.2f})")
        curves[ch] = {"b": b, "b_se": se, "model": best, "error_last_12m": scores[best], "beta": beta,
                      "season": season, "trend": trend, "t0": t0, "months": len(df),
                      "spend_min": float(df["spend"].min()), "spend_max": float(df["spend"].max()),
                      "confidence": "low" if reasons else "ok", "confidence_reason": " and ".join(reasons),
                      "all_model_errors": scores}
    return curves


def predict_new(curve, month, spend):
    """Predicted new customers (whole business) in a month for a given spend."""
    X = _design([month], [max(spend, 1.0)], curve["season"], curve["trend"], curve["t0"])
    beta = curve["beta"].copy()
    beta[1] = curve["b"]
    return float(np.exp(X @ beta)[0])


def next_customer_cost(curve, month, spend, step):
    """What the next customer costs at a spend level: step / extra customers bought by that step."""
    if not curve or spend <= 0:
        return np.nan
    dn = predict_new(curve, month, spend + step) - predict_new(curve, month, spend)
    return step / dn if dn > 0 else np.nan


def allocate(base, curves, value, window_value, s, month, record=None, b_scale=1.0, customer_scale=1.0):
    """One month's split of a fixed budget (= the sum of base, this year's spend in that month).
    Returns spend per channel, the customers function n_at(channel, spend), a reason per channel, the cost of the
    next customer at this year's spend (the payback test), and any budget left over (always 0 by construction)."""
    g = s["max_change_vs_last_year"]
    r = s["max_above_record_month"]
    record = record or {}
    held, lo, hi = {}, {}, {}
    for c in base.index:
        cv = curves.get(c)
        if base[c] <= 0:
            held[c] = "no_spend_last_year"
        elif not cv:
            held[c] = "no_curve"
        elif c not in window_value or pd.isna(value.get(c, np.nan)):
            held[c] = "no_value"
        elif s["hold_low_confidence_channels"] and cv["confidence"] == "low":
            held[c] = "low_confidence"
        if c in held:
            lo[c] = hi[c] = float(base[c])
            continue
        lo[c] = float(base[c]) * (1 - g)
        cap = float(base[c]) * (1 + g)
        if r is not None and c in record:
            cap = min(cap, record[c] * (1 + r))
        hi[c] = max(float(base[c]), cap)          # the record cap never forces a cut below this year's spend
    live = [c for c in base.index if c not in held]
    n0 = {c: predict_new(curves[c], month, base[c]) * customer_scale for c in live}
    bb = {c: min(curves[c]["b"] * b_scale, 1.0) for c in live}

    def n_at(c, spend):
        """Customers at a spend level, anchored at this year's spend so a different b changes only the slope."""
        if c not in n0:
            return np.nan
        return n0[c] * (max(spend, 1.0) / base[c]) ** bb[c]

    budget = float(base.sum())
    step = max(budget / C.ALLOCATION_STEPS, 1.0)

    def cost(c, x, dx):
        dn = n_at(c, x + dx) - n_at(c, x)
        return dx / dn if dn > 0 else np.inf

    cost_at_base = {c: cost(c, float(base[c]), step) for c in live}
    x = dict(lo)
    remaining = budget - sum(lo.values())
    stopped = set()                               # channels whose next customer stopped paying back above base
    while remaining > 1e-6:
        best, best_val, best_dx = None, -1.0, 0.0
        for c in live:
            if x[c] >= hi[c] - 1e-9:
                continue
            below = x[c] < base[c] - 1e-9
            dx = min(step, (base[c] if below else hi[c]) - x[c], remaining)   # steps stop exactly at this year's spend
            if dx <= 1e-9:
                continue
            dn = n_at(c, x[c] + dx) - n_at(c, x[c])
            if dn <= 0:
                continue
            if not below and dx / dn > window_value[c]:                       # payback test above this year's spend
                stopped.add(c)
                continue
            val = dn * value[c] / dx
            if val > best_val:
                best, best_val, best_dx = c, val, dx
        if best is None:
            break
        x[best] += best_dx
        remaining -= best_dx

    reasons = {}
    for c in base.index:
        if c in held:
            reasons[c] = held[c]
            continue
        fails = cost_at_base[c] > window_value[c]
        if x[c] > base[c] + 0.5:
            if x[c] >= hi[c] - 0.5:
                reasons[c] = "capped_record" if hi[c] < base[c] * (1 + g) - 0.5 else "capped_limit"
            else:
                reasons[c] = "grew_until_payback" if c in stopped else "grew"
        elif x[c] < base[c] - 0.5:
            reasons[c] = "cut_no_payback" if fails else "cut_lower_value"
        else:
            reasons[c] = "unchanged_no_payback" if fails else "unchanged"
    return pd.Series(x), n_at, reasons, cost_at_base, max(remaining, 0.0)


def plan(P, s, new, realloc, curves, value, window_value, log, months_ahead=12, b_scale=1.0, customer_scale=1.0):
    """Next month and the next 12 months: baseline (this year's allocation, scaled to the budget) vs recommended."""
    as_of = P["as_of"]
    ltm = realloc.reindex(range(as_of - 11, as_of + 1)).fillna(0)
    ltm_total = float(ltm.values.sum())
    budget = s["acquisition_budget_annual"] or ltm_total
    scale = budget / ltm_total if ltm_total else 1.0
    record = realloc.max().to_dict()
    rows = []
    for i in range(1, months_ahead + 1):
        m = as_of + i
        base = ltm.loc[m - 12] * scale
        rec, n_at, reasons, cost_base, _ = allocate(base, curves, value, window_value, s, m, record, b_scale,
                                                     customer_scale)
        step = max(float(base.sum()) / C.ALLOCATION_STEPS, 1.0)
        for c in base.index:
            cv = curves.get(c)
            if c in cost_base:
                nb, nr = n_at(c, base[c]), n_at(c, rec[c])
            else:
                nb = nr = float(new[c].get(m - 12, 0.0)) if c in new.columns else 0.0
                if cv and base[c] > 0:                  # held channels: show the cost for information only
                    cost_base[c] = next_customer_cost(cv, m, float(base[c]), step)
            rows.append({"month": mi_label(m), "m": m, "channel": c, "baseline_spend": float(base[c]),
                         "recommended_spend": float(rec[c]), "baseline_customers": nb, "recommended_customers": nr,
                         "b": cv["b"] * b_scale if cv else np.nan, "value_per_customer": value.get(c, np.nan),
                         "value_in_window": window_value.get(c, np.nan),
                         "cost_at_this_years_spend": cost_base.get(c, np.nan), "reason": reasons[c]})
    t = pd.DataFrame(rows)
    t["customers_change"] = t["recommended_customers"] - t["baseline_customers"]
    t["contribution_change"] = (t["customers_change"] * t["value_per_customer"]).where(t["customers_change"] != 0, 0.0)
    return t, budget


def by_calendar_year(t, curves, H):
    """Where the H-month contribution of next year's customers gained and lost lands, by calendar year."""
    acc = {}
    for r in t.itertuples():
        cv = curves.get(r.channel)
        if cv is None or not r.customers_change:
            continue
        inc = np.diff(np.concatenate([[0.0], cv.values[:H]]))
        for k in range(H):
            key = (int((r.m + k) // 12), r.channel)
            acc[key] = acc.get(key, 0.0) + r.customers_change * inc[k]
    if not acc:
        return pd.DataFrame({"year": [], "total": []})
    df = pd.Series(acc).unstack(fill_value=0.0)
    df["total"] = df.sum(axis=1)
    df.index.name = "year"
    return df.reset_index()


def explain(row, s):
    """One plain-English line per channel for next month."""
    H, W = s["ltv_horizon_months"], s["payback_window_months"]
    g, r = s["max_change_vs_last_year"], s["max_above_record_month"]
    why = row["reason"]

    def money(x):
        return f"${x:,.0f}" if x is not None and pd.notna(x) and np.isfinite(x) else "n/a"

    mc, mb = row.get("next_customer_cost"), row.get("cost_at_this_years_spend")
    v, w = row.get("value_per_customer"), row.get("value_in_window")
    earns = f"earns {money(w)} within {W} months and {money(v)} over {H}"
    text = {
        "grew": f"Pays back: next customer costs {money(mc)}, {earns}.",
        "grew_until_payback": f"Grows until its next customer stops paying back within {W} months (costs {money(mc)}, {earns}).",
        "capped_limit": f"Pays back: next customer costs {money(mc)}, {earns}. Capped at +{g:.0%} vs the same month last year.",
        "capped_record": (f"Pays back: next customer costs {money(mc)}, {earns}. Capped at {(r or 0):.0%} above its "
                          "record month; beyond that the data can't say how customers respond."),
        "cut_no_payback": (f"Doesn't pay back within {W} months: next customer costs {money(mb)} but earns {money(w)} "
                           f"in {W} months ({money(v)} over {H}), so money moves to channels that do."),
        "cut_lower_value": f"Pays back, but other channels buy more {H}-month value per dollar: next customer costs {money(mc)}, {earns}.",
        "unchanged_no_payback": f"Can't grow: next customer costs {money(mb)} but earns {money(w)} within {W} months.",
        "unchanged": f"Unchanged: next customer costs {money(mc)}, {earns}.",
        "low_confidence": (f"Held at this year's spend: its response to spend can't be measured reliably, because "
                           f"{row.get('confidence_reason') or 'the estimate is too uncertain'}."),
        "no_curve": "Held at this year's spend: not enough history to measure how its customers respond to spend.",
        "no_value": "Held at this year's spend: no value per customer yet.",
        "no_spend_last_year": "Held at $0: no spend in this month last year to start from.",
    }
    return text.get(why, "")
