"""Receiver: validate EVERYTHING before OCR, dedup by request_id (V1).

This module is deliberately framework-agnostic (no FastAPI imports) so it can be
unit-tested directly and reused by the simulator. app.py adapts HTTP to it.

Validation order mirrors docs/protocol.md §3.1 exactly. A malformed or oversized
payload raises ProtocolError and never reaches the pipeline, so it can never
enter an accepted state.
"""
from __future__ import annotations

import hashlib
import io
import json
from typing import Any, Callable, Optional

from . import protocol
from .logging_setup import get
from .state import RequestRecord, StateStore

log = get("receiver")

# JPEG decode. Pillow is a light dependency; the heavy OCR deps come later.
try:
    from PIL import Image
    _HAVE_PIL = True
except Exception:  # pragma: no cover - import guard
    _HAVE_PIL = False


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_meta(meta_raw: bytes) -> dict[str, Any]:
    """Step 2 + field presence. Raises ProtocolError(E_BAD_META / E_TOO_LARGE)."""
    if len(meta_raw) > protocol.MAX_META_BYTES:
        raise protocol.ProtocolError(
            protocol.E_TOO_LARGE,
            f"metadata {len(meta_raw)}B > {protocol.MAX_META_BYTES}B",
        )
    try:
        meta = json.loads(meta_raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise protocol.ProtocolError(protocol.E_BAD_META, f"meta not JSON: {e}")
    if not isinstance(meta, dict):
        raise protocol.ProtocolError(protocol.E_BAD_META, "meta is not an object")
    missing = [k for k in protocol.META_REQUIRED if k not in meta]
    if missing:
        raise protocol.ProtocolError(
            protocol.E_BAD_META, f"missing fields: {', '.join(missing)}"
        )
    return meta


def _check_jpeg(image_bytes: bytes) -> None:
    """Step 8: decodes as a valid JPEG. Raises E_DECODE_FAILED."""
    if not _HAVE_PIL:
        # Fallback: SOI/EOI marker sniff so the receiver still rejects garbage
        # even before Pillow is installed (stage-1 bootstrap).
        if not (image_bytes[:2] == b"\xff\xd8" and image_bytes[-2:] == b"\xff\xd9"):
            raise protocol.ProtocolError(
                protocol.E_DECODE_FAILED, "not a JPEG (SOI/EOI sniff; Pillow absent)"
            )
        return
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            if (im.format or "").upper() != "JPEG":
                raise protocol.ProtocolError(
                    protocol.E_DECODE_FAILED, f"format={im.format}, expected JPEG"
                )
            im.verify()  # structural check without full decode
    except protocol.ProtocolError:
        raise
    except Exception as e:
        raise protocol.ProtocolError(protocol.E_DECODE_FAILED, f"JPEG decode failed: {e}")


def validate_capture(
    *,
    token: Optional[str],
    request_id_header: Optional[str],
    meta_raw: bytes,
    image_bytes: bytes,
    store: StateStore,
    expected_token: str,
    on_valid: Optional[Callable[[RequestRecord, bytes], None]] = None,
) -> tuple[RequestRecord, bool, dict[str, Any]]:
    """Run the full §3.1 validation pipeline.

    Returns (record, created, meta). created=False => duplicate (no new task).
    Raises protocol.ProtocolError on any validation failure.
    """
    # 1. token
    if not token or token != expected_token:
        raise protocol.ProtocolError(protocol.E_UNAUTHORIZED, "missing/invalid token")

    # 2. meta parse + required fields
    meta = parse_meta(meta_raw)

    # 3. request_id header well-formed + equals meta.request_id
    rid = request_id_header
    if not rid or not protocol.REQUEST_ID_RE.match(rid):
        raise protocol.ProtocolError(
            protocol.E_BAD_REQUEST, "missing/malformed X-Request-Id"
        )
    if rid != str(meta.get("request_id")):
        raise protocol.ProtocolError(
            protocol.E_BAD_REQUEST, "X-Request-Id != meta.request_id"
        )

    # 4. idempotency — dedup BEFORE expensive checks so retries are cheap & safe
    existing = store.get(rid)
    if existing is not None:
        log.info("duplicate capture", extra={"request_id": rid, "status": existing.status})
        return existing, False, meta

    # 5. image present + size
    if not image_bytes:
        raise protocol.ProtocolError(protocol.E_BAD_REQUEST, "empty image part")
    if len(image_bytes) > protocol.MAX_JPEG_BYTES:
        raise protocol.ProtocolError(
            protocol.E_TOO_LARGE,
            f"jpeg {len(image_bytes)}B > {protocol.MAX_JPEG_BYTES}B",
        )

    # 6. byte_count matches
    declared = meta.get("byte_count")
    if not isinstance(declared, int) or declared != len(image_bytes):
        raise protocol.ProtocolError(
            protocol.E_SIZE_MISMATCH,
            f"byte_count={declared} actual={len(image_bytes)}",
        )

    # 7. sha256 matches
    actual_hash = sha256_hex(image_bytes)
    if str(meta.get("sha256", "")).lower() != actual_hash:
        raise protocol.ProtocolError(
            protocol.E_HASH_MISMATCH, "sha256 does not match image bytes"
        )

    # 8. decodes as JPEG
    _check_jpeg(image_bytes)

    # All checks passed — create the record (exactly one task per id) and move
    # received -> processing, then hand to the pipeline callback (if any).
    rec, created = store.start(rid, meta)
    rec.stamp("processing")
    log.info("capture accepted into pipeline",
             extra={"request_id": rid, "status": "processing"})
    if on_valid is not None:
        on_valid(rec, image_bytes)
    return rec, created, meta
