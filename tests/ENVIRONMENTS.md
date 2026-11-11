# Environments tested

Kept by hand. `TEST_REPORT.md` is rewritten by every test run; this file is not.

| Environment | Python | pandas | numpy | Streamlit | Result |
| --- | --- | --- | --- | --- | --- |
| This Mac (macOS 26.6), Apple's own Python, oldest supported packages | 3.9.6 | 2.3.3 | 2.0.2 | 1.50.0 (altair 5.5.0) | 23 of 23 tests pass; 12-month impact $381,644 |
| This Mac (macOS 26.6), python.org Python, latest packages | 3.14.6 | 3.1.1 | 2.5.4 | 1.67.0 (altair 6.3.0) | 23 of 23 tests pass; identical results |
| Ready-made packages available, nothing to compile (checked with pip, 10 Nov 2026) | 3.13, 3.14, 3.15 | 3.1.1 | 2.5.4 | 1.67.0 | Mac with Apple chip (macOS 14+), Mac with Intel chip (macOS 12+), Windows 64-bit |

The recommendation and the 12-month impact were identical in every environment and in every repeated run.

## Still to check by hand before sending
Build the zip with `python3 make_zip.py`, send it to yourself (email or a shared drive) and download it, as the panel
will, then unzip it into a new folder and follow README.md only:
- [ ] This Mac: double-click `run_mac.command` (a downloaded zip carries macOS's "downloaded" flag, so the security
      prompt appears), run the planner, download the report, then double-click `run_report_only_mac.command`.
- [ ] A Mac without Python set up: the developer-tools prompt or python.org, then the same steps.
- [ ] A Windows PC: install Python from python.org with "Add python.exe to PATH", double-click `run_windows.bat`
      (SmartScreen: More info › Run anyway), run the planner, download the report, then `run_report_only_windows.bat`.
