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


def ensure_dirs() -> None:
    for d in (DATA_DIR, CAPTURES_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
