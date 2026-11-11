"""Scenario tests. Run:  python tests/run_tests.py   (writes tests/TEST_REPORT.md)

Each test copies the data folder, changes something the way a real export might, runs the planner, and checks
what it did. Temporary copies are deleted at the end; the original files are never changed."""
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from tool.common import ToolError, load_settings, save_settings  # noqa: E402
from tool.pipeline import run  # noqa: E402
from tool.report import write_report  # noqa: E402

DATA = HERE / "data"
SETTINGS = load_settings(HERE / "settings.xlsx")
PAID = ["Affiliate & Influencer", "Paid Search", "Paid Social", "Referral"]
results, TEMP, _cache = [], [], {}


def temp_dir():
    d = Path(tempfile.mkdtemp(prefix="planner_test_"))
    TEMP.append(d)
    return d


def copy_data():
    """A writable copy of the data folder (the originals may be read-only)."""
    d = temp_dir()
    for f in DATA.glob("*.csv"):
        shutil.copyfile(f, d / f.name)
        os.chmod(d / f.name, 0o644)
    return d


def read(d, name):
    return pd.read_csv(d / f"{name}.csv", dtype=str, keep_default_na=False)


def write(df, d, name):
    df.to_csv(d / f"{name}.csv", index=False)


def settings(**over):
    s = dict(SETTINGS)
    s.update(over)
    return s


def base():
    """The real files with the saved settings: run once, reused by several tests."""
    if "res" not in _cache:
        _cache["res"] = run(DATA, SETTINGS)
    return _cache["res"]


def budget_holds(res):
    p = res["plan"].groupby("m")[["baseline_spend", "recommended_spend"]].sum()
    return bool((p["baseline_spend"] - p["recommended_spend"]).abs().max() < 1.0)


def test(name):
    def wrap(fn):
        t = time.time()
        try:
            detail = fn()
            results.append((name, "PASS", detail, time.time() - t))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", f"{type(e).__name__}: {e}", time.time() - t))
            traceback.print_exc()
        print(f"[{results[-1][1]}] {name} ({results[-1][3]:.0f}s): {results[-1][2]}")
        return fn
    return wrap


@test("1. Full run on the real files; every check against finance matches")
def t1():
    res = base()
    rec = res["reconciliation"]
    checks = rec[rec["kind"].isin(["rate", "money"])]
    assert (checks["status"] == "match").all(), checks[checks["status"] != "match"]
    assert (rec.loc[rec["kind"] == "explained", "status"] == "definition differs").all()
    assert budget_holds(res), "recommended spend does not add up to the budget in every month"
    assert res["impact"] > 0
    path = write_report(res, temp_dir())
    sheets = pd.ExcelFile(path).sheet_names
    assert len(sheets) >= 20, sheets
    return (f"{len(checks)}/{len(checks)} checks match (as of {res['reconciliation_as_of']}) plus "
            f"{int((rec['kind'] == 'explained').sum())} explained line; budget ${res['budget']:,.0f} held in every month; "
            f"12-month impact ${res['impact']:,.0f}; Excel report written ({len(sheets)} sheets)")


@test("2. New month: run on data cut at September, then with October added, no code change")
def t2():
    d = copy_data()
    o = read(d, "orders")
    dt = pd.to_datetime(o["order_date"], format="mixed")
    write(o[dt <= "2026-09-30"], d, "orders")
    s = read(d, "marketing_spend")
    write(s[s["month"] <= "2026-09"], d, "marketing_spend")
    n = read(d, "cs_notes")
    write(n[n["note_date"] <= "2026-09-30"], d, "cs_notes")
    sep = run(d, SETTINGS, with_sensitivity=False)
    octo = base()
    assert sep["as_of"] == "2026-09" and sep["next_month"]["month"].iloc[0] == "2026-10"
    assert octo["as_of"] == "2026-10" and octo["next_month"]["month"].iloc[0] == "2026-11"
    assert budget_holds(sep) and budget_holds(octo)
    return (f"September run -> plan for 2026-10 (budget ${sep['budget']:,.0f}); October run -> plan for 2026-11 "
            f"(budget ${octo['budget']:,.0f}); budget rolled forward automatically")


