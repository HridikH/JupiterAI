"""End-to-end test: fake-device simulator -> live server -> outcome.

Runs the real FastAPI app under uvicorn on an ephemeral port and drives it with
tools/fake_device.py over the real wire protocol. Exercises the full laptop side
(receiver V1 + pipeline + resolver) AND the simulator's firmware-semantics:
buffer-until-ACK, retries-with-dedup, duplicate IDs, wrong-id ACK handling,
and payload rejection — no hardware needed.
"""
from __future__ import annotations

import io
import socket
import threading
import time

import pytest
import requests
import uvicorn
from PIL import Image

from server import app as appmod, config
from server.state import StateStore
from server.ws import WSHub
from server.pipeline import Pipeline
from server.ocr.base import MockOCREngine
from server.catalog_api import load

from tools.fake_device import FakeDevice

TOKEN = "e2e_token"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def live_server():
    config.SHARED_TOKEN = TOKEN
    config.STORE_IMAGES = False
    store, hub = StateStore(), WSHub()
    # mock OCR reads NE555P on every variant -> deterministic accept
    eng = MockOCREngine(words=[("NE555P", 0.97)])
    pipe = Pipeline(store, hub, catalog=load(), engine=eng).as_fn(threaded=True)
    app = appmod.create_app(store=store, hub=hub, pipeline=pipe)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning", ws="wsproto"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if requests.get(base + "/healthz", timeout=0.5).status_code == 200:
                break
        except Exception:
            time.sleep(0.05)
    else:
        raise RuntimeError("server did not start")
    yield base
    server.should_exit = True
    th.join(timeout=5)


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (2592, 1944), (130, 110, 90)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _wait_terminal(dev, rid, timeout=8.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        rec = dev.result(rid)
        if rec.get("status") in ("accepted", "candidate_selection", "recapture", "error"):
            return rec
        time.sleep(0.1)
    return dev.result(rid)


def test_e2e_normal_accept(live_server):
    dev = FakeDevice(live_server, TOKEN)
    r = dev.upload(_jpeg(), request_id="e2e_accept_0001")
    assert r.ok and r.acked and r.status_code == 202
    rec = _wait_terminal(dev, "e2e_accept_0001")
    assert rec["status"] == "accepted"
    assert rec["result"]["part_number"] == "NE555P"
    assert rec["config_hash"]                      # result stamped with threshold hash


def test_e2e_duplicate_is_deduped(live_server):
    dev = FakeDevice(live_server, TOKEN)
    r1 = dev.upload(_jpeg(), request_id="e2e_dupe_0001")
    r2 = dev.upload(_jpeg(), request_id="e2e_dupe_0001")
    assert r1.body["duplicate"] is False
    assert r2.body["duplicate"] is True


def test_e2e_retry_same_id_deduped(live_server):
    dev = FakeDevice(live_server, TOKEN)
    r = dev.upload(_jpeg(), request_id="e2e_retry_0001", drop_attempts=2)
    assert r.attempts == 3 and r.ok      # 2 "dropped" + 1 success, server deduped


def test_e2e_wrong_header_id_rejected(live_server):
    dev = FakeDevice(live_server, TOKEN)
    r = dev.upload(_jpeg(), request_id="e2e_hdr_0001", header_id="totally_different")
    assert not r.ok and r.body["error"] == "E_BAD_REQUEST"


def test_e2e_tampered_hash_rejected(live_server):
    dev = FakeDevice(live_server, TOKEN)
    r = dev.upload(_jpeg(), request_id="e2e_tamp_0001", tamper_hash=True)
    assert not r.ok and r.body["error"] == "E_HASH_MISMATCH"


def test_e2e_oversize_rejected(live_server):
    dev = FakeDevice(live_server, TOKEN)
    r = dev.upload(_jpeg(), request_id="e2e_big_0001", oversize=True)
    assert not r.ok and r.body["error"] == "E_TOO_LARGE"


def test_e2e_bad_token_rejected(live_server):
    dev = FakeDevice(live_server, "wrong_token")
    r = dev.upload(_jpeg(), request_id="e2e_auth_0001")
    assert not r.ok and r.body["error"] == "E_UNAUTHORIZED"
