"""Payload fuzzer for V1 — probe each limit just below and just above.

Targets a running server and checks that the receiver accepts valid-boundary
payloads and rejects over-boundary ones with the RIGHT error code. Prints a
pass/fail table; exit code is nonzero if any case deviates from expectation.

    python -m tools.fuzzer --url http://127.0.0.1:8777 --token dev --image samples/x.jpg
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import protocol  # noqa: E402


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_min_jpeg() -> bytes:
    """A tiny but VALID jpeg, so size tests aren't confounded by decode errors."""
    try:
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (127, 127, 127)).save(buf, format="JPEG")
        return buf.getvalue()
    except Exception:
        # Minimal JPEG SOI...EOI (won't fully decode, but sniff-valid).
        return b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32 + b"\xff\xd9"


def _post(url, token, meta: dict, image: bytes, header_id=None, timeout=5.0):
    headers = {protocol.HDR_TOKEN: token}
    if header_id is not None:
        headers[protocol.HDR_REQUEST_ID] = header_id
    return requests.post(url + protocol.EP_CAPTURE, headers=headers,
                         data={"meta": json.dumps(meta)},
                         files={"image": ("c.jpg", image, "image/jpeg")}, timeout=timeout)


def _meta_for(image: bytes, rid: str, **over) -> dict:
    m = {"request_id": rid, "proto": protocol.PROTO_VERSION, "width": 8, "height": 8,
         "byte_count": len(image), "sha256": _sha(image), "battery_mv": 3900}
    m.update(over)
    return m


def run(url: str, token: str, image: bytes) -> int:
    cases = []  # (name, expect_http, expect_error_or_None, send_fn)

    def rid():
        return "fuzz" + uuid.uuid4().hex[:12]

    # --- JPEG size boundary (limit = 2 MiB) ---
    small = image
    at_limit = image + b"\x00" * (protocol.MAX_JPEG_BYTES - len(image))      # exactly MAX
    over = image + b"\x00" * (protocol.MAX_JPEG_BYTES - len(image) + 1)      # MAX + 1
    cases.append(("jpeg_below_limit", 202, None,
                  lambda r: _post(url, token, _meta_for(small, r), small, r)))
    cases.append(("jpeg_at_limit", 202, None,
                  lambda r: _post(url, token, _meta_for(at_limit, r), at_limit, r)))
    cases.append(("jpeg_over_limit", 413, protocol.E_TOO_LARGE,
                  lambda r: _post(url, token, _meta_for(over, r), over, r)))

    # --- metadata size boundary (limit = 16 kB) ---
    pad_ok = "x" * (protocol.MAX_META_BYTES - 400)      # comfortably under once serialized
    pad_big = "x" * (protocol.MAX_META_BYTES + 100)     # guaranteed over
    cases.append(("meta_below_limit", 202, None,
                  lambda r: _post(url, token, _meta_for(small, r, note=pad_ok), small, r)))
    cases.append(("meta_over_limit", 413, protocol.E_TOO_LARGE,
                  lambda r: _post(url, token, _meta_for(small, r, note=pad_big), small, r)))

    # --- byte_count off by one in each direction ---
    cases.append(("bytecount_minus_one", 400, protocol.E_SIZE_MISMATCH,
                  lambda r: _post(url, token, _meta_for(small, r, byte_count=len(small) - 1), small, r)))
    cases.append(("bytecount_plus_one", 400, protocol.E_SIZE_MISMATCH,
                  lambda r: _post(url, token, _meta_for(small, r, byte_count=len(small) + 1), small, r)))

    # --- hash tamper ---
    cases.append(("bad_sha256", 400, protocol.E_HASH_MISMATCH,
                  lambda r: _post(url, token, _meta_for(small, r, sha256="0" * 64), small, r)))

    # --- auth ---
    cases.append(("missing_token", 401, protocol.E_UNAUTHORIZED,
                  lambda r: _post(url, "", _meta_for(small, r), small, r)))
    cases.append(("wrong_token", 401, protocol.E_UNAUTHORIZED,
                  lambda r: _post(url, token + "x", _meta_for(small, r), small, r)))

    # --- request id ---
    cases.append(("header_id_mismatch", 400, protocol.E_BAD_REQUEST,
                  lambda r: _post(url, token, _meta_for(small, r), small, header_id="different_id_x")))
    cases.append(("short_request_id", 400, protocol.E_BAD_REQUEST,
                  lambda r: _post(url, token, _meta_for(small, "ab"), small, header_id="ab")))

    # --- decode ---
    junk = b"not a jpeg at all" * 4
    cases.append(("not_a_jpeg", 422, protocol.E_DECODE_FAILED,
                  lambda r: _post(url, token, _meta_for(junk, r), junk, r)))

    # --- missing required meta field ---
    def _bad_meta(r):
        m = _meta_for(small, r); m.pop("sha256")
        return _post(url, token, m, small, r)
    cases.append(("missing_meta_field", 400, protocol.E_BAD_META, _bad_meta))

    print(f"{'case':24s} {'expect':>18s} {'got':>18s}  result")
    print("-" * 74)
    failures = 0
    for name, exp_http, exp_err, fn in cases:
        try:
            resp = fn(rid())
            body = resp.json() if resp.content else {}
        except Exception as e:
            print(f"{name:24s} {'(request error)':>18s} {str(e)[:16]:>18s}  FAIL")
            failures += 1
            continue
        got_err = body.get("error")
        ok = resp.status_code == exp_http and (exp_err is None or got_err == exp_err)
        failures += 0 if ok else 1
        exp_s = f"{exp_http}/{exp_err or 'ok'}"
        got_s = f"{resp.status_code}/{got_err or 'ok'}"
        print(f"{name:24s} {exp_s:>18s} {got_s:>18s}  {'PASS' if ok else 'FAIL'}")

    print("-" * 74)
    print(f"{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="V1 payload fuzzer")
    ap.add_argument("--url", default="http://127.0.0.1:8777")
    ap.add_argument("--token", default="CHANGE_ME_dev_token")
    ap.add_argument("--image", help="optional seed JPEG; a tiny one is generated if omitted")
    args = ap.parse_args()
    image = Path(args.image).read_bytes() if args.image else _make_min_jpeg()
    return run(args.url, args.token, image)


if __name__ == "__main__":
    raise SystemExit(main())