@test("3. New channel label ('TikTok') appears in customers.csv")
def t3():
    d = copy_data()
    c = read(d, "customers")
    c.loc[c.sample(400, random_state=1).index, "acquisition_channel"] = "TikTok"
    write(c, d, "customers")
    res = run(d, SETTINGS, with_sensitivity=False)
    w = [x for x in res["log"].warnings if "TikTok" in x]
    assert w, "no warning about TikTok"
    assert "Unmapped" in set(res["scorecard"]["channel"])
    assert budget_holds(res)
    return f"ran to completion; warning: \"{w[0][:90]}...\"; customers shown as 'Unmapped'"


@test("4. New spend line ('TikTok Ads') appears in marketing_spend.csv")
def t4():
    d = copy_data()
    s = read(d, "marketing_spend")
    extra = s[s["channel"] == "Meta"].copy()
    extra["channel"], extra["spend_usd"] = "TikTok Ads", "15000"
    write(pd.concat([s, extra]), d, "marketing_spend")
    res = run(d, SETTINGS, with_sensitivity=False)
    w = [x for x in res["log"].warnings if "TikTok Ads" in x]
    assert w and "TikTok Ads" not in set(res["next_month"]["channel"])
    assert "TikTok Ads" in set(res["next_month_held_flat"]["channel"])
    assert budget_holds(res)
    return "held flat, shown with Brand and Email & SMS, left out of the reallocation, with a warning"


@test("5. Columns reordered and an extra column added")
def t5():
    d = copy_data()
    o = read(d, "orders")
    o["warehouse_code"] = "SLC1"
    write(o[list(reversed(o.columns))], d, "orders")
    c = read(d, "customers")
    write(c[list(reversed(c.columns))], d, "customers")
    a = run(d, SETTINGS, with_sensitivity=False)
    assert abs(a["impact"] - base()["impact"]) < 1e-6
    assert any(r["rule"] == "Extra columns" for r in a["log"].rows)
    return f"identical results to the original files (impact ${a['impact']:,.0f}); extra column logged and ignored"


@test("6. A third date format mixed in: a few rows (skipped with a warning) vs many rows (clear stop)")
def t6():
    d = copy_data()
    o = read(d, "orders")
    shop = o[o["source_system"] == "shopify"].sample(300, random_state=2).index
    o.loc[shop, "order_date"] = pd.to_datetime(o.loc[shop, "order_date"]).dt.strftime("%Y.%m.%d")
    write(o, d, "orders")
    res = run(d, SETTINGS, with_sensitivity=False)
    assert [x for x in res["log"].warnings if "unreadable dates" in x]
    o.loc[o.sample(frac=0.05, random_state=3).index, "order_date"] = "31.12.2025"
    write(o, d, "orders")
    try:
        run(d, SETTINGS, with_sensitivity=False)
        raise AssertionError("did not stop at 5% unreadable dates")
    except ToolError as e:
        msg = str(e)
    assert "date_formats" in msg
    return f"300 rows: skipped with a warning. 5% of rows: stopped with \"{msg[:110]}...\""


@test("7. Orders and spend end in different months")
def t7():
    d = copy_data()
    s = read(d, "marketing_spend")
    write(s[s["month"] <= "2026-09"], d, "marketing_spend")
    res = run(d, SETTINGS, with_sensitivity=False)
    assert res["as_of"] == "2026-09" and [x for x in res["log"].warnings if "Using 2026-09" in x]
    return "used September, the last month both files cover, and said so"


@test("8. A required column is missing")
def t8():
    d = copy_data()
    write(read(d, "orders").drop(columns=["order_date"]), d, "orders")
    try:
        run(d, SETTINGS, with_sensitivity=False)
        raise AssertionError("did not stop")
    except ToolError as e:
        assert "order_date" in str(e)
        return f"stopped with a plain message: \"{e}\""


@test("9. Assumptions changed without touching code")
def t9():
    low = run(DATA, settings(gross_margin=0.55), with_sensitivity=False)
    tight = run(DATA, settings(max_change_vs_last_year=0.10), with_sensitivity=False)
    big = run(DATA, settings(acquisition_budget_annual=12_000_000), with_sensitivity=False)
    assert low["impact"] < base()["impact"]
    p = tight["plan"]
    assert ((p["recommended_spend"] / p["baseline_spend"] - 1).abs() <= 0.1000001).all()
    assert abs(big["budget"] - 12_000_000) < 1 and budget_holds(big)
    return (f"margin 55%: impact ${low['impact']:,.0f} (< ${base()['impact']:,.0f}); 10% limit respected in all 12 "
            "months; $12M budget override used")


