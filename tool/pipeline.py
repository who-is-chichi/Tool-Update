"""Runs every step in order and returns all tables. Used by both the app and the command line."""
import copy

import numpy as np
import pandas as pd

from . import constants as C
from . import metrics as M
from . import recommend as R
from .clean import load_all
from .common import Log, check_settings, load_settings

AFFECT_ROWS = ("gross_margin", "returns_resold", "fulfillment_cost_per_order", "payment_processing_rate",
               "return_handling_cost_per_refund")
AFFECT_VALUE = AFFECT_ROWS + ("payback_window_months", "ltv_horizon_months", "recent_cohort_months",
                              "reference_cohort_months")
FLAT_WHY = {"hold_flat": "Held flat: outside the acquisition budget; the data can't show which customers it brings.",
            "retention": "Held flat: retention spend for existing customers, outside the acquisition budget."}


def _values(P, s, log):
    v, curves, factors = M.value_curves(P, s, log)
    H, W = s["ltv_horizon_months"], s["payback_window_months"]
    value = v[f"value_{H}m_today"].to_dict()
    window = {c: float(curves[c].iloc[W - 1]) for c in curves}
    ref = v[f"value_{H}m_latest_full_cohorts"].to_dict()
    ref_value = {c: (ref[c] if pd.notna(ref.get(c)) else value[c]) for c in value}
    ref_window = {c: window[c] * ref_value[c] / value[c] if value[c] else window[c] for c in window}
    return v, curves, factors, value, window, ref_value, ref_window


def _direction(t):
    first = t[t["m"] == t["m"].min()]
    return {r.channel: (1 if r.recommended_spend > r.baseline_spend * 1.005 else
                        -1 if r.recommended_spend < r.baseline_spend * 0.995 else 0) for r in first.itertuples()}


