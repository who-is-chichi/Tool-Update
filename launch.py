"""Opens the planner: starts the app if it isn't already running, waits until it answers, then opens the browser.
Used by run_mac.command and run_windows.bat; runs inside the planner's own Python environment (.venv)."""
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 8501
URL = f"http://localhost:{PORT}"


def healthy():
    try:
        with urllib.request.urlopen(f"{URL}/_stcore/health", timeout=1) as r:
            return r.read().decode().strip() == "ok"
    except Exception:  # noqa: BLE001  (not running yet)
        return False


def port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    if healthy():
        print("The planner is already running. Opening it in your browser.")
        webbrowser.open(URL)
        return 0
    if port_in_use():
        print(f"Another program is using port {PORT}. Close the other planner window (or restart the computer), "
              "then try again.")
        return 1
    cmd = [sys.executable, "-m", "streamlit", "run", str(HERE / "app.py"), "--server.port", str(PORT),
           "--server.headless", "true", "--browser.gatherUsageStats", "false"]
    proc = subprocess.Popen(cmd, cwd=str(HERE))
    for _ in range(120):                                   # up to 60 seconds
        if proc.poll() is not None:
            print("\nThe planner stopped while starting; see the messages above.")
            return proc.returncode or 1
        if healthy():
            webbrowser.open(URL)
            print(f"\nThe planner is open at {URL}. Keep this window open while you use it; close it when you're done.")
            break
        time.sleep(0.5)
    else:
        print(f"\nThe planner is slow to start. In a moment, open {URL} in your browser.")
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
