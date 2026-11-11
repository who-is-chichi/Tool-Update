# Code walkthrough (plain English)

About 3,200 lines of Python: about 2,000 for the calculations in `tool/`, 600 for the app screens, 170 for the
launchers and 460 for the tests. Data flows one way:

**files → `clean.py` → `metrics.py` → `recommend.py` → `pipeline.py` → `report.py` / `app.py`**

`pipeline.py` calls everything in order. `run.py` (command line) and `app.py` (screens) both call `pipeline.run()`,
so they always produce the same numbers. Figures below are from the October 2026 data.

---

## `tool/constants.py`: model constants
The technical choices that are not business assumptions, each named and explained: 18 months of history before a
response curve is fitted (30 for month-of-year effects), the response b kept between 0.05 and 1, the low-confidence
thresholds, 400 allocation steps, the check tolerances, and the thresholds of the latest-month checks.
Business assumptions all live in `settings.xlsx`.

## `tool/common.py`: shared pieces
- **`ToolError`**: the only error the user sees, for problems they must fix. The app and `run.py` show it as plain
  text; anything unexpected is caught too, explained in one line and logged to `output/error_log.txt`.
- **`Log`**: `rule()` records each cleaning step (file, rule, rows affected, action, why); `warn()` records what the
  user should know. Both appear in the app and the Excel report.
