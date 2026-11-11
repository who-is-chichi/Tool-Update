# Test report

23 of 23 scenario tests passed.

Environment: Python 3.14.6 on Darwin arm64; pandas 3.1.1, numpy 2.5.4, streamlit 1.67.0.

| Test | Result | What happened | Seconds |
| --- | --- | --- | --- |
| 1. Full run on the real files; every check against finance matches | PASS | 12/12 checks match (as of 2026-10) plus 1 explained line; budget $9,757,540 held in every month; 12-month impact $381,644; Excel report written (21 sheets) | 9 |
| 2. New month: run on data cut at September, then with October added, no code change | PASS | September run -> plan for 2026-10 (budget $9,627,922); October run -> plan for 2026-11 (budget $9,757,540); budget rolled forward automatically | 8 |
| 3. New channel label ('TikTok') appears in customers.csv | PASS | ran to completion; warning: "customers.csv: new channel name 'TikTok' on 398 customers is not in the channel mapping. T..."; customers shown as 'Unmapped' | 7 |
| 4. New spend line ('TikTok Ads') appears in marketing_spend.csv | PASS | held flat, shown with Brand and Email & SMS, left out of the reallocation, with a warning | 7 |
| 5. Columns reordered and an extra column added | PASS | identical results to the original files (impact $381,644); extra column logged and ignored | 9 |
| 6. A third date format mixed in: a few rows (skipped with a warning) vs many rows (clear stop) | PASS | 300 rows: skipped with a warning. 5% of rows: stopped with "orders.csv: 25,281 values in 'order_date' (5.1%) are not dates in a known format, e.g. '31.12.2025', '2025.01...." | 11 |
| 7. Orders and spend end in different months | PASS | used September, the last month both files cover, and said so | 7 |
| 8. A required column is missing | PASS | stopped with a plain message: "orders.csv is missing the column(s) ['order_date']. Check the export." | 2 |
| 9. Assumptions changed without touching code | PASS | margin 55%: impact $291,670 (< $381,644); 10% limit respected in all 12 months; $12M budget override used | 20 |
| 10. File names with prefixes or browser copy numbers, and the same result every run | PASS | found 'orders (1).csv' and '2026-10-customers.csv' etc.; identical impact on both runs ($381,644) | 7 |
| 11. Spend written as '$1,234.56' gives the same result; an unreadable spend value stops the run | PASS | identical impact ($381,644), rule logged; 'n/a' stopped with "marketing_spend.csv: 1 spend value(s) are not numbers, e.g. 2026-10 'Referral Program': 'n..." | 9 |
| 12. A whole month in a new date format stops with guidance; adding the pattern in settings fixes it | PASS | stopped: "orders.csv: 14,807 values in 'order_date' (3.0%) are not dates in a known format, e.g. '01.10.2026',..."; with 'DD.MM.YYYY' added: identical impact ($381,644) | 10 |
| 13. The payback window changes the answer, and no channel grows without paying back | PASS | 3-month window: $194,664 vs $381,644 at 12 months; all 24 channel-months that grow pay back within the window | 7 |
| 14. Guardrails hold in all 12 months, also under a $12M budget override | PASS | $9,757,540: 48 channel-months within limits; $12,000,000: 48 channel-months within limits | 6 |
| 15. Checks against finance are computed at finance's month, whatever month the data runs to | PASS | October data checked as of 2026-09 = September data: all 13 figures identical | 17 |
| 16. Back-test: projected values land within 10% of what happened | PASS | 21 projections (full 24 months and recent customers), worst error 4.9% | 0 |
| 17. Cohort LTV is filled for every channel and cohort, with labels and 95% ranges | PASS | 32 channel x cohort rows; 2025 customers: observed 12-month LTV $73.97 ± $0.55, projected 24-month $94.02 | 0 |
| 18. A channel with no spend in a month last year: held at $0 that month, no broken numbers | PASS | Referral held at $0 in Nov 2026; every figure in the 12-month plan is a real number | 7 |
| 19. Settings: save then load gives the same values; an older settings file is brought up to date | PASS | round trip identical (23 settings); an old file (4 rows missing, old limit name) loads with defaults and is completed on save | 0 |
| 20. Checks on the newest month catch an incomplete export, a renamed spend line and missing customers | PASS | warned about the incomplete month, the renamed Google Ads line and the missing customer records | 9 |
| 21. The calendar-year view adds up to the headline impact | PASS | 2026 $25,862; 2027 $208,868; 2028 $114,311; 2029 $32,604 = $381,644 | 0 |
| 22. Launchers and packaging: line endings, executable bits, clean zip | PASS | line endings and executable bits correct; make_zip.py packs 33 files without .venv or caches | 2 |
| 23. The app opens and renders every tab (Streamlit test runner) | PASS | 8 tabs, 7 expanders, 19 tables; no errors | 10 |

Other environments and manual checks: see ENVIRONMENTS.md in this folder.
