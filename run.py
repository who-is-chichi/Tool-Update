"""Command-line version of the tool. Same results as the app.

    python run.py                          # uses ./data, ./settings.xlsx, writes to ./output
    python run.py --data path/to/new_month_files
"""
import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tool.common import ToolError  # noqa: E402
from tool.metrics import mi_name  # noqa: E402
from tool.pipeline import run  # noqa: E402
from tool.report import write_report  # noqa: E402


def _pct(x):
    return "n/a" if x != x else f"{x:+.0%}"


def main():
    ap = argparse.ArgumentParser(description="Cotopaxi DTC channel planner")
    ap.add_argument("--data", default=str(HERE / "data"), help="folder with the four exported CSV files")
    ap.add_argument("--settings", default=str(HERE / "settings.xlsx"), help="settings file")
    ap.add_argument("--out", default=str(HERE / "output"), help="folder for the report")
    a = ap.parse_args()
    t = time.time()
    try:
        res = run(a.data, a.settings)
        path = write_report(res, a.out)
    except ToolError as e:
        print(f"\nThe tool stopped: {e}\n")
        return 1
    except Exception as e:  # noqa: BLE001
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        log = out / "error_log.txt"
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n==== {datetime.now():%Y-%m-%d %H:%M:%S}\n{traceback.format_exc()}")
        print(f"\nThe tool hit an unexpected problem ({type(e).__name__}: {e}). The details are saved in {log}.\n")
        return 1

    nm = res["next_month"]
    m1 = int(nm["m"].iloc[0])
    H = res["settings"]["ltv_horizon_months"]
    ly, last = mi_name(m1 - 12), mi_name(m1 - 1)
    print(f"\nData through {mi_name(m1 - 1, '%B %Y')}. Recommendation for {mi_name(m1, '%B %Y')}:\n")
    for r in nm.itertuples():
        arrow = "UP  " if r.change > 0.5 else ("DOWN" if r.change < -0.5 else "SAME")
        print(f"  {r.channel:<24} ${r.baseline_spend:>11,.0f} -> ${r.recommended_spend:>11,.0f}  {arrow} "
              f"{_pct(r.change_pct)} vs {ly}, {_pct(r.vs_last_month)} vs {last}")
        print(f"  {'':<24} {r.why}")
    for r in res["next_month_held_flat"].itertuples():
        print(f"  {r.channel:<24} ${r.spend:>11,.0f}    held flat (outside the acquisition budget)")
    print(f"\nNext 12 months on the tool's split vs this year's: {res['impact']:+,.0f} USD of {H}-month contribution "
          f"from the customers gained and lost, same budget (${res['budget']:,.0f}).")
    cy = res["impact_by_calendar_year"]
    if len(cy):
        print("  Lands by calendar year: " + ", ".join(f"{int(r.year)} {r.total:+,.0f}" for r in cy.itertuples()))
    for w in res["log"].warnings:
        print(f"  WARNING: {w}")
    print(f"\nReport: {path}\nDone in {time.time() - t:.0f} seconds.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