@test("10. File names with prefixes or browser copy numbers, and the same result every run")
def t10():
    d = copy_data()
    for f in list(d.glob("*.csv")):
        f.rename(d / (f"{f.stem} (1).csv" if f.stem == "orders" else f"2026-10-{f.name}"))
    a = run(d, SETTINGS, with_sensitivity=False)["impact"]
    assert abs(a - base()["impact"]) < 1e-6, (a, base()["impact"])
    return f"found 'orders (1).csv' and '2026-10-customers.csv' etc.; identical impact on both runs (${a:,.0f})"


@test("11. Spend written as '$1,234.56' gives the same result; an unreadable spend value stops the run")
def t11():
    d = copy_data()
    s = read(d, "marketing_spend")
    s["spend_usd"] = s["spend_usd"].map(lambda v: f"${float(v):,.2f}")
    write(s, d, "marketing_spend")
    res = run(d, SETTINGS, with_sensitivity=False)
    assert abs(res["impact"] - base()["impact"]) < 1e-6
    assert any(r["rule"] == "Amounts written as text" for r in res["log"].rows)
    s.loc[s.index[-1], "spend_usd"] = "n/a"
    write(s, d, "marketing_spend")
    try:
        run(d, SETTINGS, with_sensitivity=False)
        raise AssertionError("did not stop on an unreadable spend value")
    except ToolError as e:
        assert "not numbers" in str(e)
        return f"identical impact (${res['impact']:,.0f}), rule logged; 'n/a' stopped with \"{str(e)[:90]}...\""


@test("12. A whole month in a new date format stops with guidance; adding the pattern in settings fixes it")
def t12():
    d = copy_data()
    o = read(d, "orders")
    dt = pd.to_datetime(o["order_date"], format="mixed")
    octo = dt >= "2026-10-01"
    o.loc[octo, "order_date"] = dt[octo].dt.strftime("%d.%m.%Y")
    write(o, d, "orders")
    try:
        run(d, SETTINGS, with_sensitivity=False)
        raise AssertionError("did not stop")
    except ToolError as e:
        assert "date_formats" in str(e)
        msg = str(e)
    res = run(d, settings(date_formats=SETTINGS["date_formats"] + ["DD.MM.YYYY"]), with_sensitivity=False)
    assert abs(res["impact"] - base()["impact"]) < 1e-6
    return f"stopped: \"{msg[:100]}...\"; with 'DD.MM.YYYY' added: identical impact (${res['impact']:,.0f})"


@test("13. The payback window changes the answer, and no channel grows without paying back")
def t13():
    short = run(DATA, settings(payback_window_months=3), with_sensitivity=False)
    assert short["impact"] < base()["impact"]
    p = base()["plan"]
    grew = p[p["recommended_spend"] > p["baseline_spend"] + 1]
    assert (grew["cost_at_this_years_spend"] <= grew["value_in_window"] * 1.01).all()
    return (f"3-month window: ${short['impact']:,.0f} vs ${base()['impact']:,.0f} at 12 months; all {len(grew)} "
            "channel-months that grow pay back within the window")


@test("14. Guardrails hold in all 12 months, also under a $12M budget override")
def t14():
    out = []
    for res in [base(), run(DATA, settings(acquisition_budget_annual=12_000_000), with_sensitivity=False)]:
        s, p, rec = res["settings"], res["plan"], res["record_month"]
        g, r = s["max_change_vs_last_year"], s["max_above_record_month"]
        for row in p.itertuples():
            hi = max(row.baseline_spend, min(row.baseline_spend * (1 + g), rec[row.channel] * (1 + r)))
            assert row.recommended_spend >= row.baseline_spend * (1 - g) - 1, row
            assert row.recommended_spend <= hi + 1, row
            if row.reason == "low_confidence":
                assert abs(row.recommended_spend - row.baseline_spend) < 1, row
        assert budget_holds(res)
        out.append(f"${res['budget']:,.0f}: {len(p)} channel-months within limits")
    return "; ".join(out)


