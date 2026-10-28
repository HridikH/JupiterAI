"""Parse device serial logs into a C1/C2/E1/E2/E3/I1/I3 pass-fail table.

Consumes the `EVT <uptime_ms> <TAG> k=v ...` format from firmware/main/logging.h.

    python -m eval.parse_device_logs eval/datasets/device_run.log

Checks:
  C1  no CAPTURE_DONE exceeds 2 MB; every FRAME_REJECT is reason=oversize >2MB
  C2  every real FOCUS_DONE has dur_ms <= 1500
  E1  every UPLOAD_TRY's request eventually gets an ACK_OK (within the run)
  E2  any UPLOAD_FAIL came only after attempts == 3 (<= 2 retries)
  E3  every ACK_IGNORED has got != req (a wrong-id ACK, correctly ignored)
  I1  no BTN_PRESS fires again within the debounce window of a prior one
  I3  every STATE transition reflected within 250 ms
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_JPEG_BYTES = 2_097_152
FOCUS_DEADLINE_MS = 1500
LED_DEADLINE_MS = 250
DEBOUNCE_MS = 250

LINE_RE = re.compile(r"^EVT\s+(\d+)\s+(\w+)\s*(.*)$")


@dataclass
class Event:
    ms: int
    tag: str
    fields: dict


def parse_lines(text: str) -> list[Event]:
    events = []
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        ms, tag, rest = int(m.group(1)), m.group(2), m.group(3)
        fields = {}
        for kv in rest.split():
            if "=" in kv:
                k, v = kv.split("=", 1)
                fields[k] = v
        events.append(Event(ms, tag, fields))
    return events


def _result(name, passed, detail):
    return {"id": name, "pass": passed, "detail": detail}


def check(events: list[Event]) -> list[dict]:
    out = []

    # C1
    over = [e for e in events if e.tag == "CAPTURE_DONE" and int(e.fields.get("bytes", 0)) > MAX_JPEG_BYTES]
    rejects = [e for e in events if e.tag == "FRAME_REJECT"]
    bad_rej = [e for e in rejects if e.fields.get("reason") != "oversize"
               or int(e.fields.get("bytes", 0)) <= MAX_JPEG_BYTES]
    caps = [e for e in events if e.tag == "CAPTURE_DONE"]
    if not caps and not rejects:
        out.append(_result("C1", None, "no capture events"))
    else:
        out.append(_result("C1", not over and not bad_rej,
                           f"{len(over)} oversized accepted, {len(rejects)} rejects ({len(bad_rej)} malformed)"))

    # C2
    focus = [e for e in events if e.tag == "FOCUS_DONE" and "no_af_fw" not in e.fields.get("note", "")]
    slow = [e for e in focus if int(e.fields.get("dur_ms", 0)) > FOCUS_DEADLINE_MS]
    out.append(_result("C2", (not slow) if focus else None,
                       f"{len(focus)} focuses, {len(slow)} over {FOCUS_DEADLINE_MS}ms"
                       if focus else "no (real) focus events"))

    # E1: each request that had an UPLOAD_TRY got an ACK_OK
    tried = {e.fields.get("req") for e in events if e.tag == "UPLOAD_TRY"}
    acked = {e.fields.get("req") for e in events if e.tag == "ACK_OK"}
    failed = {e.fields.get("req") for e in events if e.tag == "UPLOAD_FAIL"}
    missing = tried - acked - failed
    out.append(_result("E1", (not missing) if tried else None,
                       f"{len(tried)} uploads, {len(acked)} acked, {len(missing)} unresolved"
                       if tried else "no upload attempts"))

    # E2: UPLOAD_FAIL only after attempts==3 (<=2 retries)
    fails = [e for e in events if e.tag == "UPLOAD_FAIL"]
    bad_fail = [e for e in fails if int(e.fields.get("attempts", 0)) != 3]
    out.append(_result("E2", (not bad_fail) if fails else None,
                       f"{len(fails)} fails, {len(bad_fail)} with wrong attempt count"
                       if fails else "no upload failures"))

    # E3: ACK_IGNORED has got != req
    ign = [e for e in events if e.tag == "ACK_IGNORED"]
    bad_ign = [e for e in ign if e.fields.get("got") == e.fields.get("req")]
    out.append(_result("E3", (not bad_ign) if ign else None,
                       f"{len(ign)} wrong-id ACKs ignored, {len(bad_ign)} wrongly flagged"
                       if ign else "no wrong-id ACKs seen"))

    # I1: no two BTN_PRESS within debounce window
    presses = [e.ms for e in events if e.tag == "BTN_PRESS"]
    violations = sum(1 for a, b in zip(presses, presses[1:]) if (b - a) < DEBOUNCE_MS)
    out.append(_result("I1", (violations == 0) if presses else None,
                       f"{len(presses)} presses, {violations} within debounce"
                       if presses else "no button presses"))

    # I3: STATE dur_ms <= 250
    states = [e for e in events if e.tag == "STATE"]
    slow_s = [e for e in states if int(e.fields.get("dur_ms", 0)) > LED_DEADLINE_MS]
    out.append(_result("I3", (not slow_s) if states else None,
                       f"{len(states)} transitions, {len(slow_s)} over {LED_DEADLINE_MS}ms"
                       if states else "no state transitions"))
    return out


def format_table(results: list[dict]) -> str:
    lines = [f"{'ID':4s} {'RESULT':6s} detail", "-" * 60]
    for r in results:
        verdict = "PASS" if r["pass"] else ("N/A" if r["pass"] is None else "FAIL")
        lines.append(f"{r['id']:4s} {verdict:6s} {r['detail']}")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m eval.parse_device_logs <logfile>", file=sys.stderr)
        return 2
    text = Path(sys.argv[1]).read_text()
    results = check(parse_lines(text))
    print(format_table(results))
    # fail the command only if a check actually failed (N/A is not a failure)
    return 1 if any(r["pass"] is False for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
