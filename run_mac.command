#!/bin/bash
# Double-click to open the planner. The first run sets things up (about 2 minutes, needs internet).
cd "$(dirname "$0")" || exit 1
pause() { read -r -p "Press Enter to close this window." _; }
CHECK='import streamlit, pandas, numpy, openpyxl, xlsxwriter, altair'

# 1. Python 3.9 or newer. Running it is the only reliable check: on a new Mac, 'python3' can be a placeholder
#    that offers to install Apple's developer tools.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  osascript -e 'display alert "Python 3.9 or newer is needed" message "If a window offered to install the command line developer tools, click Install, wait until it finishes, then double-click run_mac.command again. Otherwise install Python from python.org (accept the defaults) and double-click run_mac.command again."' >/dev/null 2>&1
  echo "Python 3.9 or newer is needed. See README, section 'One-time setup'."
  pause; exit 1
fi

# 2. The planner's own Python environment (.venv). Checked on every start; rebuilt if it is missing, half-installed
#    or was copied from another computer.
if ! ./.venv/bin/python -c "$CHECK" >/dev/null 2>&1; then
  echo "Setting up the planner (first run, about 2 minutes, needs internet)..."
  python3 -m venv --clear .venv \
    && ./.venv/bin/python -m pip install --upgrade pip -q \
    && ./.venv/bin/python -m pip install -r requirements.txt -q
  if ! ./.venv/bin/python -c "$CHECK" >/dev/null 2>&1; then
    echo ""
    echo "Setup failed (see the messages above). Check the internet connection and double-click run_mac.command again."
    echo "If it fails again, see README, section 'If something goes wrong'."
    pause; exit 1
  fi
fi

# 3. Start the planner and open it in the browser.
echo "Opening the planner in your browser. Keep this window open while you use it; close it when you're done."
./.venv/bin/python launch.py
status=$?
if [ $status -ne 0 ]; then pause; fi
exit $status
