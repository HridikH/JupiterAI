"""Receiver validation tests (V1) — unit + HTTP integration.

Covers every step of docs/protocol.md §3.1 and the idempotency rule. The guiding
assertion throughout: a malformed/oversized payload NEVER produces a record that
can reach 'accepted'.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from server import protocol, receiver
from server.state import StateStore

from conftest import make_jpeg

TOKEN = "test_token"


def _meta(image: bytes, rid: str, **over):
    m = {"request_id": rid, "proto": protocol.PROTO_VERSION,
         "width": 64, "height": 48, "byte_count": len(image),
         "sha256": hashlib.sha256(image).hexdigest(), "battery_mv": 3900}
    m.update(over)
    return json.dumps(m).encode()


def _validate(image, meta_raw, store, *, token=TOKEN, header_id="req_abcdef12"):
    return receiver.validate_capture(
        token=token, request_id_header=header_id, meta_raw=meta_raw,
        image_bytes=image, store=store, expected_token=TOKEN)


# ---- happy path -------------------------------------------------------------
def test_valid_capture_enters_processing(jpeg_bytes, store):
    rec, created, meta = _validate(jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12"), store)
    assert created is True
    assert rec.status == "processing"
    assert rec.request_id == "req_abcdef12"


def test_sha256_helper_matches():
    data = b"hello"
    assert receiver.sha256_hex(data) == hashlib.sha256(data).hexdigest()


# ---- idempotency / dedup (E1, V1 step 4) ------------------------------------
def test_duplicate_request_id_not_recreated(jpeg_bytes, store):
    _validate(jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12"), store)
    rec2, created2, _ = _validate(jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12"), store)
    assert created2 is False
    assert len(store.all_ids()) == 1


# ---- auth -------------------------------------------------------------------
@pytest.mark.parametrize("token", [None, "", "wrong"])
def test_bad_token_rejected(jpeg_bytes, store, token):
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12"), store, token=token)
    assert e.value.code == protocol.E_UNAUTHORIZED


# ---- meta -------------------------------------------------------------------
def test_bad_meta_json(jpeg_bytes, store):
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(jpeg_bytes, b"{not json", store)
    assert e.value.code == protocol.E_BAD_META


def test_missing_required_field(jpeg_bytes, store):
    raw = json.dumps({"request_id": "req_abcdef12", "width": 64}).encode()
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(jpeg_bytes, raw, store)
    assert e.value.code == protocol.E_BAD_META


def test_meta_too_large(jpeg_bytes, store):
    raw = _meta(jpeg_bytes, "req_abcdef12", note="x" * (protocol.MAX_META_BYTES + 10))
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(jpeg_bytes, raw, store)
    assert e.value.code == protocol.E_TOO_LARGE


# ---- request id -------------------------------------------------------------
def test_header_id_mismatch(jpeg_bytes, store):
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12"), store,
                  header_id="other_id_9999")
    assert e.value.code == protocol.E_BAD_REQUEST


def test_malformed_request_id(jpeg_bytes, store):
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(jpeg_bytes, _meta(jpeg_bytes, "ab"), store, header_id="ab")
    assert e.value.code == protocol.E_BAD_REQUEST


# ---- size / hash / decode ---------------------------------------------------
def test_oversize_jpeg(store):
    big = make_jpeg() + b"\x00" * protocol.MAX_JPEG_BYTES
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(big, _meta(big, "req_abcdef12"), store)
    assert e.value.code == protocol.E_TOO_LARGE


def test_byte_count_mismatch(jpeg_bytes, store):
    raw = _meta(jpeg_bytes, "req_abcdef12", byte_count=len(jpeg_bytes) + 1)
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(jpeg_bytes, raw, store)
    assert e.value.code == protocol.E_SIZE_MISMATCH


def test_hash_mismatch(jpeg_bytes, store):
    raw = _meta(jpeg_bytes, "req_abcdef12", sha256="0" * 64)
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(jpeg_bytes, raw, store)
    assert e.value.code == protocol.E_HASH_MISMATCH


def test_not_a_jpeg(store):
    junk = b"definitely not a jpeg" * 8
    with pytest.raises(protocol.ProtocolError) as e:
        _validate(junk, _meta(junk, "req_abcdef12"), store)
    assert e.value.code == protocol.E_DECODE_FAILED


def test_malformed_never_reaches_store(jpeg_bytes, store):
    """Core safety: a rejected payload leaves NO record (can't become accepted)."""
    raw = _meta(jpeg_bytes, "req_abcdef12", sha256="0" * 64)
    with pytest.raises(protocol.ProtocolError):
        _validate(jpeg_bytes, raw, store)
    assert store.get("req_abcdef12") is None


# ---- HTTP integration -------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from server import app as appmod
    from server import config
    monkeypatch.setattr(config, "SHARED_TOKEN", TOKEN)
    monkeypatch.setattr(config, "STORE_IMAGES", False)
    application = appmod.create_app()
    return TestClient(application)


def _post(client, image, meta_raw, token=TOKEN, header_id="req_abcdef12"):
    headers = {protocol.HDR_TOKEN: token}
    if header_id is not None:
        headers[protocol.HDR_REQUEST_ID] = header_id
    return client.post(protocol.EP_CAPTURE, headers=headers,
                       data={"meta": meta_raw.decode()},
                       files={"image": ("c.jpg", image, "image/jpeg")})


def test_http_healthz(client):
    r = client.get(protocol.EP_HEALTH)
    assert r.status_code == 200 and r.json()["proto"] == protocol.PROTO_VERSION


def test_http_valid_returns_202_with_matching_ack(client, jpeg_bytes):
    r = _post(client, jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12"))
    assert r.status_code == 202
    body = r.json()
    assert body["ok"] is True
    assert body["ack"]["request_id"] == "req_abcdef12"   # E1/E3 anchor
    assert body["duplicate"] is False


def test_http_duplicate_flag(client, jpeg_bytes):
    _post(client, jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12"))
    r2 = _post(client, jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12"))
    assert r2.status_code == 202 and r2.json()["duplicate"] is True


def test_http_hash_mismatch_returns_400(client, jpeg_bytes):
    r = _post(client, jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12", sha256="0" * 64))
    assert r.status_code == 400 and r.json()["error"] == protocol.E_HASH_MISMATCH


def test_http_result_roundtrip(client, jpeg_bytes):
    _post(client, jpeg_bytes, _meta(jpeg_bytes, "req_abcdef12"))
    r = client.get(f"{protocol.EP_RESULT}/req_abcdef12")
    assert r.status_code == 200
    assert r.json()["status"] == "processing"
    assert r.json()["battery_mv"] == 3900
