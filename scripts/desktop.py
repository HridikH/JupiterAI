#!/usr/bin/env python3
"""JARVIS desktop shell.

Run:  python scripts/desktop.py     (or ./scripts/desktop.py)

Spawns the Python dashboard server (unless one is already running on 7777),
waits for it to answer, then opens a chromeless browser window on it. Quitting
kills the server it spawned. All UI lives in web/index.html — this file is only
process management + window chrome.

Replaces the old Electron shell: a Chromium browser in --app mode gives the same
frameless window without shipping an unsigned 200 MB binary that macOS Gatekeeper
refuses to launch.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:7777"
WIN_SIZE = (1180, 760)

# Chromium-family browsers support --app=; ordered by preference. macOS paths
# first, then anything on PATH (Linux).
BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
]


def python_bin() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else "python3"


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if os.path.sep in candidate:
            if Path(candidate).exists():
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def ping(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # server answered, just not with a 200
    except Exception:
        return False


def ensure_server() -> subprocess.Popen | None:
    """Start the dashboard server unless one is already up. Returns the process
    only if WE spawned it, so we know whether to kill it on exit."""
    if ping():
        print("[desktop] reusing server already running on " + URL)
        return None

    print("[desktop] starting jarvis.server ...")
    proc = subprocess.Popen(
        [python_bin(), "-m", "jarvis.server", "--no-browser"],
        cwd=str(ROOT),
        # stdout/stderr inherited: model download progress lands in this terminal
    )

    for _ in range(240):  # up to ~2 min — first launch loads models
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early (code {proc.returncode})")
        if ping():
            return proc
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError("server did not come up on " + URL)


def open_window() -> subprocess.Popen:
    browser = find_browser()
    if not browser:
        raise RuntimeError(
            "No Chromium-based browser found. Install Google Chrome, or open "
            + URL
            + " in any browser manually."
        )

    # Dedicated profile dir keeps the app window out of your normal Chrome
    # session: no tabs restored, no extensions, own Dock entry.
    profile = ROOT / "data" / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)

    print(f"[desktop] opening window via {Path(browser).name}")
    return subprocess.Popen(
        [
            browser,
            f"--app={URL}",
            f"--user-data-dir={profile}",
            f"--window-size={WIN_SIZE[0]},{WIN_SIZE[1]}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    server = None
    try:
        server = ensure_server()
        window = open_window()
    except RuntimeError as e:
        print(f"[desktop] error: {e}", file=sys.stderr)
        if server:
            server.send_signal(signal.SIGINT)
            server.wait(timeout=10)
        return 1

    try:
        window.wait()  # blocks until the app window is closed
        print("[desktop] window closed")
    except KeyboardInterrupt:
        print("\n[desktop] interrupted")
        window.terminate()
    finally:
        if server:
            print("[desktop] stopping server")
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
