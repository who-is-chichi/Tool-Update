"""The app the growth team uses. Start it with run_mac.command (Mac) or run_windows.bat (Windows),
or: streamlit run app.py"""
import copy
import hashlib
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tool.common import ToolError, load_settings, save_settings  # noqa: E402
from tool.metrics import mi_name  # noqa: E402
from tool.pipeline import run  # noqa: E402
from tool.report import guardrails, report_bytes  # noqa: E402

SETTINGS = HERE / "settings.xlsx"
# One colour per channel, everywhere (validated for colour-blind separation; fixed order, never by rank).
COLORS = {"Paid Search": "#2a78d6", "Paid Social": "#eb6834", "Affiliate & Influencer": "#1baf7a",
          "Referral": "#eda100", "Organic": "#e87ba4", "Direct": "#008300", "Unattributed": "#8a8984",
          "Unmapped": "#52514e"}
INK, MUTED, GRID, TEAL = "#0b0b0b", "#52514e", "#e6e6e3", "#0F5C63"

st.set_page_config(page_title="DTC Channel Planner", page_icon="🧭", layout="wide")
st.markdown(f"""
<style>
.block-container {{padding-top: 1.6rem; padding-bottom: 3rem;}}
h1, h2, h3 {{color: {TEAL}; letter-spacing: -0.01em;}}
.kpi {{min-height: 132px; background: #ffffff; border: 1px solid {GRID}; border-left: 6px solid var(--accent); border-radius: 12px;
       padding: 14px 16px 12px; height: 100%; box-shadow: 0 1px 2px rgba(0,0,0,0.04);}}
.kpi .label {{color: {MUTED}; font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.04em;}}
.kpi .value {{color: {INK}; font-size: 1.75rem; font-weight: 700; line-height: 1.25; margin-top: 2px;}}
.kpi .sub {{color: {MUTED}; font-size: 0.85rem; margin-top: 2px;}}
.hero {{background: linear-gradient(120deg, {TEAL} 0%, #1d7f86 60%, #2a9d8f 100%); color: white;
        border-radius: 16px; padding: 22px 26px; margin-bottom: 18px;}}
.hero h1 {{color: white; margin: 0; font-size: 1.9rem;}}
.hero p {{color: #e3f4f2; margin: 6px 0 0; font-size: 1rem;}}
.chip {{display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.8rem; margin-right: 6px;
        background: rgba(255,255,255,0.18); color: white;}}
.why {{color: {MUTED}; font-size: 0.9rem;}}
</style>""", unsafe_allow_html=True)


def wide(fn, *args, **kw):
    """Call a Streamlit element full-width on both newer and older Streamlit versions."""
    try:
        return fn(*args, width="stretch", **kw)
    except TypeError:
        return fn(*args, use_container_width=True, **kw)


def money(x, sign=False):
    if x is None or pd.isna(x):
        return "–"
    return f"{'+' if sign and x > 0 else ''}{'-' if x < 0 else ''}${abs(x):,.0f}"


def pct(x, sign=False):
    return "–" if x is None or pd.isna(x) else (f"{x:+.0%}" if sign else f"{x:.1%}")


def show(df, money_cols=(), pct_cols=(), int_cols=(), dec_cols=(), rename=None, **kw):
    """Show a table with readable numbers: $1,234 / 12.3% / 1,234, and a dash for blanks."""
    d = df.copy()
    done = set()
    for cols, fmt in [(money_cols, money), (pct_cols, lambda v: f"{v:.1%}"), (int_cols, lambda v: f"{v:,.0f}"),
                      (dec_cols, lambda v: f"{v:,.2f}")]:
        for c in dict.fromkeys(cols):
            if c in d and c not in done:
                d[c] = d[c].map(lambda v, f=fmt: "–" if pd.isna(v) else (f(v) if isinstance(v, (int, float)) else v))
                done.add(c)
    d = d.astype(object).where(d.notna(), "–")
    if rename:
        d = d.rename(columns=rename)
    wide(st.dataframe, d, hide_index=True, **kw)


def kpi(col, label, value, sub, accent=TEAL):
    col.markdown(f"<div class='kpi' style='--accent:{accent}'><div class='label'>{label}</div>"
                 f"<div class='value'>{value}</div><div class='sub'>{sub}</div></div>", unsafe_allow_html=True)


def channel_scale(domain):
    dom = [c for c in COLORS if c in set(domain)] + sorted(set(domain) - set(COLORS))
    return alt.Scale(domain=dom, range=[COLORS.get(c, "#9e9e9e") for c in dom])


def style_chart(ch, height=320):
    return (ch.properties(height=height)
            .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor=GRID, domainColor=GRID, labelFontSize=12,
                            labelLimit=320, titleFontWeight="normal")
            .configure_legend(labelColor=INK, titleColor=MUTED, orient="bottom", labelFontSize=12, columns=4,
                              labelLimit=200, symbolType="circle")
            .configure_view(stroke=None))


