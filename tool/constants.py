"""Model constants: technical choices, not business assumptions (those live in settings.xlsx).

Each one is named and explained here so it can be reviewed. Change one only with a reason, then rerun the tests.
"""

# ---- response curves (tool/recommend.py)
MIN_HISTORY_MONTHS = 18              # fewer months of spend history: no curve, the channel is held at this year's spend
MIN_HISTORY_MONTHS_SEASONAL = 30     # month-of-year effects add 11 numbers to estimate; try them only with 2.5+ years
RESPONSE_MIN, RESPONSE_MAX = 0.05, 1.0   # b outside this range is not credible (b > 1: more spend gets cheaper)
LOW_CONFIDENCE_STD_ERROR = 0.08      # b's standard error above this: low confidence
LOW_CONFIDENCE_SPEND_VARIATION = 0.25    # spend that varied less than 25% (std / mean) can't show a response
ALLOCATION_STEPS = 400               # the month's budget is handed out in 400 steps; 1,000 steps gives the same answer

# ---- customer value (tool/metrics.py)
MIN_CUSTOMERS_PER_AGE = 100          # a channel's own month-to-month growth needs 100+ sampled customers at that age;
                                     # below that the all-customer growth is used for that age
YEARS_OF_REPEAT = 3                  # repeat rates for years 1, 2 and 3 after the first order
BACKTEST_RECENT_COHORTS = 6          # back-test B: the latest 6 start months with 12 months observed

# ---- checks against finance (tool/metrics.py)
CHECK_TOLERANCE_REPEAT = 0.01        # repeat rates match if within 1 point
CHECK_TOLERANCE_OTHER = 0.03         # money figures match if within 3%

# ---- checks on the latest month of data (tool/metrics.py)
LATEST_MONTH_GAP_DAYS = 3            # last order more than 3 days before month end: the export may be incomplete
NO_RECORD_SPIKE = 2.0                # share of new customers without a customers.csv record above 2x its usual level
NO_RECORD_MIN_SHARE = 0.05           # ... and above 5%
CONTINUITY_DROP_ALERT = 0.30         # existing customers ordering 30% below their recent trend: customer IDs may have changed
CONTINUITY_LOOKBACK_MONTHS = 6
