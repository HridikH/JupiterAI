#!/usr/bin/env python3
"""Glasses console desktop shell — adapted from the parent repo's scripts/desktop.py.

Run:  python ui/desktop.py

Starts the glasses FastAPI server (unless one is already up), waits for it to
answer, then opens a frameless Chromium --app window on it. Quitting the window
stops the server we spawned. All UI lives in ui/web/index.html; this file is only
process management + window chrome (same approach as the parent repo — no Electron
binary to sign).
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

ROOT = Path(__file__).resolve().parent.parent          # v4-glasses/
HOST = os.getenv("GLASSES_HOST", "127.0.0.1")
PORT = os.getenv("GLASSES_PORT", "8777")
URL = f"http://{HOST}:{PORT}"
HEALTH = URL + "/healthz"
WIN_SIZE = (1100, 760)

BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge",
]


def python_bin() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else "python3"


def find_browser():
    for c in BROWSERS:
        if os.path.sep in c:
            if Path(c).exists():
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    return None


def ping(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def ensure_server():
    if ping():
        print("[desktop] reusing server already running on " + URL)
        return None
    print("[desktop] starting glasses server ...")
    proc = subprocess.Popen([python_bin(), "-m", "server.app"], cwd=str(ROOT))
    for _ in range(120):
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early (code {proc.returncode})")
        if ping():
            return proc
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("server did not come up on " + URL)


def open_window():
    browser = find_browser()
    if not browser:
        raise RuntimeError("No Chromium-based browser found. Open " + URL + " manually.")
    profile = ROOT / "data" / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    print(f"[desktop] opening window via {Path(browser).name}")
    return subprocess.Popen(
        [browser, f"--app={URL}", f"--user-data-dir={profile}",
         f"--window-size={WIN_SIZE[0]},{WIN_SIZE[1]}",
         "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        window.wait()
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
