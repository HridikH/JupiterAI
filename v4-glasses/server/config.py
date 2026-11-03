"""Central server config. Same env-overridable idiom as jarvis/config.py.

Nothing secret is hardcoded. The shared token and LAN bind address come from env
(or a local, gitignored .env loaded by the launcher). Defaults are safe for a
local demo but the token default is intentionally a placeholder you MUST change.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # v4-glasses/
DATA_DIR = Path(os.getenv("GLASSES_DATA_DIR", str(ROOT / "data")))
CATALOG_DIR = Path(os.getenv("GLASSES_CATALOG_DIR", str(ROOT / "catalog")))
UI_WEB_DIR = ROOT / "ui" / "web"

# ---- Network (LAN-only bind) ------------------------------------------------
# Bind to the LAN interface only. 127.0.0.1 is the safe default for dev; set
# GLASSES_HOST to the laptop's 2.4 GHz LAN IP at demo time. Never 0.0.0.0 in
# a real deployment (docs/protocol.md §1).
HOST = os.getenv("GLASSES_HOST", "127.0.0.1")
PORT = int(os.getenv("GLASSES_PORT", "8777"))

# ---- Phone capture over HTTPS -----------------------------------------------
# Mobile browsers require a secure context for camera access AND SubtleCrypto
# (the sha256 the protocol needs), so the phone talks to the laptop over HTTPS.
# A second listener binds the LAN on HTTPS_PORT when a self-signed cert exists
# (make cert). HTTPS_HOST defaults to 0.0.0.0 so the phone can reach it; the
# plain-HTTP listener stays on HOST for the device/desktop.
HTTPS_HOST = os.getenv("GLASSES_HTTPS_HOST", "0.0.0.0")
HTTPS_PORT = int(os.getenv("GLASSES_HTTPS_PORT", "8443"))
CERT_FILE = os.getenv("GLASSES_CERT", str(ROOT / "data" / "cert.pem"))
KEY_FILE = os.getenv("GLASSES_KEY", str(ROOT / "data" / "key.pem"))

# ---- Auth -------------------------------------------------------------------
# Shared token required on every request (X-Glasses-Token). CHANGE THIS.
# Keep it out of git — set GLASSES_TOKEN in the environment / .env.
SHARED_TOKEN = os.getenv("GLASSES_TOKEN", "CHANGE_ME_dev_token")

# ---- Storage / privacy ------------------------------------------------------
# Captured images + logs live here, locally only. `python -m server.privacy wipe`
# removes everything under here.
CAPTURES_DIR = Path(os.getenv("GLASSES_CAPTURES_DIR", str(DATA_DIR / "captures")))
LOG_DIR = Path(os.getenv("GLASSES_LOG_DIR", str(DATA_DIR / "logs")))
STORE_IMAGES = os.getenv("GLASSES_STORE_IMAGES", "1") == "1"

# ---- Thresholds -------------------------------------------------------------
THRESHOLDS_FILE = Path(os.getenv("GLASSES_THRESHOLDS",
                                 str(ROOT / "server" / "thresholds.json")))

# ---- OCR backend ------------------------------------------------------------
# "apple" = Apple Vision via pyobjc (default, macOS, fast). "paddle"/"easy" =
# cross-platform fallback. Resolved lazily so stage-1 (receiver) needs none.
OCR_BACKEND = os.getenv("GLASSES_OCR_BACKEND", "apple")

# ---- Preprocessing ----------------------------------------------------------
CENTER_CROP_FRAC = float(os.getenv("GLASSES_CROP_FRAC", "0.60"))  # central 60%×60%

# ---- Optional datasheet Q&A (Qwen/MLX) --------------------------------------
# Strictly answers from retrieved page text with a citation; NEVER identifies a
# part. Off by default; requires the MLX stack from the parent repo.
DATASHEET_QA_ENABLED = os.getenv("GLASSES_DATASHEET_QA", "0") == "1"

# ---- Optional TTS readout (Kokoro) ------------------------------------------
TTS_READOUT_ENABLED = os.getenv("GLASSES_TTS_READOUT", "0") == "1"

# ---- Voice control ----------------------------------------------------------
# Always-listening voice command loop (show/hide pinout, datasheet questions).
# Off by default so tests / headless runs never grab the mic; the launcher sets
# GLASSES_VOICE_ALWAYS=1, and the UI can toggle it over the WebSocket.
VOICE_ALWAYS = os.getenv("GLASSES_VOICE_ALWAYS", "0") == "1"

# ---- Live camera feed (MJPEG) -----------------------------------------------
STREAM_FPS = int(os.getenv("GLASSES_STREAM_FPS", "12"))

# ---- Web datasheet lookup (parts NOT in the local catalog) ------------------
# The offline guarantee covers CACHED catalog parts. For a part the OCR reads
# that is not in the catalog (or doesn't clear the match thresholds), the system
# may search the web for that EXACT part number's datasheet. This is a retrieval
# aid only — it is never part of the deterministic identification decision and
# never produces/confirms a part number on its own.
WEB_SEARCH_ENABLED = os.getenv("GLASSES_WEB_SEARCH", "1") == "1"
WEB_SEARCH_TIMEOUT_S = float(os.getenv("GLASSES_WEB_SEARCH_TIMEOUT", "8"))
SEARXNG_URL = os.getenv("GLASSES_SEARXNG_URL", "http://localhost:8080")  # optional; DDG fallback

# Auto-cache a web-found datasheet the first time a part is seen, so the NEXT
# sighting matches offline. Auto-cached parts are unreviewed and the resolver
# only OFFERS them (never auto-accepts) until a human sets reviewed:true.
WEB_AUTOCACHE = os.getenv("GLASSES_WEB_AUTOCACHE", "1") == "1"


def ensure_dirs() -> None:
    for d in (DATA_DIR, CAPTURES_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