def log_error():
    """Save the details of an unexpected problem to output/error_log.txt; return the file's path."""
    out = HERE / "output"
    out.mkdir(exist_ok=True)
    path = out / "error_log.txt"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n==== {datetime.now():%Y-%m-%d %H:%M:%S}\n{traceback.format_exc()}")
    return path


def unexpected(e):
    path = log_error()
    st.error(f"**The planner hit an unexpected problem** ({type(e).__name__}: {e}). Nothing was changed. The details "
             f"are saved in {path}. Try the backup launcher (run_report_only), or send that file to whoever maintains "
             "the tool.")


def clamp(value, lo, hi, label, notes):
    if value < lo or value > hi:
        notes.append(f"{label}: {value:g} in settings.xlsx is outside the {lo:g} to {hi:g} the control allows; "
                     f"using {min(max(value, lo), hi):g} for this run.")
    return min(max(value, lo), hi)


def table_rows(df, n):
    """Rows typed into a mapping table, without blank or half-filled rows."""
    ok = lambda v: v is not None and str(v).strip() not in ("", "None", "nan", "NaN", "<NA>")
    return [tuple(str(x).strip() for x in row) for row in df.itertuples(index=False) if all(ok(x) for x in row[:n])]


# ------------------------------------------------------------------ sidebar: data and assumptions
try:
    base = load_settings(SETTINGS)
except ToolError as e:
    st.error(f"**settings.xlsx can't be used.** {e}")
    st.stop()

notes = []
with st.sidebar:
    st.markdown("### 1 · This month's files")
    source = st.radio("Where are the four files?", ["The data folder", "Upload them here"], label_visibility="collapsed")
    uploads, folder = None, str(HERE / "data")
    if source == "The data folder":
        folder = st.text_input("Folder", folder, help="The folder holding orders.csv, customers.csv, marketing_spend.csv and cs_notes.csv")
    else:
        uploads = st.file_uploader("Drop orders, customers, marketing_spend and cs_notes (.csv)", type="csv",
                                   accept_multiple_files=True)

    st.markdown("### 2 · Assumptions")
    s = copy.deepcopy(base)
    s["gross_margin"] = st.number_input("Gross margin (%)", 0.0, 100.0, clamp(base["gross_margin"] * 100, 0, 100, "Gross margin (%)", notes), 0.5) / 100
    s["fulfillment_cost_per_order"] = st.number_input("Fulfillment cost per order ($)", 0.0, 100.0, clamp(base["fulfillment_cost_per_order"], 0, 100, "Fulfillment cost", notes), 0.5)
    s["payment_processing_rate"] = st.number_input("Payment processing (% of order)", 0.0, 10.0, clamp(base["payment_processing_rate"] * 100, 0, 10, "Payment processing (%)", notes), 0.1) / 100
    s["returns_resold"] = st.toggle("Returned goods are resold", base["returns_resold"], help="Off = a refund is a total loss")
    s["payback_window_months"] = st.slider("Payback window (months)", 3, 36, int(clamp(base["payback_window_months"], 3, 36, "Payback window", notes)),
                                           help="A channel gets more than this year's spend only while its next customer pays back within this many months.")
    horizon = base["ltv_horizon_months"] if base["ltv_horizon_months"] in (12, 24, 36) else 24
    s["ltv_horizon_months"] = st.select_slider("Value horizon (months from acquisition)", [12, 24, 36], horizon)
    s["max_change_vs_last_year"] = st.slider(
        "Largest change vs the same month last year (%)", 0, 100,
        int(round(clamp(base["max_change_vs_last_year"] * 100, 0, 100, "Largest change (%)", notes))), 5,
        help="At 25%, each channel stays between 75% and 125% of what it got in the same month this year. The lower end is the channel minimum.") / 100
    cap_on = st.toggle("Cap spend near each channel's record month", base["max_above_record_month"] is not None,
                       help="Beyond the highest month in the data, the response to spend is a guess.")
    if cap_on:
        start = clamp((base["max_above_record_month"] if base["max_above_record_month"] is not None else 0.10) * 100, 0, 50, "Record-month cap (%)", notes)
        s["max_above_record_month"] = st.slider("… allowed above the record month (%)", 0, 50, int(round(start)), 5) / 100
    else:
        s["max_above_record_month"] = None
    s["hold_low_confidence_channels"] = st.toggle("Hold channels whose response to spend is uncertain", base["hold_low_confidence_channels"],
                                                  help="Held at this year's spend when their spend has varied too little, or the estimate is too noisy, to trust.")
    bud = st.number_input("Annual acquisition budget ($, 0 = this year's level)", 0.0, 1e9, float(base["acquisition_budget_annual"] or 0), 50000.0, format="%.0f")
    s["acquisition_budget_annual"] = bud or None
    with st.expander("More: sample rate, dates, finance month, channel names, spend lines"):
        s["sample_rate"] = st.number_input("Sample rate (%)", 1.0, 100.0, clamp(base["sample_rate"] * 100, 1, 100, "Sample rate (%)", notes), 1.0) / 100
        fmts = st.text_input("Date formats (tried in order, separated by ;)", "; ".join(base["date_formats"]),
                             help="YYYY year, MM month, DD day, Mon = Jan..Dec. Add a pattern if the planner stops on unreadable dates, e.g. DD.MM.YYYY.")
        s["date_formats"] = [p.strip() for p in fmts.split(";") if p.strip()]
        s["finance_as_of_month"] = st.text_input("Finance figures as of (YYYY-MM, blank = latest data)", base["finance_as_of_month"] or "").strip() or None
        cm = pd.DataFrame(list(base["channel_map"].items()), columns=["label in customers.csv", "channel"])
        cm = wide(st.data_editor, cm, num_rows="dynamic", key="cm")
        s["channel_map"] = dict(table_rows(cm, 2))
        sm = pd.DataFrame([(k, v[0], v[1]) for k, v in base["spend_map"].items()], columns=["spend line", "channel", "treatment"])
        sm = wide(st.data_editor, sm, num_rows="dynamic", key="sm",
                  column_config={"treatment": st.column_config.SelectboxColumn(options=["reallocate", "hold_flat", "retention"])})
        s["spend_map"] = {a: (b, c.lower()) for a, b, c in table_rows(sm, 3)}
    for n in notes:
        st.warning(n)
    if wide(st.button, "Save these as the defaults"):
        try:
            save_settings(SETTINGS, s)
            st.success("Saved to settings.xlsx")
        except ToolError as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001
            unexpected(e)
    go = wide(st.button, "Run the planner", type="primary")