@test("15. Checks against finance are computed at finance's month, whatever month the data runs to")
def t15():
    a = run(DATA, settings(finance_as_of_month="2026-09"), with_sensitivity=False)["reconciliation"]
    d = copy_data()
    o = read(d, "orders")
    write(o[pd.to_datetime(o["order_date"], format="mixed") <= "2026-09-30"], d, "orders")
    s = read(d, "marketing_spend")
    write(s[s["month"] <= "2026-09"], d, "marketing_spend")
    b = run(d, settings(finance_as_of_month="2026-09"), with_sensitivity=False)["reconciliation"]
    assert np.allclose(a["tool"].astype(float), b["tool"].astype(float), equal_nan=True)
    return f"October data checked as of 2026-09 = September data: all {len(a)} figures identical"


@test("16. Back-test: projected values land within 10% of what happened")
def t16():
    bt = base()["backtest"]
    worst = bt["error"].abs().max()
    assert len(bt) and worst < 0.10, bt
    return f"{len(bt)} projections (full 24 months and recent customers), worst error {worst:.1%}"


@test("17. Cohort LTV is filled for every channel and cohort, with labels and 95% ranges")
def t17():
    cl = base()["cohort_ltv"]
    sub = cl[cl["channel"].isin(PAID + ["All"])]
    assert sub["ltv_24m_projected"].notna().all()
    assert set(sub["projection"]) <= {"observed", "partly projected", "projected"}
    y25 = cl[(cl["channel"] == "All") & (cl["cohort"] == 2025)].iloc[0]
    assert pd.notna(y25["ltv_12m"]) and pd.notna(y25["ltv_12m_plus_minus"])
    return (f"{len(cl)} channel x cohort rows; 2025 customers: observed 12-month LTV ${y25['ltv_12m']:,.2f} "
            f"± ${y25['ltv_12m_plus_minus']:,.2f}, projected 24-month ${y25['ltv_24m_projected']:,.2f}")


@test("18. A channel with no spend in a month last year: held at $0 that month, no broken numbers")
def t18():
    d = copy_data()
    s = read(d, "marketing_spend")
    s.loc[(s["month"] == "2025-11") & (s["channel"] == "Referral Program"), "spend_usd"] = "0"
    write(s, d, "marketing_spend")
    res = run(d, SETTINGS, with_sensitivity=False)
    p = res["plan"]
    cols = ["recommended_spend", "baseline_customers", "recommended_customers", "contribution_change"]
    assert np.isfinite(p[cols].to_numpy(dtype=float)).all()
    nov = p[(p["month"] == "2026-11") & (p["channel"] == "Referral")].iloc[0]
    assert nov["recommended_spend"] == 0 and nov["reason"] == "no_spend_last_year"
    return "Referral held at $0 in Nov 2026; every figure in the 12-month plan is a real number"


@test("19. Settings: save then load gives the same values; an older settings file is brought up to date")
def t19():
    d = temp_dir()
    path = d / "settings.xlsx"
    shutil.copyfile(HERE / "settings.xlsx", path)
    s1 = load_settings(path)
    save_settings(path, s1)
    s2 = load_settings(path)
    for k in s1:
        if k != "settings_notes":
            assert s1[k] == s2[k] or (s1[k] != s1[k] and s2[k] != s2[k]), (k, s1[k], s2[k])
    import openpyxl
    old = d / "old_settings.xlsx"
    shutil.copyfile(HERE / "settings.xlsx", old)
    wb = openpyxl.load_workbook(old)
    ws = wb["assumptions"]
    for row in list(ws.iter_rows(min_row=2)):
        if row[0].value == "max_change_vs_last_year":
            row[0].value = "max_monthly_change"
    for i in range(ws.max_row, 1, -1):
        if ws.cell(i, 1).value in ("max_above_record_month", "hold_low_confidence_channels", "finance_as_of_month",
                                   "date_formats"):
            ws.delete_rows(i)
    del wb["note_themes"]
    wb.save(old)
    s3 = load_settings(old)
    assert s3["settings_notes"] and s3["max_change_vs_last_year"] == SETTINGS["max_change_vs_last_year"]
    save_settings(old, s3)
    s4 = load_settings(old)
    assert not s4["settings_notes"] and "note_themes" in openpyxl.load_workbook(old).sheetnames
    return (f"round trip identical ({len(s1) - 1} settings); an old file ({len(s3['settings_notes'])} rows missing, "
            "old limit name) loads with defaults and is completed on save")