- **`load_settings()` / `check_settings()`**: read `settings.xlsx` and check it (numbers, ranges, date patterns,
  finance's month). Settings added in this version have built-in defaults, so an older settings file still works and
  the run says which rows were missing. **`save_settings()`** writes the app's edits back and adds missing rows.
- **Date patterns**: `DD.MM.YYYY` is turned into a computer date format; after `HH`, `MM` means minutes. Each pattern
  is checked when settings load, so a typo stops with a plain message instead of a crash.
- **`DEFAULT_THEMES`**: the customer-service note themes, used if `settings.xlsx` has no `note_themes` sheet.

## `tool/clean.py`: step 1, read and clean the four files
- **`find_file()`**: finds `orders.csv` with a prefix (`2026-11-orders.csv`) or a browser copy number
  (`orders (1).csv`); ignores hidden files.
- **`read()`**: every column as text (nothing silently converted), columns matched **by name**; reads files saved by
  Excel (Windows text encoding, semicolons). Stops if a required column is missing.
- **`to_amount()`**: reads `$1,234.56`, `1 234` and `(12.50)`; a decimal comma like `12,50` stays unreadable rather
  than being guessed as 1,250.
- **`parse_dates()`**: tries each pattern in `date_formats` in turn and logs how many rows used each. Rows matching
  none are skipped with a warning; above 1% the tool stops and shows examples and the setting to add.
- **`clean_orders()`**, in order: trim spaces in IDs (2,451 rows) → remove `TEST` customers (610) → keep `order` and
  `refund` rows (34,149 refunds, never counted as orders) → read amounts → parse dates (216,651 rows written MM/DD/YYYY
  by the legacy ERP) → drop exact duplicates (4,945) → flag orders whose total ≠ subtotal − discount + shipping (none).
- **`clean_customers()`**: trim IDs → remove 40 tests → keep the first of 1,350 duplicate IDs (all agree on date and
  channel) → parse dates → map 18 labels to 7 channels. **A label not in the mapping becomes "Unmapped" with a
  warning; it never guesses.**
- **`clean_spend()`**: reads amounts (an unreadable spend value **stops** the run, because $0 would distort CAC; a
  blank one is $0 with a warning), accepts months re-saved by Excel (`Oct-26`), removes exact duplicate rows, maps each
  line to a channel and a treatment (`reallocate`, `hold_flat`, `retention`). Facebook/Instagram (to Aug 2024) and Meta
  (from Sep 2024) both map to Paid Social. An unknown line is held flat with a warning.

## `tool/metrics.py`: step 2, the numbers the brief asks for
- **Month index**: months are one integer (`year×12 + month−1`), so "months since first order" is subtraction.
- **`prepare()`**: the as-of month is the last month all files cover. **A customer starts on their first order**
  (`acquisition_date` is when the record was created and is unreliable: 6,087 are later than the first order, 583 are
  in the future). 4,843 ordering customers have no customer record: kept as Unattributed. 32,217 customers acquired
  before 2023 are left out (they aren't new). **Contribution** of a row: order = margin × (subtotal − discount) −
  fulfillment − payment rate × order total; refund = −(refund × margin) if goods are resold, −refund if not.
- **The 25% sample**: rates and values per customer need no scaling; customer counts and totals are divided by the
  sample rate. Finance's revenue (within 1%) and blended CAC (within 2%) confirm it.
- **`repeat_rates()`**: the brief's definition (an order in months 12(n−1) to 12n after the first, counting customers
  whose full year has passed), with whole-business counts, sample counts and a 95% range.
- **`repeat_decomposition()`**: splits the change in all-customer repeat into **channel mix** and **within-channel**
  change, using the average of both years' shares and rates so the parts add up exactly. Year-1 repeat, 2023 → 2025
  customers: 42.2% → 31.4% = −5.6 points from mix (Paid Social grew from 21% of 2023's new customers to 43% of 2025's)
  and −5.2 points within channels (concentrated in 2025, after the January 2025 move to Shopify).
- **`early_read()`**: share buying again within 90 days, only for quarters whose customers have all had 90 days.
- **`value_curves()`** (chain-ladder): growth from month k to k+1 is measured on the latest 12 start months that have
  reached month k+1; a recent customer's value = what they've earned so far × the remaining growth steps. Today's value
  = the average over customers acquired in the last 12 months. Ages with fewer than 100 sampled customers use all
  customers' growth.
- **`cohort_ltv()`**: LTV by channel and cohort year. Observed at 12/24/36 months over the customers whose window has
  passed, with a 95% range; projected 24-month LTV for every cohort, labelled observed / partly projected / projected.
- **`backtest()`**: rebuilds the projection with only the data available at an earlier date. A: the latest customers
  with the full 24 months, from their first 3 or 12 months. B: customers acquired June–November 2025, from 3 months to
  12. Worst error 4.9%.
- **`cac_tables()`**: channel CAC = reallocated spend ÷ (new customers ÷ sample rate); blended CAC = all spend except
  retention ÷ all new customers, which matches finance.
- **`payback_by_cohort()`**: the first month a cohort's contribution covers that year's CAC; each month of age uses
  the customers who have reached it.
- **`reconciliation()`**: one row per figure in `finance_reference`, computed **as of finance's month**
  (`finance_as_of_month`), so the checks compare like with like on any new month of data. The "marketing spend = 24%
  of revenue" figure is shown as "definition differs": the spend file is $12.6M a year, DTC only, without wholesale
  trade marketing and co-op.
- **`service_issues()`**: notes sorted into themes by keyword (`note_themes`), per 1,000 orders by quarter and by
  channel. Between Q3 2025 and Q3 2026: fit and sizing +39%, promo code problems +59%, late shipments +69%, referral
  credits not posted +124%.
- **`latest_month_checks()`**: warns when the newest month looks incomplete, a reallocated channel has no spend
  (renamed line?), many new customers have no record, or existing customers suddenly order far less than their trend
  (customer IDs changed?).

## `tool/recommend.py`: step 3, next month's split
- **Response curve** per reallocated channel: `new customers = A × spend^b`, optionally × month-of-year effects and a
  trend. **b** is the key number: b = 0.8 means 10% more spend buys about 8% more customers.
- **`fit_curves()`**: tries four versions (season, season+trend, trend, none); for each of the last 12 months it fits
  on the months before and predicts that month, and keeps the version with the smallest average error. Low confidence
  when b's error is large or spend has barely varied: Referral (b = 0.27 ± 0.09, spend varied 20%).
- **`allocate()`**, the decision rule for one month:
  - Budget = this year's spend in that month (scaled if a budget is typed in), so the total is fixed.
  - Every channel starts at 75% of this year's spend; the rest is handed out in 400 steps, each to the channel that
    buys the most 24-month contribution per dollar.
  - Up to this year's spend there is no test. **Above it, a channel only gets money while its next customer pays back
    within the payback window.**
  - Ceiling: 125% of this year's spend, and never more than 10% above the channel's record month.
  - Held at this year's spend: low-confidence channels (setting), channels with no curve or value, and channels with
    no spend in that month last year.
  - Each channel gets a reason (grew, capped, cut, held).
  - Customers at a new spend level = this year's customers × (new spend ÷ this year's spend)^b.
- **`plan()`**: the same for each of the next 12 months; customers gained or lost × 24-month value = impact.
- **`by_calendar_year()`**: where that 24-month value lands: 2026 $26k, 2027 $209k, 2028 $114k, 2029 $33k.
- **`explain()`**: the one-line reason per channel shown in the app.