@st.cache_data(show_spinner=False, max_entries=8)
def cached_run(data_key, _folder, settings_key, _settings):
    return run(_folder, _settings)


def folder_key(path):
    h = hashlib.md5(str(path).encode())
    p = Path(path)
    if p.is_dir():
        for f in sorted(p.glob("*.csv")):
            stat = f.stat()
            h.update(f"{f.name}{stat.st_size}{stat.st_mtime_ns}".encode())
    return h.hexdigest()


def uploads_key(files):
    h = hashlib.md5()
    for f in sorted(files, key=lambda x: x.name):
        h.update(f.name.encode())
        h.update(hashlib.md5(f.getvalue()).digest())
    return h.hexdigest()


# ------------------------------------------------------------------ run
if st.query_params.get("autorun") == "1" and "res" not in st.session_state and "autorun_done" not in st.session_state:
    go = True                     # opening the app with ?autorun=1 runs it straight away on the data folder
    st.session_state["autorun_done"] = True
if go:
    try:
        skey = repr(sorted((k, str(v)) for k, v in s.items()))
        with st.spinner("Cleaning the files, scoring channels and planning next month (about 20 seconds)…"):
            if source == "Upload them here":
                if not uploads:
                    raise ToolError("Upload the four files first.")
                with tempfile.TemporaryDirectory(prefix="planner_") as tmp:
                    for f in uploads:
                        (Path(tmp) / Path(f.name).name).write_bytes(f.getvalue())
                    res = cached_run(uploads_key(uploads), tmp, skey, s)
            else:
                res = cached_run(folder_key(folder), folder, skey, s)
        st.session_state["res"] = res
        st.session_state.pop("report", None)
    except ToolError as e:
        st.session_state.pop("res", None)
        st.error(f"**The planner stopped.** {e}")
    except Exception as e:  # noqa: BLE001
        st.session_state.pop("res", None)
        unexpected(e)

