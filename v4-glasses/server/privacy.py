"""Privacy: local-only storage + a single wipe command.

    python -m server.privacy wipe      # delete all captured images + logs
    python -m server.privacy status     # show what's stored

Images and logs never leave the laptop; there is no network path out of the
runtime. This just makes cleanup a one-liner.
"""
from __future__ import annotations

import shutil
import sys

from . import config


def _du(path) -> tuple[int, int]:
    n = total = 0
    if path.exists():
        for p in path.rglob("*"):
            if p.is_file():
                n += 1
                total += p.stat().st_size
    return n, total


def status() -> None:
    for label, d in (("captures", config.CAPTURES_DIR), ("logs", config.LOG_DIR)):
        n, total = _du(d)
        print(f"{label:10s} {d}  ({n} files, {total/1e6:.2f} MB)")


def wipe() -> None:
    for d in (config.CAPTURES_DIR, config.LOG_DIR):
        if d.exists():
            shutil.rmtree(d)
            print(f"wiped {d}")
    config.ensure_dirs()
    print("done — storage reset.")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "wipe":
        wipe()
    else:
        status()


if __name__ == "__main__":
    main()
