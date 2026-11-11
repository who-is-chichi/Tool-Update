"""Step 2: the numbers the brief asks for, by channel and cohort.

Months are integers (year*12 + month - 1), so month arithmetic is subtraction: the 'month index'.
k = months since a customer's first order (k = 0 is the month of the first order).
The data is a random sample of customers (settings: sample_rate). Rates and values per customer need no scaling;
customer counts and totals are divided by sample_rate to describe the whole business.
"""
import re

import numpy as np
import pandas as pd

from . import constants as C
from .common import ToolError


def mi(dates):
    """Month index of a date column."""
    return dates.dt.year * 12 + dates.dt.month - 1


def mi_label(m):
    return f"{m // 12}-{m % 12 + 1:02d}"


def month_end(m):
    return pd.Timestamp(year=m // 12, month=m % 12 + 1, day=1) + pd.offsets.MonthEnd(0)


def mi_name(m, fmt="%b %Y"):
    """Month index -> 'Nov 2026'."""
    return month_end(m).strftime(fmt)


# ---------------------------------------------------------------- customers and contribution
def contribution(rows, s):
    """Contribution of each order (positive) and refund (negative) row. Shipping charged is left out because the
    fulfilment cost is already net of it."""
    is_order = rows["order_type"] == "order"
    merch = rows["subtotal_usd"] - rows["discount_usd"]
    refunded = -rows["order_total_usd"]                      # positive amount refunded
    loss_rate = s["gross_margin"] if s["returns_resold"] else 1.0
    return np.where(
        is_order,
        s["gross_margin"] * merch - s["fulfillment_cost_per_order"] - s["payment_processing_rate"] * rows["order_total_usd"],
        -(refunded * loss_rate) - s["return_handling_cost_per_refund"])


def with_settings(P, s):
    """The same prepared data with contribution recomputed for other assumptions (used by the sensitivity table)."""
    P2 = dict(P)
    P2["rows"] = P["rows"].assign(cm=contribution(P["rows"], s))
    return P2


def prepare(d, s, log):
    orders, cust_raw, spend = d["orders"], d["customers"], d["spend"]
    if orders.empty or spend.empty:
        raise ToolError("After cleaning, orders.csv or marketing_spend.csv has no usable rows. Check the export.")
    spend_m = spend["month"].map(lambda p: p.year * 12 + p.month - 1)
    order_m = mi(orders["date"])
    as_of = int(min(order_m.max(), spend_m.max()))          # last month that ALL files cover
    if order_m.max() != spend_m.max():
        log.warn(f"Orders run to {mi_label(int(order_m.max()))} and spend runs to {mi_label(int(spend_m.max()))}. "
                 f"Using {mi_label(as_of)}, the last month both cover.")
    late = order_m > as_of
    if late.any():
        log.rule("orders.csv", "Orders after the as-of month", late.sum(), "excluded this run", "No spend data for that month yet")
    late_spend = spend_m > as_of
    if late_spend.any():
        log.rule("marketing_spend.csv", "Spend after the as-of month", late_spend.sum(), "excluded this run",
                 "No orders for that month yet")
        spend, spend_m = spend[~late_spend], spend_m[~late_spend]
    orders = orders[~late].copy()
    orders["m"] = mi(orders["date"])
    data_start = orders["date"].min()

    real = orders[orders["order_type"] == "order"]
    first = real.groupby("customer_id")["date"].min().rename("start")
    cust = first.to_frame().join(cust_raw.set_index("customer_id"), how="left")

    no_record = cust["channel"].isna()
    cust["has_record"] = ~no_record
    log.rule("customers.csv", "Ordering customers with no customer record", no_record.sum(),
             "kept, channel = Unattributed", "They bought, so they count as new customers; we just don't know the channel")
    cust["channel"] = cust["channel"].fillna("Unattributed")

    before = cust["acquisition_date"] < data_start
    log.rule("customers.csv", "Customers acquired before the order data starts", before.sum(),
             "left out of cohorts and new-customer counts",
             f"Acquired before {data_start:%Y-%m-%d}; their earlier orders are not in the file, so they are not new")
    cust = cust[~before]

    gap = (cust["acquisition_date"] - cust["start"]).dt.days
    log.rule("customers.csv", "Acquisition date later than first order", (gap > 30).sum(),
             "first order date used as the customer's start", "Record-created date is unreliable; a purchase date is not")
    future = cust["acquisition_date"] > orders["date"].max()
    log.rule("customers.csv", "Acquisition date in the future", future.sum(),
             "first order date used as the customer's start", "Dates after the end of the export cannot be right")

    cust["start_m"] = mi(cust["start"])
    cust["cohort"] = cust["start"].dt.year

    rows = orders[orders["customer_id"].isin(cust.index)].copy()
    rows["cm"] = contribution(rows, s)
    rows = rows.join(cust[["start", "start_m", "channel", "cohort"]], on="customer_id")
    rows["k"] = rows["m"] - rows["start_m"]
    return {"orders": orders, "rows": rows, "cust": cust, "spend": spend, "spend_m": spend_m,
            "as_of": as_of, "as_of_end": month_end(as_of), "data_start": data_start}


def cut_data(d, m):
    """The cleaned data as it stood at the end of month m (for checks against figures taken at that date)."""
    sp = d["spend"]
    spm = sp["month"].map(lambda p: p.year * 12 + p.month - 1)
    return {"orders": d["orders"][d["orders"]["date"] <= month_end(m)], "customers": d["customers"],
            "spend": sp[spm <= m], "notes": d["notes"]}


# ---------------------------------------------------------------- repeat rates
def repeat_rates(P, s):
    """Brief's definition: share of a cohort with at least one more order in months 1-12, 13-24, 25-36 after the
    first order, counting only customers whose full period has passed. Customers = whole business (sample / rate);
    plus_minus = 95% range from the sample."""
    cust, rows, end = P["cust"], P["rows"], P["as_of_end"]
    orders = rows[rows["order_type"] == "order"][["customer_id", "date"]].join(cust["start"], on="customer_id")
    out = []
    for n in range(1, C.YEARS_OF_REPEAT + 1):
        hi = cust["start"] + pd.DateOffset(months=12 * n)
        eligible = hi <= end
        o_lo = orders["start"] + pd.DateOffset(months=12 * (n - 1))
        o_hi = orders["start"] + pd.DateOffset(months=12 * n)
        hit = orders[(orders["date"] > o_lo) & (orders["date"] <= o_hi)]["customer_id"].unique()
        c = cust[eligible].assign(rep=lambda x: x.index.isin(hit))
        for ch, g in [("All", c)] + list(c.groupby("channel")):
            t = g.groupby("cohort")["rep"].agg(["size", "mean"]).reset_index()
            t["channel"], t["year"] = ch, n
            out.append(t)
    r = pd.concat(out).rename(columns={"size": "customers_in_sample", "mean": "repeat_rate"})
    r["customers"] = r["customers_in_sample"] / s["sample_rate"]
    r["plus_minus"] = 1.96 * np.sqrt(r["repeat_rate"] * (1 - r["repeat_rate"]) / r["customers_in_sample"])
    return r[["channel", "cohort", "year", "customers", "customers_in_sample", "repeat_rate", "plus_minus"]].reset_index(drop=True)


def early_read(P, days=90, quarters=16):
    """Share buying again within 90 days, by quarter of first order: a signal for cohorts too young for Year 1.
    Only quarters whose customers have all had the 90 days."""
    cust, rows, end = P["cust"], P["rows"], P["as_of_end"]
    orders = rows[rows["order_type"] == "order"][["customer_id", "date", "start"]]
    hit = orders[(orders["date"] > orders["start"]) &
                 (orders["date"] <= orders["start"] + pd.Timedelta(days=days))]["customer_id"].unique()
    q = cust["start"].dt.to_period("Q")
    complete = q.dt.end_time.dt.normalize() + pd.Timedelta(days=days) <= end
    c = cust[complete].assign(rep=lambda x: x.index.isin(hit), quarter=q[complete].astype(str))
    t = c.pivot_table(index="channel", columns="quarter", values="rep", aggfunc="mean")
    t.loc["All"] = c.groupby("quarter")["rep"].mean()
    return t[t.columns[-quarters:]]


def repeat_decomposition(rep):
    """Why did repeat change between cohorts? The change in the all-customer rate = channel mix (more customers from
    low-repeat channels) + within-channel change (each channel repeating less). Averages of the two cohorts' shares and
    rates are used, so the two parts add up exactly; one row per channel shows where each part comes from."""
    r = rep[rep["channel"] != "All"]
    out = []
    for year in sorted(r["year"].unique()):
        ry = r[r["year"] == year]
        cohorts = sorted(ry["cohort"].unique())
        pairs = list(zip(cohorts, cohorts[1:])) + ([(cohorts[0], cohorts[-1])] if len(cohorts) > 2 else [])
        for a, b in pairs:
            A, B = ry[ry["cohort"] == a].set_index("channel"), ry[ry["cohort"] == b].set_index("channel")
            ch = A.index.union(B.index)
            sa = A["customers_in_sample"].reindex(ch).fillna(0)
            sb = B["customers_in_sample"].reindex(ch).fillna(0)
            sa, sb = sa / sa.sum(), sb / sb.sum()
            ra, rb = A["repeat_rate"].reindex(ch), B["repeat_rate"].reindex(ch)
            ra, rb = ra.fillna(rb), rb.fillna(ra)
            mix = (sb - sa) * (ra + rb) / 2
            within = (rb - ra) * (sa + sb) / 2
            base = {"year": int(year), "from_cohort": int(a), "to_cohort": int(b)}
            out.append({**base, "channel": "All", "share_from": 1.0, "share_to": 1.0,
                        "rate_from": float((sa * ra).sum()), "rate_to": float((sb * rb).sum()),
                        "change": float((sb * rb).sum() - (sa * ra).sum()),
                        "from_mix": float(mix.sum()), "from_within_channel": float(within.sum())})
            for c in ch:
                out.append({**base, "channel": c, "share_from": sa[c], "share_to": sb[c], "rate_from": ra[c],
                            "rate_to": rb[c], "change": mix[c] + within[c], "from_mix": mix[c],
                            "from_within_channel": within[c]})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- value per customer (LTV)
def cohort_cum(P, max_k):
    """For each channel and start month: customers, and cumulative contribution per customer by month k."""
    rows, cust = P["rows"], P["cust"]
    tot = rows[rows["k"] < max_k].groupby(["channel", "start_m", "k"])["cm"].sum()
    size = cust.groupby(["channel", "start_m"]).size()
    grid = tot.unstack("k").reindex(columns=range(max_k), fill_value=0).fillna(0)
    grid = grid.reindex(size.index, fill_value=0)
    cum = grid.div(size, axis=0).cumsum(axis=1)            # per customer, cumulative
    return cum, size


def _pool(cum, size):
    """All channels together: cumulative contribution per customer by start month and age."""
    tot = cum.mul(size, axis=0).groupby(level="start_m").sum()
    sz = size.groupby(level="start_m").sum()
    return tot.div(sz, axis=0), sz


def _factors(cs, sz, as_of, n_ages, R, fallback=None):
    """Chain-ladder growth factors: f[k] = total cumulative contribution at age k+1 / at age k, over the latest R start
    months that have reached age k+1 by month as_of (so only data available then is used). An age with fewer than
    MIN_CUSTOMERS_PER_AGE sampled customers, or nothing to divide by, uses the fallback (all customers) instead.
    Factors below 1 are allowed: in some months refunds outweigh new orders."""
    tot = cs.mul(sz, axis=0)
    f = np.full(n_ages - 1, np.nan)
    borrowed = []
    months = sorted(sz.index)
    for k in range(n_ages - 1):
        ms = [m for m in months if m + k + 1 <= as_of][-R:]
        den = float(tot.loc[ms, k].sum()) if ms else 0.0
        if ms and sz.loc[ms].sum() >= C.MIN_CUSTOMERS_PER_AGE and den > 0:
            f[k] = float(tot.loc[ms, k + 1].sum()) / den
        elif fallback is not None:
            f[k] = fallback[k]
            borrowed.append(k)
    return f, borrowed


def _project(cs, sz, f, months, as_of, horizon):
    """Customer-weighted value at `horizon` months: each start month's observed cumulative at its age, grown with f."""
    w = sz.loc[months]
    vals = [cs.loc[m].iloc[min(as_of - m, horizon - 1)] * np.prod(f[min(as_of - m, horizon - 1):horizon - 1])
            for m in months]
    return float(np.dot(vals, w) / w.sum())


def value_curves(P, s, log, horizon=None):
    """Today's value of a new customer per channel: the H-month contribution of customers acquired in the last
    recent_cohort_months, each projected from what they have earned so far with chain-ladder growth factors.
    Also returns the month-by-month curve (used for payback) and the observed value of the latest customers who
    already have the full H months."""
    H = horizon or s["ltv_horizon_months"]
    W = s["payback_window_months"]
    R, N = s["reference_cohort_months"], s["recent_cohort_months"]
    as_of = P["as_of"]
    n_ages = max(H, W, 36)
    cum, size = cohort_cum(P, n_ages)
    cum_all, size_all = _pool(cum, size)
    f_all, _ = _factors(cum_all, size_all, as_of, n_ages, R)
    out, curves, factors = [], {}, {"All": f_all}
    for ch in sorted(size.index.get_level_values(0).unique()):
        cs, sz = cum.loc[ch], size.loc[ch]
        f, borrowed = _factors(cs, sz, as_of, n_ages, R, f_all)
        recent = [m for m in sz.index if as_of - N < m <= as_of]
        if not recent or np.isnan(f[:H - 1]).any():
            log.warn(f"{ch}: not enough history to project a {H}-month value per customer.")
            continue
        early = [k for k in borrowed if k < H - 1]
        if early:
            log.warn(f"{ch}: few customers, so its growth uses all customers' pattern at {len(early)} of {H - 1} "
                     "months of age.")
        value = _project(cs, sz, f, recent, as_of, H)
        w = sz.loc[recent]
        k0 = float((cs.loc[recent].iloc[:, 0] * w).sum() / w.sum())
        curve = pd.Series(np.concatenate([[k0], k0 * np.cumprod(np.nan_to_num(f, nan=1.0))]))
        if curve.iloc[H - 1] > 0:
            curve = curve * (value / curve.iloc[H - 1])
        curves[ch], factors[ch] = curve, f
        full = sorted(m for m in sz.index if m + H - 1 <= as_of)[-R:]
        ref = float((cs.loc[full].iloc[:, H - 1] * sz.loc[full]).sum() / sz.loc[full].sum()) if full else np.nan
        out.append({"channel": ch, f"value_{H}m_today": value, f"value_{H}m_latest_full_cohorts": ref,
                    "latest_full_cohorts": f"{mi_label(full[0])} to {mi_label(full[-1])}" if full else "none yet",
                    "today_customers_from": mi_label(min(recent)),
                    "today_vs_latest_full": value / ref if ref and ref == ref else np.nan})
    cols = ["channel", f"value_{H}m_today", f"value_{H}m_latest_full_cohorts", "latest_full_cohorts",
            "today_customers_from", "today_vs_latest_full"]
    return pd.DataFrame(out, columns=cols).set_index("channel"), curves, factors


def cohort_ltv(P, s, factors, horizons=(12, 24, 36)):
    """LTV on contribution margin per customer, by channel and cohort year.
    Observed: average over customers whose 12/24/36 months have passed (same rule as repeat rates), with a 95% range.
    Projected H-month value: each start month's observed value at its age, grown with the chain-ladder factors."""
    rows, cust, as_of = P["rows"], P["cust"], P["as_of"]
    H = s["ltv_horizon_months"]
    base = cust[["channel", "cohort", "start_m"]].copy()
    for h in horizons:
        per = rows.loc[rows["k"] < h].groupby("customer_id")["cm"].sum()
        base[f"v{h}"] = per.reindex(base.index).fillna(0.0).to_numpy()
        base[f"e{h}"] = (base["start_m"] + h - 1 <= as_of).to_numpy()
    cum, size = cohort_cum(P, max(H, 2))
    cum_all, size_all = _pool(cum, size)
    out = []
    for ch, g in [("All", base)] + list(base.groupby("channel")):
        f = factors.get(ch)
        cs, sz = (cum_all, size_all) if ch == "All" else (cum.loc[ch], size.loc[ch])
        for y, gy in g.groupby("cohort"):
            r = {"channel": ch, "cohort": int(y), "customers": len(gy) / s["sample_rate"], "customers_in_sample": len(gy)}
            for h in horizons:
                e = gy.loc[gy[f"e{h}"], f"v{h}"]
                n = len(e)
                r[f"ltv_{h}m"] = e.mean() if n else np.nan
                r[f"ltv_{h}m_plus_minus"] = 1.96 * e.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
                r[f"ltv_{h}m_customers_in_sample"] = n
            ms = [m for m in sz.index if m // 12 == y]
            if f is None or not ms:
                r[f"ltv_{H}m_projected"], r["projection"] = np.nan, "not enough data"
            else:
                r[f"ltv_{H}m_projected"] = _project(cs, sz, f, ms, as_of, H)
                full = sum(as_of - m >= H - 1 for m in ms)
                r["projection"] = "observed" if full == len(ms) else ("projected" if full == 0 else "partly projected")
            out.append(r)
    return pd.DataFrame(out)


def backtest(P, s):
    """Would the projection have worked? Rebuild it with only the data available at an earlier date and compare with
    what actually happened. A: the latest customers with the full horizon, projected from their first 3 and 12 months.
    B: the latest customers with 12 months observed, projected from their first 3 months (recent behaviour)."""
    H, R = s["ltv_horizon_months"], s["reference_cohort_months"]
    as_of = P["as_of"]
    n_ages = max(H, 12)
    cum, size = cohort_cum(P, n_ages)
    cum_all, size_all = _pool(cum, size)
    out = []

    def test(label, cs, sz, targets, then, horizon, from_k, ch):
        f_all, _ = _factors(cum_all, size_all, then, n_ages, R)
        f, _ = _factors(cs, sz, then, n_ages, R, f_all)
        if np.isnan(f[:horizon - 1]).any():
            return
        pred = _project(cs, sz, f, targets, then, horizon)
        w = sz.loc[targets]
        actual = float((cs.loc[targets].iloc[:, horizon - 1] * w).sum() / w.sum())
        out.append({"test": label, "channel": ch, "projected_from_months": from_k, "horizon_months": horizon,
                    "predicted": pred, "actual": actual, "error": pred / actual - 1 if actual else np.nan})

    for ch in sorted(size.index.get_level_values(0).unique()):
        cs, sz = cum.loc[ch], size.loc[ch]
        full = sorted(m for m in sz.index if m + H - 1 <= as_of)[-R:]
        for k in (3, 12):
            if full:
                test(f"A: full {H} months, customers from {mi_label(full[0])} to {mi_label(full[-1])}",
                     cs, sz, full, max(full) + k - 1, H, k, ch)
        recent = sorted(m for m in sz.index if m + 11 <= as_of)[-C.BACKTEST_RECENT_COHORTS:]
        if recent:
            test(f"B: first 12 months, customers from {mi_label(recent[0])} to {mi_label(recent[-1])}",
                 cs, sz, recent, max(recent) + 2, 12, 3, ch)
    return pd.DataFrame(out, columns=["test", "channel", "projected_from_months", "horizon_months", "predicted",
                                      "actual", "error"])


# ---------------------------------------------------------------- CAC and payback
def monthly_new_and_spend(P, s):
    """New customers (scaled to the whole business) and reallocatable spend, by month and channel."""
    new = P["cust"].groupby(["start_m", "channel"]).size().unstack(fill_value=0) / s["sample_rate"]
    sp = P["spend"].assign(m=P["spend_m"])
    realloc = sp[sp["treatment"] == "reallocate"].pivot_table(index="m", columns="channel", values="spend_usd",
                                                               aggfunc="sum").fillna(0)
    return new, realloc, sp


def cac_tables(P, s):
    new, realloc, sp = monthly_new_and_spend(P, s)
    as_of = P["as_of"]
    paid = list(realloc.columns)
    year = lambda idx: [m // 12 for m in idx]
    by_year_spend = realloc.groupby(year(realloc.index)).sum()
    by_year_new = new.groupby(year(new.index)).sum()
    cac_year = (by_year_spend / by_year_new.reindex(columns=paid)).T
    last3 = [as_of - 2, as_of - 1, as_of]
    cac_l3m = realloc.reindex(last3).fillna(0).sum() / new.reindex(index=last3, columns=paid).fillna(0).sum()
    sp = sp.assign(y=sp["m"] // 12)
    nonret = sp[sp["treatment"] != "retention"].groupby("y")["spend_usd"].sum()
    blended = (nonret / new.sum(axis=1).groupby(year(new.index)).sum()).rename("blended_cac")
    return cac_year, cac_l3m.rename("cac_last_3_months"), blended, new, realloc


def payback_month(curve, cac, cap=36):
    """First month k at which cumulative contribution covers CAC (0 = on the first order)."""
    if pd.isna(cac) or curve is None:
        return "n/a"
    hit = [k for k, v in enumerate(curve.values[:cap]) if v >= cac]
    if not hit:
        n = min(cap, len(curve))
        return f"not within {n} month{'s' if n != 1 else ''}"
    return "first order" if hit[0] == 0 else f"month {hit[0]}"


def payback_by_cohort(P, cac_year):
    """Months until a cohort's average contribution covers that year's CAC. At each month of age the average uses the
    customers who have reached it, so the older members of a young cohort count."""
    cum, size = cohort_cum(P, 36)
    as_of = P["as_of"]
    chans = set(size.index.get_level_values(0))
    out = []
    for ch in cac_year.index:
        if ch not in chans:
            continue
        cs, sz = cum.loc[ch], size.loc[ch]
        for y in cac_year.columns:
            ms = [m for m in sz.index if m // 12 == y]
            cac = cac_year.loc[ch, y]
            if not ms or pd.isna(cac):
                continue
            ages = min(as_of - min(ms), 35)
            curve = []
            for k in range(ages + 1):
                el = [m for m in ms if m + k <= as_of]
                curve.append(float((cs.loc[el, k] * sz.loc[el]).sum() / sz.loc[el].sum()))
            hit = next((k for k, v in enumerate(curve) if v >= cac), None)
            label = (("first order" if hit == 0 else f"month {hit}") if hit is not None
                     else f"not yet ({ages + 1} month{'s' if ages else ''} observed)")
            out.append({"channel": ch, "cohort": int(y), "cac": cac, "payback_month": label, "months_observed": ages + 1})
    return pd.DataFrame(out, columns=["channel", "cohort", "cac", "payback_month", "months_observed"])


# ---------------------------------------------------------------- checks against finance
def reconciliation(P, s, rep, blended):
    """The tool's figures next to finance's: one row per figure in settings.xlsx > finance_reference."""
    fin = s["finance"]
    allr = rep[rep["channel"] == "All"].set_index(["year", "cohort"])["repeat_rate"]
    o = P["orders"]
    last12 = o["date"] > o["date"].max() - pd.DateOffset(months=12)
    real = o["order_type"] == "order"
    aov = o[last12 & real]["order_total_usd"].mean()
    rev = o[last12]["order_total_usd"].sum() / s["sample_rate"]
    sp = P["spend"].assign(m=P["spend_m"])
    spend_ltm = sp[sp["m"] > P["as_of"] - 12]["spend_usd"].sum()
    rows = []
    for key, fv in fin.items():
        rep_key = re.fullmatch(r"repeat_y(\d+)_(\d{4})", key)
        cac_key = re.fullmatch(r"blended_cac_(\d{4})", key)
        if rep_key:
            yr, coh = int(rep_key.group(1)), int(rep_key.group(2))
            tv = allr.get((yr, coh), np.nan)
            rows.append({"metric": f"Year {yr} repeat, {coh} customers", "tool": tv, "finance": fv, "kind": "rate",
                         "note": "" if tv == tv else "Not enough months of data for this cohort and year."})
        elif cac_key:
            y = int(cac_key.group(1))
            tv = blended.get(y, np.nan)
            rows.append({"metric": f"Blended CAC {y}", "tool": tv, "finance": fv, "kind": "money",
                         "note": "" if tv == tv else "No data for this year."})
        elif key == "aov":
            rows.append({"metric": "Average order value, last 12 months", "tool": aov, "finance": fv, "kind": "money", "note": ""})
        elif key == "dtc_revenue_ltm":
            rows.append({"metric": "DTC revenue, last 12 months, net of refunds (sample / sample rate)", "tool": rev,
                         "finance": fv, "kind": "money", "note": ""})
        elif key == "marketing_share_of_revenue":
            total, dtc = fin.get("revenue_ltm_total"), fin.get("dtc_revenue_ltm")
            tv = spend_ltm / total if total else np.nan
            note = (f"The spend file holds ${spend_ltm / 1e6:,.1f}M over the last 12 months"
                    + (f": {tv:.0%} of total revenue" if total else "")
                    + (f" and {spend_ltm / dtc:.0%} of DTC revenue" if dtc else "")
                    + ". It covers DTC marketing only and excludes wholesale trade marketing and co-op (data "
                      "dictionary), so it can't tie to a company-wide figure.")
            rows.append({"metric": "Marketing spend as a share of revenue, last 12 months", "tool": tv, "finance": fv,
                         "kind": "explained", "note": note})
        elif key == "revenue_ltm_total":
            continue                                        # used by the line above
        else:
            rows.append({"metric": key, "tool": np.nan, "finance": fv, "kind": "unknown",
                         "note": "The tool doesn't compute this figure; check its name in settings.xlsx."})
    t = pd.DataFrame(rows, columns=["metric", "tool", "finance", "kind", "note"])
    t["difference"] = t["tool"] - t["finance"]

    def status(r):
        if r.kind == "explained":
            return "definition differs"
        if pd.isna(r.tool):
            return "can't compute"
        if r.kind == "rate":
            return "match" if abs(r.difference) <= C.CHECK_TOLERANCE_REPEAT else "check"
        return "match" if r.finance and abs(r.difference / r.finance) <= C.CHECK_TOLERANCE_OTHER else "check"

    t["status"] = [status(r) for r in t.itertuples()]
    return t[["metric", "tool", "finance", "difference", "status", "kind", "note"]]


# ---------------------------------------------------------------- customer-service notes
def classify_notes(text, themes):
    """First theme whose keywords appear in the note (case ignored); 'Other' if none."""
    low = text.astype("string").str.lower().fillna("")
    theme = pd.Series("Other", index=text.index, dtype=object)
    free = pd.Series(True, index=text.index)
    for name, words, _ in themes:
        if not words:
            continue
        hit = low.str.contains("|".join(re.escape(w.lower()) for w in words), regex=True).fillna(False).astype(bool)
        hit &= free
        theme[hit] = name
        free &= ~hit
    return theme


def service_issues(notes, P, themes):
    """Customer-service notes by theme: per 1,000 orders by complete quarter (sample notes / sample orders, so no
    scaling), the latest quarter against a year earlier, and per 1,000 orders by acquisition channel."""
    n = notes[notes["note_date"].notna()].copy()
    o = P["orders"]
    real = o[o["order_type"] == "order"]
    q_orders = real.groupby(real["date"].dt.to_period("Q")).size()
    complete = [q for q in q_orders.index if q.end_time.normalize() <= P["as_of_end"].normalize()]
    if n.empty or not complete:
        return None
    n["theme"] = classify_notes(n["note_text"], themes)
    problem = {t: p for t, _, p in themes}
    n["quarter"] = n["note_date"].dt.to_period("Q")
    counts = (n[n["quarter"].isin(complete)].groupby(["theme", "quarter"]).size().unstack("quarter")
              .reindex(columns=complete).fillna(0))
    per1000 = counts.div(q_orders[complete], axis=1) * 1000
    last, prev = complete[-1], complete[-1] - 4
    summary = pd.DataFrame({
        "theme": per1000.index,
        "service_problem": ["yes" if problem.get(t, False) else "no" for t in per1000.index],
        "notes_latest_quarter_in_sample": counts[last].astype(int).to_numpy(),
        "per_1000_orders_latest_quarter": per1000[last].to_numpy(),
        "per_1000_orders_year_earlier": per1000[prev].to_numpy() if prev in per1000.columns else np.nan})
    summary["change"] = summary["per_1000_orders_latest_quarter"] / summary["per_1000_orders_year_earlier"] - 1
    summary = summary.sort_values(["service_problem", "per_1000_orders_latest_quarter"], ascending=[False, False])
    per1000.columns = [str(q) for q in per1000.columns]

    window = complete[-4:]
    chan = P["cust"]["channel"]
    other = "Acquired before the data, or no order"
    n4 = n[n["quarter"].isin(window)]
    n4 = n4.assign(channel=n4["customer_id"].map(chan).fillna(other))
    r4 = real[real["date"].dt.to_period("Q").isin(window)]
    r4 = r4.assign(channel=r4["customer_id"].map(chan).fillna(other))
    by_ch = n4.groupby(["theme", "channel"]).size().unstack("channel").fillna(0)
    by_ch = by_ch.div(r4.groupby("channel").size().reindex(by_ch.columns), axis=1) * 1000
    return {"summary": summary.reset_index(drop=True), "by_quarter": per1000, "by_channel": by_ch,
            "other_share": float((n["theme"] == "Other").mean()), "latest_quarter": str(last),
            "year_earlier_quarter": str(prev), "window": f"{window[0]} to {window[-1]}"}


# ---------------------------------------------------------------- checks on the newest month of data
def latest_month_checks(P, realloc, log):
    """Warnings that catch an incomplete or changed export in the newest month."""
    as_of, o, cust = P["as_of"], P["orders"], P["cust"]
    label = mi_label(as_of)
    last = o["date"].max()
    gap = (P["as_of_end"].normalize() - last.normalize()).days
    if gap > C.LATEST_MONTH_GAP_DAYS:
        log.warn(f"The latest month ({label}) looks incomplete: the last order is on {last:%d %b %Y}, {gap} days before "
                 f"the month ends. Check the export; {label} figures will be understated.")
    if as_of in realloc.index:
        before = realloc[realloc.index < as_of].tail(3)
        for c in realloc.columns:
            if realloc.loc[as_of, c] == 0 and before[c].sum() > 0:
                log.warn(f"{c} has no spend in {label} after spending in earlier months. If its spend line was renamed, "
                         "add the new name to settings.xlsx > spend_mapping.")
    no_rec = 1 - cust.groupby("start_m")["has_record"].mean()
    usual = no_rec[(no_rec.index >= as_of - 12) & (no_rec.index < as_of)].mean()
    latest = no_rec.get(as_of, np.nan)
    if latest == latest and usual == usual and latest > max(C.NO_RECORD_SPIKE * usual, C.NO_RECORD_MIN_SHARE):
        log.warn(f"{latest:.0%} of {label}'s new customers have no record in customers.csv (usually {usual:.0%}). "
                 "customers.csv may be missing recent customers; they show as Unattributed.")
    real = P["rows"][P["rows"]["order_type"] == "order"]
    active = real[(real["k"] >= 12) & (real["k"] <= 23)].groupby("m")["customer_id"].nunique()
    starts = cust.groupby("start_m").size()

    def share(m):
        n = starts[(starts.index >= m - 23) & (starts.index <= m - 12)].sum()
        return active.get(m, 0) / n if n else np.nan

    ratio = pd.Series({m: share(m) / share(m - 12) if share(m - 12) else np.nan
                       for m in range(as_of - C.CONTINUITY_LOOKBACK_MONTHS, as_of + 1)})
    if ratio.drop(as_of).notna().sum() >= 4 and ratio[as_of] == ratio[as_of]:
        trend = ratio.drop(as_of).median()
        if ratio[as_of] < (1 - C.CONTINUITY_DROP_ALERT) * trend:
            log.warn(f"Existing customers ordered much less in {label} than their recent trend ({ratio[as_of]:.0%} of a "
                     f"year earlier, against about {trend:.0%} in the months before). Customer IDs may have changed in "
                     "the export, for example after a new order system; check before relying on repeat rates.")