res = st.session_state.get("res")
if not res:
    st.markdown("<div class='hero'><h1>DTC Channel Planner</h1>"
                "<p>Splits next month's acquisition budget by what customers are worth, not by what they cost.</p></div>",
                unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    kpi(c1, "Step 1", "Add the files", "Point to this month's export, or upload the four CSVs in the sidebar.", COLORS["Paid Search"])
    kpi(c2, "Step 2", "Check assumptions", "Margin, costs, payback window and limits. No code to edit.", COLORS["Affiliate & Influencer"])
    kpi(c3, "Step 3", "Run the planner", "Get next month's split, the channel scorecard and a downloadable report.", COLORS["Paid Social"])
    st.stop()


# ------------------------------------------------------------------ results
def render(res):
    S = res["settings"]
    H, W = S["ltv_horizon_months"], S["payback_window_months"]
    nm = res["next_month"].copy()
    flat = res["next_month_held_flat"]
    m1 = int(nm["m"].iloc[0])
    month, month_s, last_s, ly_s = mi_name(m1, "%B %Y"), mi_name(m1), mi_name(m1 - 1), mi_name(m1 - 12)
    rec = res["reconciliation"]
    checks = rec[rec["kind"].isin(["rate", "money"])]
    fin_label = pd.Period(res["reconciliation_as_of"], freq="M").strftime("%b %Y")
    d_cust = nm["recommended_customers"].sum() - nm["baseline_customers"].sum()
    d_val = nm["contribution_change"].sum()
    flat_names = " and ".join(flat["channel"]) if len(flat) else "Brand and retention spend"

    st.markdown(f"<div class='hero'><h1>Next month's acquisition plan · {month}</h1>"
                f"<p><span class='chip'>Data through {mi_name(m1 - 1, '%B %Y')}</span>"
                f"<span class='chip'>Budget held at {money(res['budget'])} a year</span>"
                f"<span class='chip'>{flat_names} held flat</span></p></div>", unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)
    kpi(k1, f"Budget for {month_s}", money(nm["baseline_spend"].sum()), f"This year's {ly_s} amount, scaled to the budget", COLORS["Paid Search"])
    kpi(k2, f"Value added in {month_s}", money(d_val, True), f"{H}-month contribution · {d_cust:+,.0f} customers, same spend", COLORS["Affiliate & Influencer"])
    kpi(k3, "Next 12 months on this plan", money(res["impact"], True), f"{H}-month value of the customers gained and lost vs this year's split", COLORS["Referral"])
    ok = int((checks["status"] == "match").sum())
    kpi(k4, "Checks against finance", f"{ok} of {len(checks)} match", f"Repeat rates, blended CAC, AOV, revenue · as of {fin_label}",
        "#0ca30c" if ok == len(checks) else "#fab219")

    if res["log"].warnings:
        with st.expander(f"⚠️  {len(res['log'].warnings)} things to know about this run", expanded=True):
            for wmsg in res["log"].warnings:
                st.markdown(f"- {wmsg}")

    st.markdown(f"## Where the money should go in {month}")
    left, right = st.columns([1.05, 1])
    bars = pd.concat([nm.assign(plan="This year's split", spend=nm["baseline_spend"]),
                      nm.assign(plan="Recommended", spend=nm["recommended_spend"])])
    bars["label"] = bars["spend"].map(lambda v: f"${v / 1000:,.0f}k")
    chart = alt.Chart(bars).encode(
        y=alt.Y("channel:N", title=None, sort=list(COLORS)),
        yOffset=alt.YOffset("plan:N", sort=["This year's split", "Recommended"]),
        x=alt.X("spend:Q", title=f"Spend in {month} ($)", axis=alt.Axis(format="$,.0s")),
        tooltip=[alt.Tooltip("channel:N"), alt.Tooltip("plan:N"), alt.Tooltip("spend:Q", format="$,.0f")])
    layer = (chart.mark_bar(cornerRadiusEnd=4, height=16).encode(
        color=alt.condition(alt.datum.plan == "Recommended",
                            alt.Color("channel:N", scale=channel_scale(bars["channel"]), legend=None), alt.value("#cfcfca")))
        + chart.mark_text(align="left", dx=4, color=MUTED, fontSize=11).encode(text="label:N"))
    wide(left.altair_chart, style_chart(layer, 300))
    left.caption(f"Grey = this year's split for {ly_s}. Colour = recommended. Same total budget.")
    for r in nm.sort_values("change", ascending=False).itertuples():
        arrow = "🟢 ▲" if r.change > 1 else ("🔴 ▼" if r.change < -1 else "⚪ ●")
        vs = f"{pct(r.change_pct, True)} vs {ly_s}" + (f" · {pct(r.vs_last_month, True)} vs {last_s}" if pd.notna(r.vs_last_month) else "")
        right.markdown(f"**{arrow} {r.channel}**  {money(r.baseline_spend)} → **{money(r.recommended_spend)}** ({vs})  \n"
                       f"<span class='why'>{r.why}</span>", unsafe_allow_html=True)
    for r in flat.itertuples():
        right.markdown(f"**⚪ ● {r.channel}**  {money(r.spend)} (held flat)  \n<span class='why'>{r.why}</span>",
                       unsafe_allow_html=True)
    total = nm["recommended_spend"].sum() + flat["spend"].sum()
    right.markdown(f"**Total {month_s}: {money(total)}** (acquisition {money(nm['recommended_spend'].sum())} + held flat {money(flat['spend'].sum())})")
    with st.expander("Guardrails we chose and why"):
        for r in guardrails(res).itertuples():
            st.markdown(f"- **{r.choice}.** {r[2]}")

    tabs = st.tabs(["📋 Scorecard", "🔁 Repeat & cohorts", "💰 Value & payback", "📈 12-month impact",
                    "🎚️ Sensitivity", "✅ Checks vs finance", "🧹 Data cleaning", "⚙️ Settings used"])

    with tabs[0]:
        sc = res["scorecard"].copy()
        sc["verdict"] = sc["verdict"].map(lambda v: ("✅ " if v.startswith("Pays") else "⚠️ " if v.startswith("Next") else "➖ ") + v)
        show(sc, money_cols=[f"value_{W}m", f"value_{H}m", "cac_last_3m", "next_customer_cost"],
             pct_cols=["share_of_new", "year1_repeat_latest_cohort", "repeat_90d_latest_quarter"], int_cols=["new_customers_last_12m"],
             rename={"channel": "Channel", "new_customers_last_12m": "New customers, 12 mo", "share_of_new": "Share of new",
                     "year1_repeat_latest_cohort": "Year-1 repeat", "latest_full_cohort": "Cohort",
                     "repeat_90d_latest_quarter": f"90-day repeat ({res['early_read_latest_quarter']})",
                     f"value_{W}m": f"Value in {W} mo", f"value_{H}m": f"Value in {H} mo", "cac_last_3m": "CAC (3 mo)",
                     "next_customer_cost": f"Next customer's cost ({ly_s} spend)", "payback_month": "Pays back", "verdict": "Verdict"})
        st.caption(f"Value = contribution per customer ({S['gross_margin']:.0%} margin less ${S['fulfillment_cost_per_order']:,.0f} "
                   f"fulfillment and {S['payment_processing_rate']:.1%} payment costs, net of refunds), projected for recent customers. "
                   "Payback month 0 = paid back on the first order. Next customer's cost = what one more customer costs at this "
                   "year's spend, from the channel's response curve.")

    with tabs[1]:
        rep = res["repeat"]
        y1 = rep[(rep["year"] == 1) & (rep["channel"] != "Unmapped")].copy()
        y1["series"] = y1["channel"].replace({"All": "All customers"})
        c1, c2 = st.columns(2)
        dom = [c for c in COLORS if c in set(y1["channel"])] + ["All customers"]
        rng = [COLORS[c] for c in dom[:-1]] + [INK]
        line = alt.Chart(y1).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60, filled=True)).encode(
            x=alt.X("cohort:O", title="Customers acquired in", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("repeat_rate:Q", title="Year-1 repeat", axis=alt.Axis(format="%")),
            color=alt.Color("series:N", scale=alt.Scale(domain=dom, range=rng), title=None),
            strokeDash=alt.condition(alt.datum.series == "All customers", alt.value([6, 4]), alt.value([1, 0])),
            tooltip=[alt.Tooltip("series", title="channel"), "cohort", alt.Tooltip("repeat_rate", format=".1%"),
                     alt.Tooltip("customers", format=",.0f")])
        c1.markdown("**Year-1 repeat by cohort** · dashed = all customers")
        wide(c1.altair_chart, style_chart(line, 330))
        er = res["early_read"].reset_index().melt(id_vars="channel", var_name="quarter", value_name="rate")
        er = er[er["channel"] != "All"]
        l2 = alt.Chart(er).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=50, filled=True)).encode(
            x=alt.X("quarter:O", title="Quarter of first order", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("rate:Q", title="90-day repeat", axis=alt.Axis(format="%")),
            color=alt.Color("channel:N", scale=channel_scale(er["channel"]), title=None),
            tooltip=["channel", "quarter", alt.Tooltip("rate", format=".1%")])
        c2.markdown("**Early read: 90-day repeat by quarter**")
        wide(c2.altair_chart, style_chart(l2, 330))
        yl = y1[~y1["channel"].isin(["All"])]
        if yl["cohort"].nunique() >= 2:
            latest, prev = int(yl["cohort"].max()), int(sorted(yl["cohort"].unique())[-2])
            cur = yl[yl["cohort"] == latest].set_index("channel")["repeat_rate"]
            old = yl[yl["cohort"] == prev].set_index("channel")["repeat_rate"]
            down = sum(1 for c in cur.index if c in old.index and cur[c] < old[c])
            st.caption(f"{latest} customers: {cur.idxmin()} repeats least in year 1 ({cur.min():.0%}), {cur.idxmax()} most "
                       f"({cur.max():.0%}). Year-1 repeat fell from {prev} to {latest} customers in {down} of {len(cur)} channels.")
        tbl = rep.pivot_table(index="channel", columns=["cohort", "year"], values="repeat_rate")
        tbl.columns = [f"{c} · Y{y}" for c, y in tbl.columns]
        wide(st.dataframe, tbl.style.format("{:.1%}", na_rep="–"))
        with st.expander("Detail with customer counts and ± ranges"):
            show(rep, pct_cols=["repeat_rate", "plus_minus"], int_cols=["customers", "customers_in_sample"],
                 rename={"customers": f"customers (whole business)", "customers_in_sample": "customers in the sample",
                         "repeat_rate": "repeat rate", "plus_minus": "± (95%)"})
            st.caption(f"Customers are a {S['sample_rate']:.0%} sample: counts are divided by {S['sample_rate']:g}; the ± range "
                       "is how far the rate could be from the whole business's rate because of sampling.")
        dec = res["repeat_decomposition"]
        with st.expander("Why repeat changed: channel mix vs within-channel"):
            if len(dec):
                d1 = dec[(dec["year"] == 1) & (dec["channel"] == "All")]
                head = d1.iloc[-1]
                st.markdown(f"Year-1 repeat went from **{head.rate_from:.1%}** ({head.from_cohort} customers) to "
                            f"**{head.rate_to:.1%}** ({head.to_cohort}): **{head.from_mix * 100:+.1f} points** from the channel "
                            f"mix (more customers from low-repeat channels) and **{head.from_within_channel * 100:+.1f} points** "
                            "from lower repeat within channels.")
                show(dec[dec["channel"] == "All"][["year", "from_cohort", "to_cohort", "rate_from", "rate_to", "change", "from_mix", "from_within_channel"]],
                     pct_cols=["rate_from", "rate_to", "change", "from_mix", "from_within_channel"],
                     rename={"from_cohort": "from", "to_cohort": "to", "rate_from": "rate before", "rate_to": "rate after",
                             "from_mix": "from channel mix", "from_within_channel": "from within channels"})
                detail = dec[(dec["year"] == 1) & (dec["channel"] != "All") & (dec["from_cohort"] == head.from_cohort) & (dec["to_cohort"] == head.to_cohort)]
                show(detail[["channel", "share_from", "share_to", "rate_from", "rate_to", "from_mix", "from_within_channel"]],
                     pct_cols=["share_from", "share_to", "rate_from", "rate_to", "from_mix", "from_within_channel"],
                     rename={"share_from": f"share of new, {head.from_cohort}", "share_to": f"share of new, {head.to_cohort}",
                             "rate_from": f"year-1 repeat, {head.from_cohort}", "rate_to": f"year-1 repeat, {head.to_cohort}",
                             "from_mix": "from mix", "from_within_channel": "from within channel"})
                st.caption("The two parts use the average of both years' shares and rates, so they add up exactly to the change.")
        si = res.get("service_issues")
        with st.expander("Service issues (customer-service notes)"):
            if si:
                show(si["summary"], dec_cols=["per_1000_orders_latest_quarter", "per_1000_orders_year_earlier"], pct_cols=["change"],
                     int_cols=["notes_latest_quarter_in_sample"],
                     rename={"theme": "Theme", "service_problem": "Service problem", "notes_latest_quarter_in_sample": f"Notes {si['latest_quarter']} (sample)",
                             "per_1000_orders_latest_quarter": f"Per 1,000 orders, {si['latest_quarter']}",
                             "per_1000_orders_year_earlier": f"Per 1,000 orders, {si['year_earlier_quarter']}", "change": "Change"})
                bq = si["by_quarter"].reset_index().melt(id_vars="theme", var_name="quarter", value_name="per_1000")
                probs = si["summary"].loc[si["summary"]["service_problem"] == "yes", "theme"].tolist()
                bq = bq[bq["theme"].isin(probs)]
                lc = alt.Chart(bq).mark_line(point=True).encode(
                    x=alt.X("quarter:O", title=None, axis=alt.Axis(labelAngle=-45)),
                    y=alt.Y("per_1000:Q", title="Notes per 1,000 orders"),
                    color=alt.Color("theme:N", title=None), tooltip=["theme", "quarter", alt.Tooltip("per_1000", format=".1f")])
                st.markdown("**Service problems over time**")
                wide(st.altair_chart, style_chart(lc, 300))
                st.markdown(f"**By acquisition channel, {si['window']}** (notes per 1,000 orders)")
                bc = si["by_channel"].reset_index()
                show(bc, dec_cols=[c for c in bc.columns if c != "theme"])
                st.caption(f"Notes per 1,000 orders (sample notes ÷ sample orders, so no scaling). Themes are keyword rules in "
                           f"settings.xlsx › note_themes; {si['other_share']:.1%} of notes matched no theme.")
            else:
                st.write("No complete quarter of notes to show.")

    with tabs[2]:
        curves, cac = res["value_curves"], res["cac_l3m"]
        paid = [c for c in COLORS if c in cac.index and c in curves]
        if paid:
            vc = pd.DataFrame([{"channel": c, "month": k, "value": v} for c in paid for k, v in enumerate(curves[c].values[:max(H, W)])])
            joined = vc.merge(pd.DataFrame({"channel": paid, "cac": [cac[c] for c in paid]}), on="channel")
            st.markdown(f"**When does a customer pay back?** Line = contribution so far. Dashed grey = CAC (last 3 months). Red = {W}-month window.")
            f2 = alt.layer(
                alt.Chart().mark_line(strokeWidth=2.5).encode(
                    x=alt.X("month:Q", title="Months since first order"),
                    y=alt.Y("value:Q", title="$ per customer", axis=alt.Axis(format="$,.0f")),
                    color=alt.Color("channel:N", scale=channel_scale(paid), legend=None),
                    tooltip=["channel", "month", alt.Tooltip("value", format="$,.0f"), alt.Tooltip("cac", title="CAC", format="$,.0f")]),
                alt.Chart().mark_rule(strokeDash=[5, 4], color=MUTED).encode(y="mean(cac):Q"),
                alt.Chart().mark_rule(color="#d03b3b", strokeDash=[2, 3]).encode(x=alt.datum(W - 1)),
                data=joined).properties(width=440, height=200).facet(facet=alt.Facet("channel:N", title=None, sort=paid), columns=2)
            st.altair_chart(f2.configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor=GRID, domainColor=GRID)
                            .configure_view(stroke=None).configure_header(labelColor=INK, labelFontSize=13, labelFontWeight="bold"))
        show(res["value"].reset_index(), money_cols=[f"value_{H}m_today", f"value_{H}m_latest_full_cohorts"], dec_cols=["today_vs_latest_full"],
             rename={"channel": "Channel", f"value_{H}m_today": f"Value {H} mo, today's customers",
                     f"value_{H}m_latest_full_cohorts": f"Value {H} mo, latest full cohorts (observed)",
                     "latest_full_cohorts": "Latest full cohorts", "today_customers_from": "Today's customers from",
                     "today_vs_latest_full": "Today ÷ latest full"})
        cl = res["cohort_ltv"]
        st.markdown("**LTV on contribution margin, by channel and cohort**")
        show(cl[["channel", "cohort", "customers", "ltv_12m", "ltv_24m", "ltv_36m", f"ltv_{H}m_projected", "projection"]],
             money_cols=["ltv_12m", "ltv_24m", "ltv_36m", f"ltv_{H}m_projected"], int_cols=["customers"],
             rename={"channel": "Channel", "cohort": "Cohort", "customers": "Customers", "ltv_12m": "Observed 12 mo",
                     "ltv_24m": "Observed 24 mo", "ltv_36m": "Observed 36 mo", f"ltv_{H}m_projected": f"{H} mo incl. projection",
                     "projection": "Basis"})
        with st.expander("Detail with ± ranges"):
            show(cl, money_cols=[c for c in cl.columns if c.startswith("ltv_") and "customers" not in c],
                 int_cols=["customers", "customers_in_sample"] + [c for c in cl.columns if c.endswith("customers_in_sample")])
            st.caption("Observed = average over the customers whose 12, 24 or 36 months have passed, with a 95% range. "
                       f"'{H} mo incl. projection' grows each start month's value so far with the chain-ladder growth.")
        bt = res["backtest"]
        if len(bt):
            a = bt[bt["test"].str.startswith("A")]
            b_ = bt[bt["test"].str.startswith("B")]
            parts = []
            if len(a):
                parts.append(f"projecting the latest full cohorts from their first 12 months lands within "
                             f"{a[a.projected_from_months == 12]['error'].abs().max():.1%} of actual, from 3 months within "
                             f"{a[a.projected_from_months == 3]['error'].abs().max():.1%}")
            if len(b_):
                parts.append(f"projecting customers acquired {b_['test'].iloc[0].split('customers from ')[-1]} from 3 months to 12 "
                             f"lands within {b_['error'].abs().max():.1%}")
            st.caption("Back-test: " + "; ".join(parts) + ". Each projection uses only the data available at the time.")

    with tabs[3]:
        imp = res["impact_by_channel"].copy()
        c1, c2 = st.columns([1, 1.1])
        ib = alt.Chart(imp).mark_bar(cornerRadiusEnd=4, height=22).encode(
            y=alt.Y("channel:N", sort=list(COLORS), title=None),
            x=alt.X("contribution_change:Q", title=f"Change in {H}-month contribution ($)", axis=alt.Axis(format="$,.0s")),
            color=alt.Color("channel:N", scale=channel_scale(imp["channel"]), legend=None),
            tooltip=["channel", alt.Tooltip("customers_change", title="Customers gained/lost", format="+,.0f"),
                     alt.Tooltip("contribution_change", title="Contribution change", format="$,.0f")])
        zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=MUTED).encode(x="x:Q")
        c1.markdown(f"**Next 12 months: {money(res['impact'], True)} of {H}-month contribution, same budget**")
        wide(c1.altair_chart, style_chart(ib + zero, 260))
        c1.caption(f"Customers gained in channels that get more, minus customers lost in channels that get less, each valued "
                   f"over their first {H} months from acquisition.")
        with c2:
            show(imp, money_cols=["baseline_spend", "recommended_spend", "contribution_change", "value_per_customer"],
                 int_cols=["baseline_customers", "recommended_customers", "customers_change"],
                 rename={"channel": "Channel", "baseline_spend": "This year's split", "recommended_spend": "Recommended",
                         "baseline_customers": "Customers now", "recommended_customers": "Customers on plan", "customers_change": "Change",
                         "contribution_change": "Contribution change", "value_per_customer": "Value per customer"})
        cy = res["impact_by_calendar_year"]
        if len(cy):
            st.markdown(f"**Where that value lands, by calendar year**")
            show(cy, money_cols=[c for c in cy.columns if c != "year"], rename={"year": "Year", "total": "Total"})
        resp = res["response"]
        st.markdown(f"**How strongly new customers respond to spend** (fitted from up to {int(resp['months_used'].max()) if len(resp) else 0} months of history)")
        show(resp, money_cols=["spend_min", "spend_max"], pct_cols=["error_last_12m"], dec_cols=["b", "b_std_error"],
             rename={"channel": "Channel", "model": "Model chosen", "b": "Response b", "b_std_error": "± error",
                     "error_last_12m": "Forecast error, last 12 mo", "months_used": "Months", "spend_min": "Lowest month seen",
                     "spend_max": "Highest month seen", "confidence": "Confidence", "why_low_confidence": "Why low"})
        st.caption("b = 0.8 means 10% more spend buys about 8% more customers. The model is picked each run by how well it predicted the last 12 months.")

    with tabs[4]:
        sens = res.get("sensitivity")
        if sens is not None:
            base_v = float(sens.loc[sens["scenario"] == "Base case", "impact"].iloc[0])
            sv = sens[sens["scenario"] != "Base case"].copy()
            sv["direction"] = sv["change_vs_base"].map(lambda v: "Raises the impact" if v > 0 else ("Lowers the impact" if v < 0 else "No change"))
            order = sv.reindex(sv["change_vs_base"].abs().sort_values(ascending=False).index)["scenario"].tolist()
            sb = alt.Chart(sv).mark_bar(cornerRadiusEnd=4, height=16).encode(
                y=alt.Y("scenario:N", sort=order, title=None),
                x=alt.X("change_vs_base:Q", title="Change in the 12-month impact vs the base case ($)", axis=alt.Axis(format="$,.0s")),
                color=alt.Color("direction:N", scale=alt.Scale(domain=["Raises the impact", "Lowers the impact", "No change"],
                                                                range=["#1d7f86", "#c0504d", "#b5b5b0"]), title=None),
                tooltip=["scenario", alt.Tooltip("impact", title="Impact", format="$,.0f"), alt.Tooltip("change_vs_base", title="vs base", format="+$,.0f")])
            zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=INK).encode(x="x:Q")
            st.markdown(f"**What moves the answer?** Base case: **{money(base_v, True)}**. Each bar changes one assumption and reruns the 12-month plan.")
            wide(st.altair_chart, style_chart(sb + zero, 440))
            sv["same_direction"] = sv["same_direction"].map({True: "yes", False: "no"})
            show(sv[["scenario", "impact", "change_vs_base", "same_direction"]], money_cols=["impact", "change_vs_base"],
                 rename={"scenario": "Scenario", "impact": "12-month impact", "change_vs_base": "vs base case",
                         "same_direction": f"Same moves in {month_s}?"})
            top = order[:2]
            st.caption(f"Biggest movers: {' and '.join(top)}. Every channel moves the same way as the base case in "
                       f"{int((sv['same_direction'] == 'yes').sum())} of {len(sv)} scenarios.")

    with tabs[5]:
        r2 = rec.copy()

        def fmt(v, kind, metric):
            if pd.isna(v):
                return "–"
            if kind in ("rate", "explained"):
                return f"{v:.1%}"
            if "revenue" in metric.lower():
                return f"${v / 1e6:,.1f}M"
            return ("-" if v < 0 else "") + f"${abs(v):,.2f}"

        for col in ["tool", "finance", "difference"]:
            r2[col] = [fmt(v, k, m) for v, k, m in zip(r2[col], r2["kind"], r2["metric"])]
        r2["status"] = r2["status"].map({"match": "✅ match", "check": "⚠️ check", "definition differs": "ℹ️ definition differs",
                                         "can't compute": "⚠️ can't compute"}).fillna(r2["status"])
        show(r2.drop(columns=["kind"]), rename={"metric": "Metric", "tool": "This tool", "finance": "Finance",
                                                "difference": "Difference", "status": "Status", "note": "Note"})
        gaps = checks.assign(rel=[abs(d) * 100 if k == "rate" else abs(d / f) * 100 for d, k, f in zip(checks["difference"], checks["kind"], checks["finance"])])
        big = gaps.sort_values("rel", ascending=False).iloc[0] if len(gaps) else None
        st.caption(f"Finance's figures are as of {fin_label}; the tool recomputes its own figures at that month, so the checks "
                   "compare like with like on any new month of data. Repeat rates match within 1 point, money figures within 3%."
                   + (f" Largest gap: {big.metric}." if big is not None else ""))

    with tabs[6]:
        lt = res["log"].table()
        wide(st.dataframe, lt, hide_index=True, column_config={"rows affected": st.column_config.NumberColumn(format="%,d")})
        st.download_button("Download the cleaning log (CSV)", lt.to_csv(index=False).encode(), f"cleaning_log_{res['as_of']}.csv", "text/csv")

    with tabs[7]:
        rows = [(k, "; ".join(v) if k == "date_formats" else str(v)) for k, v in S.items()
                if not isinstance(v, (dict, list, tuple)) or k == "date_formats"]
        wide(st.dataframe, pd.DataFrame(rows, columns=["setting", "value"]), hide_index=True)

    st.divider()
    if "report" not in st.session_state:
        st.session_state["report"] = report_bytes(res)
    st.download_button("⬇️  Download the full Excel report", st.session_state["report"], f"channel_plan_{res['as_of']}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")


try:
    render(res)
except Exception as e:  # noqa: BLE001
    unexpected(e)
