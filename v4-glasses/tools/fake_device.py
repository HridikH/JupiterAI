"""Fake-device simulator — exercises the whole laptop side without hardware.

Plays the firmware's role against the REAL wire protocol (docs/protocol.md):
builds correct multipart uploads, matches ACKs by request_id, ignores wrong-id
ACKs (E3), retries the SAME request_id on drops with a 5 s timeout / 2-retry cap
(E2), and can send duplicates to prove server idempotency (E1 / V1 step 4).

Usage:
    python -m tools.fake_device --url http://127.0.0.1:8777 --token dev \\
        --image eval/datasets/samples/ne555.jpg --scenario normal

Scenarios: normal | duplicate | retry | wrong-header-id | tamper-hash |
           tamper-bytecount | oversize | bad-meta
It is also importable (FakeDevice) for the e2e test.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

# Allow both `python -m tools.fake_device` and direct execution.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import protocol  # noqa: E402

TIMEOUT_S = 5.0        # per-attempt (E2)
MAX_RETRIES = 2        # => 3 attempts total (E2)


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _jpeg_dims(b: bytes) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(b)) as im:
            return im.size
    except Exception:
        return (0, 0)


@dataclass
class UploadResult:
    ok: bool
    status_code: int
    request_id: str
    body: dict
    attempts: int
    acked: bool          # did a *matching* ACK arrive? (E1/E3)


class FakeDevice:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token

    def _build(self, image_bytes: bytes, request_id: str, *,
               attempt: int = 0,
               tamper_hash: bool = False,
               tamper_bytecount: bool = False,
               bad_meta: bool = False,
               battery_mv: int = 3900) -> tuple[dict, bytes]:
        w, h = _jpeg_dims(image_bytes)
        sha = _sha256(image_bytes)
        if tamper_hash:
            sha = "0" * 64
        byte_count = len(image_bytes) + (7 if tamper_bytecount else 0)
        meta = {
            "request_id": request_id,
            "proto": protocol.PROTO_VERSION,
            "width": w, "height": h,
            "byte_count": byte_count,
            "sha256": sha,
            "battery_mv": battery_mv,
            "firmware": "fake-device/0.1",
            "attempt": attempt,
        }
        if bad_meta:
            meta.pop("sha256")   # drop a required field -> E_BAD_META
        return meta, image_bytes

    def _post_once(self, meta: dict, image_bytes: bytes, *,
                   header_id: Optional[str], timeout: float) -> requests.Response:
        headers = {protocol.HDR_TOKEN: self.token}
        if header_id is not None:
            headers[protocol.HDR_REQUEST_ID] = header_id
        files = {"image": ("capture.jpg", image_bytes, "image/jpeg")}
        data = {"meta": json.dumps(meta)}
        return requests.post(self.url + protocol.EP_CAPTURE,
                             headers=headers, data=data, files=files, timeout=timeout)

    def _ack_matches(self, sent_id: str, body: dict) -> bool:
        """Firmware rule (E3): accept an ACK only if its id matches what we sent."""
        ack = body.get("ack") or {}
        return ack.get("request_id") == sent_id

    def upload(self, image_bytes: bytes, *,
               request_id: Optional[str] = None,
               header_id: Optional[str] = None,
               tamper_hash: bool = False,
               tamper_bytecount: bool = False,
               bad_meta: bool = False,
               oversize: bool = False,
               drop_attempts: int = 0,
               battery_mv: int = 3900) -> UploadResult:
        """Upload with firmware semantics: buffer-until-ACK, retry same id.

        drop_attempts: simulate the first N attempts getting no (usable) ACK —
        the request may still reach the server, so retries exercise server
        idempotency on the SAME request_id.
        """
        rid = request_id or uuid.uuid4().hex[:16]
        if oversize:
            image_bytes = image_bytes + b"\x00" * (protocol.MAX_JPEG_BYTES + 1)
        attempts = 0
        last: Optional[requests.Response] = None
        for attempt in range(MAX_RETRIES + 1):
            attempts += 1
            meta, body_img = self._build(
                image_bytes, rid, attempt=attempt,
                tamper_hash=tamper_hash, tamper_bytecount=tamper_bytecount,
                bad_meta=bad_meta, battery_mv=battery_mv)
            hid = header_id if header_id is not None else rid
            try:
                resp = self._post_once(meta, body_img, header_id=hid, timeout=TIMEOUT_S)
            except requests.RequestException:
                time.sleep(0.05)   # brief backoff, then retry same id (E2)
                continue
            last = resp
            try:
                data = resp.json()
            except ValueError:
                data = {}
            # Simulate a dropped ACK for the first `drop_attempts` tries: the
            # request reached the server, but the device "didn't hear back" and
            # retries with the SAME id.
            if attempt < drop_attempts:
                continue
            if resp.status_code == 202:
                if not self._ack_matches(rid, data):
                    # Wrong-id ACK -> ignore, as if no ACK (E3). Retry.
                    continue
                return UploadResult(True, resp.status_code, rid, data, attempts, True)
            # A definitive error response (4xx) is NOT retryable — surface it.
            return UploadResult(False, resp.status_code, rid, data, attempts, False)
        # Exhausted retries without a matching ACK -> visible error (E2).
        body = last.json() if last is not None and last.content else {}
        return UploadResult(False, last.status_code if last else 0, rid, body, attempts, False)

    def result(self, request_id: str) -> dict:
        r = requests.get(f"{self.url}{protocol.EP_RESULT}/{request_id}",
                         headers={protocol.HDR_TOKEN: self.token}, timeout=TIMEOUT_S)
        return r.json()


# ---- CLI scenarios ----------------------------------------------------------
def _run_scenario(dev: FakeDevice, scenario: str, image_bytes: bytes) -> int:
    print(f"[fake-device] scenario={scenario}")
    if scenario == "normal":
        r = dev.upload(image_bytes)
    elif scenario == "duplicate":
        r1 = dev.upload(image_bytes, request_id="dupe_test_000001")
        print(f"  first:  ok={r1.ok} dup={r1.body.get('duplicate')}")
        r = dev.upload(image_bytes, request_id="dupe_test_000001")
        print(f"  second: ok={r.ok} dup={r.body.get('duplicate')} (expect duplicate=True)")
    elif scenario == "retry":
        r = dev.upload(image_bytes, drop_attempts=2)
        print(f"  attempts={r.attempts} (expect 3; server deduped the retries)")
    elif scenario == "wrong-header-id":
        r = dev.upload(image_bytes, header_id="mismatched_header_id")
    elif scenario == "tamper-hash":
        r = dev.upload(image_bytes, tamper_hash=True)
    elif scenario == "tamper-bytecount":
        r = dev.upload(image_bytes, tamper_bytecount=True)
    elif scenario == "oversize":
        r = dev.upload(image_bytes, oversize=True)
    elif scenario == "bad-meta":
        r = dev.upload(image_bytes, bad_meta=True)
    else:
        print(f"unknown scenario {scenario!r}")
        return 2
    print(f"  -> ok={r.ok} http={r.status_code} error={r.body.get('error')} "
          f"status={r.body.get('status')} attempts={r.attempts} acked={r.acked}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Glasses fake-device simulator")
    ap.add_argument("--url", default="http://127.0.0.1:8777")
    ap.add_argument("--token", default="CHANGE_ME_dev_token")
    ap.add_argument("--image", required=True, help="path to a JPEG to replay")
    ap.add_argument("--scenario", default="normal")
    args = ap.parse_args()

    image_bytes = Path(args.image).read_bytes()
    dev = FakeDevice(args.url, args.token)
    return _run_scenario(dev, args.scenario, image_bytes)


if __name__ == "__main__":
    raise SystemExit(main())
