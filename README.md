# DTC Channel Planner

Each month this tool tells the growth team how to split the DTC acquisition budget across channels,
based on what each channel's customers are worth over their first 24 months, not just what they cost.

It reads the four files Cotopaxi exports every month, cleans them, scores every channel and recommends
next month's spend. The total budget stays at this year's level; only the split changes.

---

## One-time setup (5 minutes)

You need **Python 3.9 or newer**. To check on a Mac, open **Terminal** and type `python3 --version`.
- If you see a version number, you're set.
- If a window offers to install the **command line developer tools**, click **Install** and wait; that includes Python.
- Otherwise install Python from [python.org/downloads](https://www.python.org/downloads/) and accept the defaults.

**Mac**
1. Unzip the folder anywhere, for example in Documents.
2. Double-click **`run_mac.command`**. If macOS blocks it the first time:
   - **macOS 15 or later** ("Apple could not verify..."): click **Done**, open **System Settings › Privacy & Security**,
     scroll down, click **Open Anyway** next to run_mac.command, confirm, then double-click it again.
   - **macOS 14 or earlier**: right-click the file › **Open** › **Open**.
   - If Terminal asks to access files in Documents or Desktop, click **Allow**.
   - Still blocked? Open **Terminal**, type `bash ` (with a space), drag `run_mac.command` into the window, press Enter.
3. The first time, it installs what it needs (about 2 minutes, needs internet). Your browser then opens the planner.

**Windows**
1. Install Python from [python.org/downloads](https://www.python.org/downloads/). On the installer's first screen,
   tick **"Add python.exe to PATH"**.
2. Unzip the folder anywhere and double-click **`run_windows.bat`**. If Windows shows "Windows protected your PC",
   click **More info** › **Run anyway**.
3. The first time, it installs what it needs (about 2 minutes, needs internet). Your browser then opens the planner.

Keep the black terminal window open while you use the planner. Close it when you're done.
If setup is interrupted (for example the internet drops), just double-click the launcher again: it repairs itself.

---

## Every month (3 steps)

1. **Add the files.** Put this month's `orders.csv`, `customers.csv`, `marketing_spend.csv` and `cs_notes.csv`
   in the **`data`** folder, replacing last month's. Or choose **Upload them here** in the sidebar and drop them in.
   Names may carry a prefix (`2026-11-orders.csv`) or a browser copy number (`orders (1).csv`).
2. **Check the assumptions** in the sidebar: margin, costs, payback window, guardrails and the budget. No code to edit.
3. Click **Run the planner** (about 20 seconds).

You get:
- **Where the money should go next month**: this year's split against the recommendation for each channel, the change
  against the same month last year and against last month, and the reason. Brand and Email & SMS are shown, held flat.
- **Guardrails we chose and why** (click to open): the payback target, change limit and caps, with the reason for each.
- **Scorecard**: new customers, repeat, value per customer, CAC, the cost of the next customer, and payback.
- **Repeat & cohorts**: repeat by channel, cohort and year. Click to open **Why repeat changed** (channel mix against
  repeat within channels) and **Service issues** (customer-service notes by theme).
- **Value & payback**: value per customer, LTV by channel and cohort, payback; **12-month impact**, also by calendar year;
  **Sensitivity**: how much each assumption moves the answer.
- **Checks vs finance**: the tool's figures next to finance's, at the same month, so you know the data is right.
- **Data cleaning**: everything the tool changed in the files, and why.
- **Download the full Excel report** at the bottom of the page.

---

## How it decides

1. **Value of a customer** = contribution over the first 24 months after their first order: 59% margin, less $9
   fulfillment and 2.6% payment costs, net of refunds (the defaults in settings.xlsx). Recent customers are projected
   month by month from the latest customers who reached each age, and the projection is checked against what really
   happened (within 5% as of October 2026).
2. **CAC** = a channel's spend ÷ the new customers it brought. Orders and customers are a 25% sample, so customer counts
   are multiplied by 4; rates and values per customer are used as measured.
3. From the full spend history (46 months as of October 2026) the tool learns how strongly each channel's new customers
   respond to more spend, which gives **the cost of the next customer**.
4. **The budget is fixed at this year's level** and each month gets this year's amount for that month. Money moves, in
   small steps, to the channel that buys the most 24-month value per dollar, within these guardrails:
   - **Payback target, 12 months**: a channel gets more than this year's spend only while its next customer pays back
     within 12 months. Cotopaxi has no target; 12 months keeps cash recovery inside one budget year.
   - **Change limit, 75% to 125%** of the channel's spend in the same month this year. The 75% floor is the channel
     minimum. It keeps the plan near spend levels the data has seen; the planner reruns every month.
   - **Record-month cap**: never more than 10% above a channel's highest month in the data. Beyond that, how customers
     respond to spend is a guess.
   - **Uncertain response**: a channel whose response to spend can't be measured reliably is held at this year's spend.
5. **Brand and Email & SMS are held flat**: they sit outside the acquisition budget, and the data can't show which
   customers they bring.

Every guardrail is a setting the team can change.

---

## Changing assumptions or channel names

- **For this run only:** change them in the sidebar.
- **To keep them:** click **Save these as the defaults**. They're stored in `settings.xlsx` (close it in Excel first).
- You can also edit **`settings.xlsx`** directly in Excel:
  - `assumptions`: every number the tool uses, each with a plain description: margin, costs, payback window, change
    limit, record-month cap, holding uncertain channels, the month finance's figures are from, and the date formats.
  - `channel_mapping`: how each label in customers.csv maps to a channel. Add a row when a new label appears.
  - `spend_mapping`: how each line in marketing_spend.csv maps to a channel, and whether it is reallocated,
    held flat (brand) or retention (email & SMS).
  - `finance_reference`: finance's figures, used for the checks.
  - `note_themes`: the keywords that sort customer-service notes into themes.

---

## If something goes wrong

The planner never guesses quietly. It either handles a surprise and tells you, or stops and says why.

| You see | What it means | What to do |
|---|---|---|
| "New channel name 'X'… Treated as 'Unmapped'" | customers.csv has a label the tool hasn't seen | Add it to `channel_mapping`, then run again |
| "New spend line 'X'… Held flat" | marketing_spend.csv has a new line | Add it to `spend_mapping` if it should be reallocated |
| "X has no spend in (month) after spending in earlier months" | A spend line was probably renamed | Add the new name to `spend_mapping` |
| "Orders run to … and spend runs to …" | The files end in different months | The tool uses the last month both cover. Check the export |
| "The latest month looks incomplete" | The orders stop days before the month ends | Re-export the files |
| "…new customers have no record in customers.csv" | customers.csv is missing recent customers | Re-export customers.csv |
| "Existing customers ordered much less … than their recent trend" | Customer IDs may have changed in the export | Check with whoever exports the files |
| "N rows had unreadable dates" | A few dates are in an unknown format | Fine if few. The tool stops if more than 1% are unreadable |
| "…are not dates in a known format, e.g. '31.10.2026'" | The export changed its date format | Add the pattern (e.g. `DD.MM.YYYY`) to date formats (sidebar › More, or `settings.xlsx`), run again |
| "…spend value(s) are not numbers" | A spend amount can't be read | Fix the export. Amounts like `$1,234.56` are read automatically |
| "…capped at 10% above its record month" or "…held at this year's spend" | A guardrail is working | Nothing; it's information |
| "The planner stopped: … is missing the column …" | A file is missing a column | Re-export the file |
| "settings.xlsx … is open in another program" | Excel has the file open | Close it in Excel, click Save again |
| "The planner hit an unexpected problem" | Something the tool didn't expect | Details are in `output/error_log.txt`. Use the backup below |
| "Setup failed" | The first-time install was interrupted | Check the internet connection, double-click the launcher again |
| The browser shows nothing | The planner is still starting | Wait 10 seconds and refresh, or open http://localhost:8501 |

**Backup:** double-click **`run_report_only_mac.command`** (Mac) or **`run_report_only_windows.bat`** (Windows).
It runs the same planner without the app and saves the Excel report in the **`output`** folder.

---

## What's in the folder

| File | What it is |
|---|---|
| `run_mac.command`, `run_windows.bat` | Start the planner |
| `run_report_only_*` | Backup: Excel report without the app |
| `settings.xlsx` | Every assumption and mapping. Edit in Excel |
| `data/` | This month's four files |
| `output/` | Reports from the backup launcher |
| `app.py` | The app's screens |
| `run.py` | The command-line version (`python run.py --data path/to/files`) |
| `launch.py` | Starts the app and opens the browser (used by the launchers) |
| `tool/` | The calculations: `clean.py`, `metrics.py`, `recommend.py`, `pipeline.py`, `report.py`, `common.py`, `constants.py` |
| `tests/` | 23 scenario tests (`python tests/run_tests.py`), the latest test report, and the environments tested |
| `WALKTHROUGH.md` | What every part of the code does, in plain English |
| `make_zip.py` | For whoever sends the folder on: `python3 make_zip.py` builds a clean zip without this computer's set-up |
