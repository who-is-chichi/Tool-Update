"""Step 4: write everything to one Excel file (and the cleaning log as CSV)."""
import io
from pathlib import Path

import pandas as pd

from .metrics import mi_name
from .recommend import REASON_LABEL

TEAL = "#0F5C63"
FORMATS = {"money": "$#,##0", "money2": "$#,##0.00", "pct": "0.0%", "count": "#,##0", "dec": "0.00"}
MONEY = ("spend", "value", "cac", "contribution", "impact", "cost", "predicted", "actual", "budget", "ltv", "total")
PCT = ("rate", "share", "repeat", "pct", "error", "vs_last_month")
EXACT = {"b": "dec", "b_std_error": "dec", "change": "money", "change_vs_base": "money", "latest_full_cohort": None,
         "cohort": None, "year": None, "from_cohort": None, "to_cohort": None, "payback_month": None,
         "months_used": "count", "months_observed": "count", "projected_from_months": "count",
         "horizon_months": "count", "today_vs_latest_full": "dec", "value": None, "same_direction": None,
         "tool": None, "finance": None, "difference": None, "service_problem": None, "projection": None}


def _fmt_for(col):
    c = str(col).lower()
    if c in EXACT:
        return EXACT[c]
    if "customers" in c or "notes_" in c or c in ("rows affected",):
        return "count"
    if any(k in c for k in PCT):
        return "pct"
    if any(k in c for k in MONEY):
        return "money"
    return None


def _label(col):
    return str(col).replace("_", " ")


class _Book:
    """Small helper around the Excel writer: titled tables, number formats, several tables per sheet."""

    def __init__(self, writer):
        self.w, self.wb = writer, writer.book
        self.f_title = self.wb.add_format({"bold": True, "font_size": 14, "font_color": TEAL})
        self.f_sub = self.wb.add_format({"bold": True, "font_size": 11, "font_color": TEAL})
        self.f_note = self.wb.add_format({"italic": True, "font_color": "#555555"})
        self.f_head = self.wb.add_format({"bold": True, "bg_color": TEAL, "font_color": "#FFFFFF", "text_wrap": True,
                                          "valign": "top", "border": 1, "border_color": "#D0D7DE"})
        self.fmts = {k: self.wb.add_format({"num_format": v}) for k, v in FORMATS.items()}
        self.f_warn = self.wb.add_format({"bold": True, "font_color": "#B03A2E"})
        self.f_bold = self.wb.add_format({"bold": True})

    def table(self, df, sheet, title, note=None, index=False, formats=None, start=0, sub=False):
        """Write one titled table. formats: {column: 'money'|'pct'|'count'|'dec'|'money2'} overrides the guesses."""
        df = df.reset_index() if index else df.copy()
        head = start + (2 if note else 1)
        df.to_excel(self.w, sheet_name=sheet, startrow=head, index=False)
        ws = self.w.sheets[sheet]
        ws.write(start, 0, title, self.f_sub if sub else self.f_title)
        if note:
            ws.write(start + 1, 0, note, self.f_note)
        formats = formats or {}
        for j, col in enumerate(df.columns):
            ws.write(head, j, _label(col), self.f_head)
            kind = formats.get(col, _fmt_for(col))
            if kind:
                for i, val in enumerate(df[col].tolist()):
                    if isinstance(val, (int, float)) and pd.notna(val) and not isinstance(val, bool):
                        ws.write_number(head + 1 + i, j, float(val), self.fmts[kind])
            if start == 0:
                lengths = [len(str(x)) for x in df[col].tolist()][:200] or [10]
                ws.set_column(j, j, min(60, max(12, len(_label(col)) * 0.7 + 2, sorted(lengths)[int(0.9 * (len(lengths) - 1))] + 2)))
        if start == 0:
            ws.freeze_panes(head + 1, 1)
        return ws, head + len(df) + 3


def _money(x, sign=False):
    if x is None or pd.isna(x):
        return "-"
    return f"{'+' if sign and x > 0 else ''}{'-' if x < 0 else ''}${abs(x):,.0f}"