## `tool/pipeline.py`: runs everything
Calls the steps in order; computes the finance checks at finance's month; builds next month's table (with "vs last
month" and the held-flat Brand and Email & SMS rows), the scorecard and the warnings; runs the **sensitivity** table:
one assumption at a time (returns, margin ±3 points, fulfillment +$2, payback window 6 or 24 months, change limit 10%
or 50%, record cap off or 0%, low-confidence channels moved, response ±20%, value from the latest full cohorts,
unattributed customers shared across paid channels), each rerunning the 12-month plan, with whether every channel still
moves the same way.

## `tool/report.py`: step 4, the Excel report
21 sheets with titles, notes and number formats. The **Summary** sheet holds the headline, the calendar-year split, the
checks, and **Guardrails we chose and why**. The **12-month plan** sheet is the input to the business-case workbook.
The app builds the report in memory; `run.py` saves it, with the cleaning log as CSV, in `output/`.

## `app.py`: the screens
Sidebar: files and assumptions (edits apply to this run; "Save these as the defaults" writes `settings.xlsx`).
Out-of-range settings are pulled into range with a note. Main page: headline cards, next month's split with reasons and
"vs last month", then tabs. "Guardrails we chose and why", "Why repeat changed", "Service issues" and the ± detail
tables are collapsed until clicked. Each channel has one colour everywhere (a colour-blind-safe set, assigned by
channel, never by rank). Opening the app with `?autorun=1` runs it straight away on the data folder.

## Launchers, `launch.py`, `run.py`, `make_zip.py`
- `run_mac.command` / `run_windows.bat`: check that Python 3.9+ really runs (a new Mac's `python3` and Windows'
  `python` can be placeholders), check the planner's own Python environment (`.venv`) on every start and rebuild it if
  it is missing, half-installed or copied from another computer, then call `launch.py`.
- `launch.py`: if the planner is already running, opens the browser; otherwise starts it, waits until it answers and
  opens the browser.
- `run.py`: `python run.py --data <folder>` prints the recommendation and warnings and writes the Excel report to
  `output/`. The backup launchers use it.
- `make_zip.py`: builds a clean zip to send on, without `.venv`, caches or old reports.

## `tests/run_tests.py`: 23 scenario tests
Real data matches finance · new month (September → October) · new channel label · new spend line · shuffled and extra
columns · unknown date format (a few rows vs 5%) · files ending in different months · missing column · assumptions
changed · file names with prefixes or copy numbers · `$1,234.56` spend and unreadable spend · a whole month in a new date
format, fixed from settings · payback window matters · guardrails hold (also with a $12M budget) · checks at finance's
month · back-test within 10% · cohort LTV filled · a month with no spend last year · settings save/load and an old
settings file · latest-month checks · calendar years add up · launchers and zip · the app renders. Writes
`tests/TEST_REPORT.md`; `tests/ENVIRONMENTS.md` lists the environments tested.

---

### Likely "explain this line" questions
- *Why read everything as text?* So pandas never silently changes an ID like `C00012` or misreads a date.
- *Why month-index integers?* Month arithmetic without calendar edge cases.
- *Why does the payback test only apply above this year's spend?* The budget is fixed, so all of it must be placed.
  Every channel can always go back to this year's level; payback decides who may **grow** beyond it. With a 3-month
  window only Affiliate can grow; at 12 months Paid Search can too; Paid Social can't (next customer $71, earns $58 in
  12 months).
- *Why 75–125% and the record-month cap?* Cotopaxi has no limits; they are ours to set. The response curves are only
  reliable near spend levels seen in the data. At +25%, Paid Search would reach $297k against a record of $253k.
- *Why is Referral held?* Its spend has varied only 20% around its average, so b = 0.27 ± 0.09: its next customer could
  cost anywhere from about $45 to $260. We don't move money on a curve we can't trust.
- *Why chain-ladder?* Repeat behaviour changed in 2025. Growing recent customers with the pattern of customers acquired
  before the change overstated their value by 1–5%; chain-ladder uses the latest customers who reached each age.
- *Why anchor customers at this year's spend (`n0 × (x/base)^b`)?* The forecast at this year's spend then equals the
  model's own prediction, and a different b changes only the slope, which keeps the sensitivity test fair.
- *Why 400 steps?* Fine enough that the answer doesn't change at 1,000 steps, and fast (about $2,400 a step in November).
- *Why the model contest?* No single curve fits every channel best (search has a trend, referral is seasonal);
  picking by forecast error is objective and reruns every month.
- *Did the move to Shopify (January 2025) break customer IDs?* We checked: customers from before the switch kept
  ordering through January 2025 at the usual rate, and "new" customers didn't jump. Duplicate-account notes come from
  new customers about a month after their first order (about 0.6% of them). The 2025 drop in repeat is real, and a
  monthly check now warns if a break like that appears.
- *Why do the finance checks use October on November's data?* Finance's figures are as of 31 October 2026; comparing
  them with November figures would compare different dates.
