@echo off
rem Backup: runs the planner without the app and saves the Excel report in the output folder.
setlocal
cd /d "%~dp0"
set "CHECK=import streamlit, pandas, numpy, openpyxl, xlsxwriter, altair"

rem 1. Python 3.9 or newer: the 'py' launcher first, then 'python'. Running it is the only reliable check:
rem    'python' may be a Microsoft Store placeholder that only offers to install Python.
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY goto nopython

rem 2. The planner's own Python environment (.venv). Checked on every start; rebuilt if missing or half-installed.
".venv\Scripts\python.exe" -c "%CHECK%" >nul 2>&1
if not errorlevel 1 goto ready
echo Setting up the planner (first run, about 2 minutes, needs internet)...
%PY% -m venv --clear .venv
if errorlevel 1 goto setupfailed
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
".venv\Scripts\python.exe" -c "%CHECK%" >nul 2>&1
if errorlevel 1 goto setupfailed

:ready
".venv\Scripts\python.exe" run.py
start "" "output"
pause
exit /b 0

:nopython
echo Python 3.9 or newer is needed.
echo Install it from python.org and tick "Add python.exe to PATH" on the first screen of the installer,
echo then double-click this file again. See README, section "One-time setup".
pause
exit /b 1

:setupfailed
echo.
echo Setup failed (see the messages above). Check the internet connection and double-click this file again.
echo If it fails again, see README, section "If something goes wrong".
pause
exit /b 1