def guardrails(res):
    """What we chose and why: one line each (Cotopaxi asked for this)."""
    s, nm = res["settings"], res["next_month"]
    H, W = s["ltv_horizon_months"], s["payback_window_months"]
    g, r = s["max_change_vs_last_year"], s["max_above_record_month"]
    flat = res["next_month_held_flat"]
    held = ", ".join(nm.loc[nm["reason"] == "low_confidence", "channel"]) or "none this month"
    errs = res["backtest"]["error"].abs()
    lines = [
        ("Budget", f"Fixed at this year's level ({_money(res['budget'])} a year) and moved across channels, as Cotopaxi asked. "
                   "Each month gets this year's amount for that month, so seasonality is kept."),
        ("Brand and Email & SMS", f"Held flat ({', '.join(flat['channel']) or 'none'}: {_money(flat['spend'].sum())} next month). "
                                  "Outside the acquisition budget; the data can't show which customers they bring."),
        ("Payback target", f"{W} months. A channel gets more than this year's spend only while its next customer pays back "
                           f"within {W} months. Cotopaxi has no target; {W} months keeps cash recovery inside one budget year."),
        ("Change limit", f"Each channel stays between {1 - g:.0%} and {1 + g:.0%} of its spend in the same month this year; "
                         f"the {1 - g:.0%} floor is the channel minimum. Keeps the plan near spend levels the data has seen; "
                         "the planner reruns every month."),
        ("Record-month cap", (f"At most {r:.0%} above a channel's highest month in the data; beyond that the response "
                              "curve is a guess.") if r is not None else "Off: spend may go beyond any month in the data."),
        ("Uncertain response", ("Channels whose response to spend can't be measured reliably are held at this year's "
                                f"spend (this month: {held}).") if s["hold_low_confidence_channels"]
         else "Off: low-confidence channels are moved like the others."),
        ("Value of a customer", f"Contribution over each customer's first {H} months from acquisition (Cotopaxi's "
                                "definition). Recent customers are projected month by month from the latest customers "
                                f"who reached each age (chain-ladder); back-tested within {errs.max():.0%}." if len(errs)
                                else f"Contribution over each customer's first {H} months from acquisition."),
        ("Sample", f"Customers and orders are a {s['sample_rate']:.0%} sample; customer counts and totals are divided by "
                   f"{s['sample_rate']:g}, rates and values per customer are used as measured."),
    ]
    return pd.DataFrame(lines, columns=["choice", "what and why"])