@test("20. Checks on the newest month catch an incomplete export, a renamed spend line and missing customers")
def t20():
    d = copy_data()
    o = read(d, "orders")
    dt = pd.to_datetime(o["order_date"], format="mixed")
    o = o[dt <= "2026-10-15"]
    write(o, d, "orders")
    s = read(d, "marketing_spend")
    s.loc[(s["month"] == "2026-10") & (s["channel"] == "Google Ads"), "channel"] = "Google Search"
    write(s, d, "marketing_spend")
    od = pd.to_datetime(o["order_date"], format="mixed")
    first = od.groupby(o["customer_id"].str.strip()).min()
    new_oct = set(first[first >= "2026-10-01"].index)
    c = read(d, "customers")
    write(c[~c["customer_id"].str.strip().isin(new_oct)], d, "customers")
    res = run(d, SETTINGS, with_sensitivity=False)
    w = " | ".join(res["log"].warnings)
    for needle in ["looks incomplete", "Paid Search has no spend in 2026-10", "no record in customers.csv"]:
        assert needle in w, (needle, res["log"].warnings)
    return "warned about the incomplete month, the renamed Google Ads line and the missing customer records"


@test("21. The calendar-year view adds up to the headline impact")
def t21():
    res = base()
    cy = res["impact_by_calendar_year"]
    assert abs(cy["total"].sum() - res["impact"]) < 1.0
    return "; ".join(f"{int(r.year)} ${r.total:,.0f}" for r in cy.itertuples()) + f" = ${res['impact']:,.0f}"


@test("22. Launchers and packaging: line endings, executable bits, clean zip")
def t22():
    for f in ["run_windows.bat", "run_report_only_windows.bat"]:
        raw = (HERE / f).read_bytes()
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"{f} must use Windows line endings"
    for f in ["run_mac.command", "run_report_only_mac.command"]:
        assert b"\r" not in (HERE / f).read_bytes(), f"{f} must use Mac/Unix line endings"
        if os.name == "posix":
            assert os.access(HERE / f, os.X_OK), f"{f} is not executable"
    z = temp_dir() / "planner.zip"
    subprocess.run([sys.executable, str(HERE / "make_zip.py"), str(z)], check=True, capture_output=True)
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        modes = {i.filename: (i.external_attr >> 16) & 0o777 for i in zf.infolist()}
    assert not [n for n in names if "/.venv/" in n or "__pycache__" in n or n.endswith(".DS_Store")]
    assert all(m == 0o755 for n, m in modes.items() if n.endswith(".command"))
    return f"line endings and executable bits correct; make_zip.py packs {len(names)} files without .venv or caches"


@test("23. The app opens and renders every tab (Streamlit test runner)")
def t23():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(HERE / "app.py"), default_timeout=300)
    at.query_params["autorun"] = "1"
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    labels = [e.label for e in at.expander]
    for needle in ["Guardrails we chose and why", "Why repeat changed", "Service issues", "Detail with"]:
        assert any(needle in x for x in labels), (needle, labels)
    return f"{len(at.tabs)} tabs, {len(at.expander)} expanders, {len(at.dataframe)} tables; no errors"


if __name__ == "__main__":
    for d in TEMP:
        shutil.rmtree(d, ignore_errors=True)
    passed = sum(r[1] == "PASS" for r in results)
    try:
        import streamlit
        st_version = streamlit.__version__
    except ImportError:
        st_version = "not installed"
    env = (f"Python {platform.python_version()} on {platform.system()} {platform.machine()}; pandas {pd.__version__}, "
           f"numpy {np.__version__}, streamlit {st_version}")
    lines = ["# Test report", "", f"{passed} of {len(results)} scenario tests passed.", "", f"Environment: {env}.", "",
             "| Test | Result | What happened | Seconds |", "| --- | --- | --- | --- |"]
    lines += [f"| {n} | {r} | {d.replace('|', '/')} | {t:.0f} |" for n, r, d, t in results]
    lines += ["", "Other environments and manual checks: see ENVIRONMENTS.md in this folder."]
    (HERE / "tests" / "TEST_REPORT.md").write_text("\n".join(lines) + "\n")
    print(f"\n{passed} of {len(results)} passed. Report: tests/TEST_REPORT.md")
    sys.exit(0 if passed == len(results) else 1)
