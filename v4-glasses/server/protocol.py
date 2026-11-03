"""Wire-protocol constants and shared schemas — mirrors docs/protocol.md.

If this file and docs/protocol.md disagree, the DOC wins and this is a bug.
Both firmware and server treat docs/protocol.md as the contract; this module is
the Python-side transcription so the server and tools never hardcode magic
strings in multiple places.
"""
from __future__ import annotations

import re

PROTO_VERSION = "glasses-proto/1"

# ---- Size limits (hard) -----------------------------------------------------
MAX_JPEG_BYTES = 2_097_152   # 2 MiB  (C1 / V1)
MAX_META_BYTES = 16_384      # 16 kB  (V1)

# ---- request_id shape -------------------------------------------------------
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# ---- Headers ----------------------------------------------------------------
HDR_TOKEN = "X-Glasses-Token"
HDR_REQUEST_ID = "X-Request-Id"

# ---- Endpoints --------------------------------------------------------------
EP_CAPTURE = "/capture"
EP_RESULT = "/result"       # /result/{request_id}
EP_HEALTH = "/healthz"

# ---- States (server state machine, §6) --------------------------------------
STATES = (
    "received",
    "processing",
    "accepted",
    "candidate_selection",
    "recapture",
    "error",
)
TERMINAL_STATES = ("accepted", "candidate_selection", "recapture", "error")

# ---- Recapture reasons (U2) -------------------------------------------------
RECAPTURE_REASONS = ("blur", "glare", "off_center", "too_far", "unreadable", "not_in_catalog")

# ---- Error codes (§8) -------------------------------------------------------
E_UNAUTHORIZED = "E_UNAUTHORIZED"
E_BAD_META = "E_BAD_META"
E_BAD_REQUEST = "E_BAD_REQUEST"
E_SIZE_MISMATCH = "E_SIZE_MISMATCH"
E_HASH_MISMATCH = "E_HASH_MISMATCH"
E_TOO_LARGE = "E_TOO_LARGE"
E_DECODE_FAILED = "E_DECODE_FAILED"
E_INTERNAL = "E_INTERNAL"

# code -> default HTTP status
ERROR_HTTP = {
    E_UNAUTHORIZED: 401,
    E_BAD_META: 400,
    E_BAD_REQUEST: 400,
    E_SIZE_MISMATCH: 400,
    E_HASH_MISMATCH: 400,
    E_TOO_LARGE: 413,
    E_DECODE_FAILED: 422,
    E_INTERNAL: 500,
}

# Required metadata fields (§4)
META_REQUIRED = ("request_id", "width", "height", "byte_count", "sha256")


class ProtocolError(Exception):
    """Raised by validation with a protocol error code + HTTP status."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        self.http_status = ERROR_HTTP.get(code, 400)
        super().__init__(f"{code}: {detail}")
