"""Builds a clean zip of the planner to send to someone else.

Leaves out this computer's Python setup (.venv), caches, reports in output/ and backups; keeps the Mac launchers
executable and the data files replaceable.

    python make_zip.py                 -> Cotopaxi_Channel_Planner.zip next to this folder
    python make_zip.py path/to/x.zip
"""
import os
import stat
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIP_DIRS = {".venv", "__pycache__", ".git", ".pytest_cache", ".idea", ".vscode"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / f"{HERE.name}.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(HERE):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                p = Path(root) / name
                rel = p.relative_to(HERE)
                if (name in SKIP_FILES or name.startswith("._") or p.suffix in (".zip", ".pyc")
                        or (rel.parts[0] == "output" and name != ".keep")):
                    continue
                info = zipfile.ZipInfo.from_file(p, arcname=f"{HERE.name}/{rel.as_posix()}")
                info.external_attr = (stat.S_IFREG | (0o755 if p.suffix == ".command" else 0o644)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
                count += 1
    print(f"Wrote {out} ({count} files).")


if __name__ == "__main__":
    main()