def _build(res, target):
    s = res["settings"]
    H, W = s["ltv_horizon_months"], s["payback_window_months"]
    nm, rec = res["next_month"], res["reconciliation"]
    month = nm["month"].iloc[0]
    m1 = int(nm["m"].iloc[0])
    with pd.ExcelWriter(target, engine="xlsxwriter") as w:
        b = _Book(w)
        # ---- Summary
        d_cust = nm["recommended_customers"].sum() - nm["baseline_customers"].sum()
        d_val = nm["contribution_change"].sum()
        checks = rec[rec["kind"].isin(["rate", "money"])]
        cy = res["impact_by_calendar_year"]
        items = [("Data through", mi_name(m1 - 1, "%B %Y")),
                 ("Plan for", f"{mi_name(m1, '%B %Y')} and the next 12 months"),
                 ("Acquisition budget, next 12 months (this year's level)", _money(res["budget"])),
                 (f"Acquisition budget, {mi_name(m1)}", _money(nm["baseline_spend"].sum())),
                 (f"{mi_name(m1)}: change in new customers", f"{d_cust:+,.0f}"),
                 (f"{mi_name(m1)}: change in {H}-month contribution", _money(d_val, True)),
                 (f"Next 12 months on the tool's split vs this year's: change in the {H}-month contribution of the "
                  "customers gained and lost", _money(res["impact"], True))]
        items += [(f"   ... of which lands in {int(r.year)}", _money(r.total, True)) for r in cy.itertuples()]
        items += [(f"Checks against finance (as of {pd.Period(res['reconciliation_as_of'], freq='M').strftime('%b %Y')})",
                   f"{(checks['status'] == 'match').sum()} of {len(checks)} match"),
                  ("Warnings this run", str(len(res["log"].warnings)))]
        summary = pd.DataFrame(items, columns=["item", "value"])
        ws, row = b.table(summary, "Summary", "Cotopaxi DTC channel planner",
                          f"Plan for {mi_name(m1, '%B %Y')}. Same total budget; only the split changes.")
        ws.set_column(0, 0, 90)
        ws.set_column(1, 1, 22)
        _, row = b.table(guardrails(res), "Summary", "Guardrails we chose and why", start=row, sub=True)
        ws.write(row, 0, "Warnings", b.f_warn)
        for i, msg in enumerate(res["log"].warnings or ["None"]):
            ws.write(row + 1 + i, 0, msg)

        # ---- Next month
        cols = ["channel", "baseline_spend", "recommended_spend", "change", "change_pct", "last_month_spend",
                "vs_last_month", "baseline_customers", "recommended_customers", "next_customer_cost",
                "value_in_window", "value_per_customer", "reason", "why"]
        t = nm[cols].copy()
        t["reason"] = t["reason"].map(REASON_LABEL).fillna(t["reason"])
        flat = res["next_month_held_flat"]
        if len(flat):
            t = pd.concat([t, pd.DataFrame({"channel": flat["channel"], "baseline_spend": flat["spend"],
                                            "recommended_spend": flat["spend"], "change": 0.0, "change_pct": 0.0,
                                            "reason": REASON_LABEL["held_flat"], "why": flat["why"]})])
        num = t.select_dtypes("number").columns
        t[num] = t[num].round(2)
        t = pd.concat([t, pd.DataFrame([{"channel": "Total", "baseline_spend": round(t["baseline_spend"].sum(), 2),
                                         "recommended_spend": round(t["recommended_spend"].sum(), 2),
                                         "change": round(t["change"].sum(), 2) + 0.0}])])
        last = res["last_month"]
        t = t.rename(columns={"baseline_spend": f"this year's spend ({mi_name(m1 - 12)})",
                              "recommended_spend": "recommended spend", "last_month_spend": f"spend in {mi_name(m1 - 1)}",
                              "vs_last_month": f"change vs {mi_name(m1 - 1)}", "baseline_customers": "new customers, this year's split",
                              "recommended_customers": "new customers, recommended", "value_in_window": f"value in {W} months",
                              "value_per_customer": f"value over {H} months"})
        b.table(t, "Next month", f"Recommended spend for {mi_name(m1, '%B %Y')}",
                f"This year's spend = the same month last year, scaled to the budget. Same total; only the split changes. "
                f"Data through {last}.",
                formats={f"change vs {mi_name(m1 - 1)}": "pct", "change_pct": "pct", "next_customer_cost": "money",
                         f"value in {W} months": "money", f"value over {H} months": "money"})
        b.table(res["scorecard"], "Scorecard", "Channel scorecard",
                f"CAC = last 3 months. Next customer's cost = at this year's spend for {mi_name(m1)}. Value = contribution "
                "per customer (projected for recent customers). Payback month 0 = paid back on the first order.",
                formats={"next_customer_cost": "money", "latest_full_cohort": None})

        # ---- Repeat
        rep = res["repeat"]
        pv = rep.pivot_table(index="channel", columns=["cohort", "year"], values="repeat_rate")
        pv.columns = [f"{c} customers, year {y}" for c, y in pv.columns]
        ws, row = b.table(pv, "Repeat by cohort", "Repeat purchase rate by channel, cohort and year since first order",
                          "Share with at least one more order in months 1-12, 13-24, 25-36. Only customers whose full year "
                          "has passed.", index=True, formats={c: "pct" for c in pv.columns})
        b.table(rep, "Repeat by cohort", "Detail: customers and 95% range",
                f"customers = whole business (sample / {s['sample_rate']:g}); plus minus = 95% range from the sample.",
                start=row, sub=True, formats={"plus_minus": "pct", "repeat_rate": "pct"})
        dec = res["repeat_decomposition"]
        if len(dec):
            b.table(dec, "Why repeat changed", "Why repeat changed: channel mix vs within-channel",
                    "change = from mix (more customers from low-repeat channels) + from within channel (each channel "
                    "repeating less). Rates in points of repeat; the two parts add up exactly.",
                    formats={c: "pct" for c in ["share_from", "share_to", "rate_from", "rate_to", "change", "from_mix",
                                                "from_within_channel"]})
        b.table(res["early_read"], "90-day repeat", "Early read: share ordering again within 90 days, by quarter of first order",
                "Only quarters whose customers have all had 90 days.", index=True,
                formats={c: "pct" for c in res["early_read"].columns})

        # ---- Value and payback
        b.table(res["value"], "Customer value", f"Contribution per customer over {H} months",
                "Today = customers acquired in the last 12 months, projected month by month from the latest customers "
                f"who reached each age (chain-ladder). Latest full cohorts = observed, with the full {H} months.",
                index=True, formats={"today_vs_latest_full": "dec"})
        curves = pd.DataFrame({c: v.values[:max(H, W)] for c, v in res["value_curves"].items()})
        curves.index = [f"month {k}" for k in range(len(curves))]
        b.table(curves, "Value by month", "Cumulative contribution per customer by month since first order (today's customers)",
                index=True, formats={c: "money" for c in curves.columns})
        b.table(res["cohort_ltv"], "Cohort LTV", "LTV on contribution margin by channel and cohort",
                f"Observed: average over customers whose 12/24/36 months have passed, with a 95% range. Projected {H}-month "
                "LTV: each start month's value so far, grown with the chain-ladder factors.",
                formats={c: "money" for c in res["cohort_ltv"].columns if c.startswith("ltv_") and "customers" not in c})
        cy_ = res["cac_year"].copy()
        cy_.columns = [f"CAC {c}" for c in cy_.columns]
        cy_ = cy_.join(res["cac_l3m"].rename("CAC last 3 months"))
        ws, row = b.table(cy_, "CAC", "CAC by channel and year (spend / new customers scaled from the sample)", index=True,
                          formats={c: "money2" for c in cy_.columns})
        b.table(res["blended_cac"].rename("blended CAC").to_frame(), "CAC", "Blended CAC (all spend except retention / all new customers)",
                index=True, start=row, sub=True, formats={"blended CAC": "money2"})
        b.table(res["payback_cohort"], "Payback by cohort", "Months until contribution covers that year's CAC",
                "Month 0 = paid back on the first order. Each month of age uses the customers who have reached it.",
                formats={"cac": "money2"})

        # ---- Recommendation
        b.table(res["response"], "Response curves", "How new customers respond to spend, per channel",
                "b: 10% more spend buys about b x 10% more customers. Model chosen each run by one-month-ahead error over "
                "the last 12 months.", formats={"error_last_12m": "pct"})
        plan = res["plan"].drop(columns=["m"]).copy()
        plan["reason"] = plan["reason"].map(REASON_LABEL).fillna(plan["reason"])
        b.table(plan, "12-month plan", "This year's split vs recommended, month by month: input to the business-case workbook",
                f"Customers at another spend = this year's customers x (spend / this year's spend)^b. Contribution change = "
                f"customer change x {H}-month value per customer.",
                formats={"cost_at_this_years_spend": "money", "value_in_window": "money"})
        b.table(res["impact_by_channel"], "Impact by channel",
                f"Next 12 months: customers gained and lost, and their {H}-month contribution")
        b.table(res["impact_by_calendar_year"], "Impact by year",
                f"Where the {H}-month contribution of next year's customers lands, by calendar year",
                "Customers acquired in the next 12 months, each counted for their first months as their value builds up.",
                formats={c: "money" for c in res["impact_by_calendar_year"].columns if c != "year"})
        if "sensitivity" in res:
            b.table(res["sensitivity"], "Sensitivity", "How the 12-month impact moves when one assumption changes",
                    "same direction = every channel moves the same way next month as in the base case.")
        b.table(res["backtest"], "Back-test", "Would the projection have worked? Predicted vs actual value",
                "Each projection uses only the data available at the time.")

        # ---- Service issues
        si = res.get("service_issues")
        if si:
            ws, row = b.table(si["summary"], "Service issues", "Customer-service notes by theme, per 1,000 orders",
                              f"Latest complete quarter ({si['latest_quarter']}) vs a year earlier ({si['year_earlier_quarter']}). "
                              "Themes are keyword rules in settings.xlsx > note_themes.",
                              formats={"change": "pct", "per_1000_orders_latest_quarter": "dec",
                                       "per_1000_orders_year_earlier": "dec"})
            ws, row = b.table(si["by_quarter"], "Service issues", "By quarter (notes per 1,000 orders)", index=True,
                              start=row, sub=True, formats={c: "dec" for c in si["by_quarter"].columns})
            b.table(si["by_channel"], "Service issues", f"By acquisition channel, {si['window']} (notes per 1,000 orders)",
                    index=True, start=row, sub=True, formats={c: "dec" for c in si["by_channel"].columns})

        # ---- Checks and log
        recx = rec.drop(columns=["kind"])
        b.table(recx, "Reconciliation", f"The tool's figures against finance's (both as of {res['reconciliation_as_of']})",
                "Repeat rates match within 1 point, money figures within 3%. 'Definition differs' lines are explained, not "
                "pass/fail.")
        b.table(res["log"].table(), "Cleaning log", "What the tool changed in the data, and why")
        settings_rows = [(k, "; ".join(map(str, v)) if isinstance(v, list) and k == "date_formats" else v)
                         for k, v in s.items() if not isinstance(v, (dict, list, tuple)) or k == "date_formats"]
        b.table(pd.DataFrame(settings_rows, columns=["setting", "value"]).astype(str), "Settings used",
                "Assumptions used in this run")


def report_bytes(res):
    """The Excel report as bytes (for the app's download button; nothing written to disk)."""
    buf = io.BytesIO()
    _build(res, buf)
    return buf.getvalue()


def write_report(res, out_folder):
    out = Path(out_folder)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"channel_plan_{res['as_of']}.xlsx"
    _build(res, path)
    res["log"].table().to_csv(out / f"cleaning_log_{res['as_of']}.csv", index=False)
    return path
