#!/bin/bash
# Backup: runs the planner without the app and saves the Excel report in the output folder.
cd "$(dirname "$0")" || exit 1
pause() { read -r -p "Press Enter to close this window." _; }
CHECK='import streamlit, pandas, numpy, openpyxl, xlsxwriter, altair'

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  echo "Python 3.9 or newer is needed. See README, section 'One-time setup'."
  pause; exit 1
fi
if ! ./.venv/bin/python -c "$CHECK" >/dev/null 2>&1; then
  echo "Setting up the planner (first run, about 2 minutes, needs internet)..."
  python3 -m venv --clear .venv \
    && ./.venv/bin/python -m pip install --upgrade pip -q \
    && ./.venv/bin/python -m pip install -r requirements.txt -q
  if ! ./.venv/bin/python -c "$CHECK" >/dev/null 2>&1; then
    echo "Setup failed (see the messages above). Check the internet connection and try again."
    pause; exit 1
  fi
fi
./.venv/bin/python run.py
open output
pause
