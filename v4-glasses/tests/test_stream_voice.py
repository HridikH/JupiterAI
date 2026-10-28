"""Live-feed buffer, voice-command routing, and /frame + /pins endpoint tests."""
from __future__ import annotations

import threading

import pytest

from server import protocol
from server.stream import FrameBuffer


# ---- frame buffer -----------------------------------------------------------
def test_frame_buffer_put_and_seq():
    fb = FrameBuffer()
    fb.put(b"a")
    frame, seq = fb.wait_next(-1, timeout=0.1)
    assert frame == b"a" and seq == 1
    fb.put(b"bb")
    frame, seq = fb.wait_next(seq, timeout=0.1)
    assert frame == b"bb" and seq == 2


def test_frame_buffer_wait_times_out_without_new_frame():
    fb = FrameBuffer()
    fb.put(b"x")
    _, seq = fb.wait_next(-1, timeout=0.1)
    # no new frame -> returns same seq after timeout
    frame, seq2 = fb.wait_next(seq, timeout=0.1)
    assert seq2 == seq


# ---- voice command routing (deterministic, no LLM) --------------------------
from server.app import _PINOUT_ON_RE, _PINOUT_OFF_RE  # noqa: E402


@pytest.mark.parametrize("phrase", [
    "show pinout", "display the pinout", "bring up the pins", "turn on pinout",
    "overlay the pinout", "pinout on",
])
def test_pinout_on_phrases(phrase):
    assert _PINOUT_ON_RE.search(phrase)
    assert not _PINOUT_OFF_RE.search(phrase)


@pytest.mark.parametrize("phrase", [
    "hide pinout", "remove the pinout", "close the pins", "turn off pinout",
    "clear the pinout", "take down the pinout",
])
def test_pinout_off_phrases(phrase):
    assert _PINOUT_OFF_RE.search(phrase)


def test_unrelated_phrase_is_not_a_pinout_command():
    assert not _PINOUT_ON_RE.search("what is the max supply voltage")
    assert not _PINOUT_OFF_RE.search("what is the max supply voltage")


# ---- HTTP: frame ingest + pins ----------------------------------------------
@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from server import app as appmod, config
    monkeypatch.setattr(config, "SHARED_TOKEN", "tkn")
    monkeypatch.setattr(config, "VOICE_ALWAYS", False)  # never grab the mic in tests
    return TestClient(appmod.create_app())


def _jpeg():
    import io
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (32, 24), (10, 10, 10)).save(b, "JPEG"); return b.getvalue()


def test_frame_requires_token(client):
    r = client.post("/frame", content=_jpeg())
    assert r.status_code == 401


def test_frame_accepts_jpeg(client):
    r = client.post("/frame", headers={protocol.HDR_TOKEN: "tkn"}, content=_jpeg())
    assert r.status_code == 200 and r.json()["ok"] is True


def test_frame_rejects_oversize(client):
    big = b"\xff\xd8" + b"\x00" * (protocol.MAX_JPEG_BYTES + 1)
    r = client.post("/frame", headers={protocol.HDR_TOKEN: "tkn"}, content=big)
    assert r.status_code == 413


def test_pins_endpoint_shape(client):
    # unknown part (no cached datasheet) -> empty pin list but well-formed
    r = client.get("/pins", params={"part": "NOPART"})
    assert r.status_code == 200
    body = r.json()
    assert body["part"] == "NOPART" and body["pins"] == []