def run(data_folder, settings, with_sensitivity=True):
    """settings: a dict from load_settings() (the app may edit it) or a path to settings.xlsx."""
    s = load_settings(settings) if not isinstance(settings, dict) else check_settings(copy.deepcopy(settings))
    log = Log()
    for note in s.get("settings_notes", []):
        log.warn(note)
    d = load_all(data_folder, s, log)
    P = M.prepare(d, s, log)
    H, W = s["ltv_horizon_months"], s["payback_window_months"]
    as_of = P["as_of"]
    out = {"settings": s, "log": log, "as_of": M.mi_label(as_of), "data_start": P["data_start"]}

    # ---- metrics by channel and cohort
    rep = M.repeat_rates(P, s)
    out["repeat"] = rep
    out["repeat_decomposition"] = M.repeat_decomposition(rep)
    out["early_read"] = M.early_read(P, quarters=16)
    v, curves, factors, value, window, ref_value, ref_window = _values(P, s, log)
    out["value"], out["value_curves"] = v, curves
    out["cohort_ltv"] = M.cohort_ltv(P, s, factors)
    out["backtest"] = M.backtest(P, s)
    cac_year, cac_l3m, blended, new, realloc = M.cac_tables(P, s)
    out["cac_year"], out["cac_l3m"], out["blended_cac"] = cac_year, cac_l3m, blended
    out["payback_cohort"] = M.payback_by_cohort(P, cac_year)
    out["record_month"] = realloc.max()

    # ---- checks against finance, computed at finance's month so they compare like with like
    fm = s.get("finance_as_of_month")
    fin_m = int(fm[:4]) * 12 + int(fm[5:7]) - 1 if fm else None
    if fin_m is not None and fin_m < as_of:
        P_f = M.prepare(M.cut_data(d, fin_m), s, Log())
        out["reconciliation"] = M.reconciliation(P_f, s, M.repeat_rates(P_f, s), M.cac_tables(P_f, s)[2])
        out["reconciliation_as_of"] = M.mi_label(fin_m)
    else:
        if fin_m is not None and fin_m > as_of:
            log.warn(f"finance_as_of_month ({fm}) is after the data ends ({M.mi_label(as_of)}); the checks against "
                     "finance use the latest data.")
        out["reconciliation"] = M.reconciliation(P, s, rep, blended)
        out["reconciliation_as_of"] = M.mi_label(as_of)

    M.latest_month_checks(P, realloc, log)
    out["service_issues"] = M.service_issues(d["notes"], P, s["note_themes"])

    # ---- response curves and recommendation
    rc = R.fit_curves(new, realloc, s, log)
    out["response"] = pd.DataFrame(
        [{"channel": c, "model": r["model"], "b": r["b"], "b_std_error": r["b_se"], "error_last_12m": r["error_last_12m"],
          "months_used": r["months"], "spend_min": r["spend_min"], "spend_max": r["spend_max"],
          "confidence": r["confidence"], "why_low_confidence": r["confidence_reason"]} for c, r in rc.items() if r],
        columns=["channel", "model", "b", "b_std_error", "error_last_12m", "months_used", "spend_min", "spend_max",
                 "confidence", "why_low_confidence"])
    t, budget = R.plan(P, s, new, realloc, rc, value, window, log)
    total = float(t["contribution_change"].sum())
    out["plan"], out["budget"], out["impact"] = t, budget, total
    out["impact_by_calendar_year"] = R.by_calendar_year(t, curves, H)

    # ---- next month
    nxt = t[t["m"] == t["m"].min()].copy()
    m1 = int(nxt["m"].iloc[0])
    nxt["change"] = nxt["recommended_spend"] - nxt["baseline_spend"]
    nxt["change_pct"] = nxt["change"] / nxt["baseline_spend"].replace(0, np.nan)
    last_actual = realloc.loc[as_of] if as_of in realloc.index else pd.Series(dtype=float)
    nxt["last_month_spend"] = nxt["channel"].map(last_actual).astype(float)
    nxt["vs_last_month"] = nxt["recommended_spend"] / nxt["last_month_spend"].replace(0, np.nan) - 1
    step = max(nxt["baseline_spend"].sum() / C.ALLOCATION_STEPS, 1.0)
    nxt["next_customer_cost"] = [R.next_customer_cost(rc.get(r.channel), r.m, r.recommended_spend, step)
                                 for r in nxt.itertuples()]
    nxt["confidence_reason"] = nxt["channel"].map(lambda c: rc[c]["confidence_reason"] if rc.get(c) else "")
    nxt["why"] = [R.explain(r, s) for _, r in nxt.iterrows()]
    out["next_month"] = nxt.reset_index(drop=True)
    out["last_month"] = M.mi_label(as_of)

    sp = P["spend"].assign(m=P["spend_m"])
    flat = sp[(sp["treatment"] != "reallocate") & (sp["m"] == m1 - 12)]
    flat = flat.groupby(["channel", "treatment"], as_index=False)["spend_usd"].sum()
    flat["why"] = flat["treatment"].map(FLAT_WHY).fillna("Held flat: outside the acquisition budget.")
    out["next_month_held_flat"] = flat.rename(columns={"spend_usd": "spend"})

    month_name = M.mi_name(m1)
    for r in nxt.itertuples():
        top = float(realloc[r.channel].max()) if r.channel in realloc.columns else np.nan
        if r.reason == "low_confidence":
            log.warn(f"{r.channel} is held at this year's spend in {month_name}: its response to spend can't be "
                     f"measured reliably, because {r.confidence_reason}.")
        elif r.reason == "capped_record":
            log.warn(f"{r.channel} is capped at {s['max_above_record_month']:.0%} above its record month (${top:,.0f}): "
                     f"${r.recommended_spend:,.0f} in {month_name}. Above its record the forecast is less certain.")
        elif top == top and r.recommended_spend > top * 1.0001:
            log.warn(f"{r.channel}: recommended {month_name} spend (${r.recommended_spend:,.0f}) is above any month in "
                     f"the data (${top:,.0f}); treat its forecast with care.")

    imp = t.groupby("channel")[["baseline_spend", "recommended_spend", "baseline_customers", "recommended_customers",
                                "customers_change", "contribution_change"]].sum()
    imp["value_per_customer"] = imp.index.map(value)
    out["impact_by_channel"] = imp.reset_index()

    # ---- scorecard
    allc = P["cust"]
    last12 = allc[allc["start_m"] > as_of - 12]
    y1 = rep[rep["year"] == 1]
    er = out["early_read"]
    out["early_read_latest_quarter"] = er.columns[-1] if len(er.columns) else ""
    cost0 = nxt.set_index("channel")["cost_at_this_years_spend"]
    rows = []
    for c in sorted(set(allc["channel"])):
        yc = y1[y1["channel"] == c].sort_values("cohort")
        cac = cac_l3m.get(c, np.nan)
        rows.append({"channel": c,
                     "new_customers_last_12m": (last12["channel"] == c).sum() / s["sample_rate"],
                     "share_of_new": (last12["channel"] == c).mean(),
                     "year1_repeat_latest_cohort": yc["repeat_rate"].iloc[-1] if len(yc) else np.nan,
                     "latest_full_cohort": int(yc["cohort"].iloc[-1]) if len(yc) else None,
                     "repeat_90d_latest_quarter": er.loc[c].iloc[-1] if c in er.index else np.nan,
                     f"value_{W}m": window.get(c, np.nan), f"value_{H}m": value.get(c, np.nan),
                     "cac_last_3m": cac, "next_customer_cost": cost0.get(c, np.nan),
                     "payback_month": M.payback_month(curves.get(c), cac) if c in cac_l3m.index else "no spend line"})
    sc = pd.DataFrame(rows)
    reason0 = nxt.set_index("channel")["reason"]

    def verdict(r):
        why = reason0.get(r["channel"], "")
        if pd.isna(r["cac_last_3m"]):
            return "No spend line (organic, direct or unknown)"
        if why == "low_confidence":
            return "Held: response to spend can't be measured reliably"
        if why in ("no_curve", "no_value", "no_spend_last_year") or pd.isna(r["next_customer_cost"]):
            return "Held: not enough history"
        if r["next_customer_cost"] > r[f"value_{W}m"]:
            return f"Next customer doesn't pay back within {W} months"
        return f"Pays back within {W} months"

    sc["verdict"] = [verdict(r) for _, r in sc.iterrows()]
    out["scorecard"] = sc.sort_values("share_of_new", ascending=False).reset_index(drop=True)

    # ---- sensitivity: how the 12-month impact moves when one assumption changes
    if with_sensitivity:
        base_dir = _direction(t)
        sens = [("Base case", total, True)]

        def scenario(label, b_scale=1.0, customer_scale=1.0, values=None, **over):
            s2 = copy.deepcopy(s)
            s2.update(over)
            lg = Log()
            if values is not None:
                val2, win2 = values
            elif any(k in over for k in AFFECT_VALUE):
                P2 = M.with_settings(P, s2) if any(k in over for k in AFFECT_ROWS) else P
                _, _, _, val2, win2, _, _ = _values(P2, s2, lg)
            else:
                val2, win2 = value, window
            t2, _ = R.plan(P, s2, new, realloc, rc, val2, win2, lg, b_scale=b_scale, customer_scale=customer_scale)
            sens.append((label, float(t2["contribution_change"].sum()), _direction(t2) == base_dir))

        gm, fc, g = s["gross_margin"], s["fulfillment_cost_per_order"], s["max_change_vs_last_year"]
        scenario("Returned goods are a total loss" if s["returns_resold"] else "Returned goods are resold",
                 returns_resold=not s["returns_resold"])
        scenario(f"Gross margin {gm - 0.03:.0%} (3 points lower)", gross_margin=gm - 0.03)
        scenario(f"Gross margin {gm + 0.03:.0%} (3 points higher)", gross_margin=gm + 0.03)
        scenario(f"Fulfillment cost ${fc + 2:,.0f} per order ($2 more)", fulfillment_cost_per_order=fc + 2)
        for w_ in (6, 24):
            if w_ != W:
                scenario(f"Payback window {w_} months", payback_window_months=w_)
        for g_ in (0.10, 0.50):
            if abs(g_ - g) > 1e-9:
                scenario(f"Change limit {g_:.0%} vs the same month last year", max_change_vs_last_year=g_)
        if s["max_above_record_month"] is not None:
            scenario("No record-month cap", max_above_record_month=None)
        if s["max_above_record_month"] != 0:
            scenario("Never above the record month", max_above_record_month=0.0)
        scenario("Low-confidence channels moved like the others" if s["hold_low_confidence_channels"]
                 else "Low-confidence channels held", hold_low_confidence_channels=not s["hold_low_confidence_channels"])
        scenario("Spend response 20% weaker", b_scale=0.8)
        scenario("Spend response 20% stronger", b_scale=1.2)
        ranges = [x for x in v["latest_full_cohorts"] if x != "none yet"]
        if ranges:
            scenario(f"Value per customer as observed for customers acquired {ranges[0]} (full {H} months)",
                     values=(ref_value, ref_window))
        paid = list(realloc.columns)
        u, p = int((last12["channel"] == "Unattributed").sum()), int(last12["channel"].isin(paid).sum())
        if u and p:
            scenario(f"Unattributed customers shared across paid channels (+{u / p:.0%} customers)",
                     customer_scale=1 + u / p)
        st = pd.DataFrame(sens, columns=["scenario", "impact", "same_direction"])
        st["change_vs_base"] = st["impact"] - total
        out["sensitivity"] = st
    return out
