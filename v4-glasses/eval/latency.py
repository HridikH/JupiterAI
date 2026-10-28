"""Latency report (U3): button press -> first actionable result.

Parses the device log and pairs each BTN_PRESS with the next TERMINAL status LED
transition (accepted / recapture / error). candidate_selection leaves the device
in 'processing', so those are excluded automatically — which also excludes the
operator's decision time, as U3 requires. Reports median and nearest-rank p95.

    python -m eval.latency eval/datasets/device_run.log

Targets: median ~10 s, p95 <= 20 s.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.parse_device_logs import parse_lines  # noqa: E402

TERMINAL_LEDS = {"accepted", "recapture", "error"}
TARGET_MEDIAN_MS = 10_000
TARGET_P95_MS = 20_000


def nearest_rank_percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return float("nan")
    import math
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_vals)))
    return sorted_vals[rank - 1]


def latencies_ms(text: str) -> list[int]:
    events = parse_lines(text)
    out = []
    press_ms = None
    for e in events:
        if e.tag == "BTN_PRESS":
            press_ms = e.ms
        elif e.tag == "STATE" and e.fields.get("led") in TERMINAL_LEDS and press_ms is not None:
            out.append(e.ms - press_ms)
            press_ms = None
    return out


def report(text: str) -> dict:
    vals = sorted(latencies_ms(text))
    if not vals:
        return {"n": 0}
    median = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals)//2 - 1] + vals[len(vals)//2]) / 2
    p95 = nearest_rank_percentile(vals, 95)
    return {
        "n": len(vals),
        "median_ms": median,
        "p95_ms": p95,
        "min_ms": vals[0],
        "max_ms": vals[-1],
        "median_pass": median <= TARGET_MEDIAN_MS,
        "p95_pass": p95 <= TARGET_P95_MS,
    }


def format_report(r: dict) -> str:
    if not r.get("n"):
        return "no actionable results found in log"
    return (
        "===== Latency (U3: press -> first actionable result) =====\n"
        f"n:        {r['n']}  (candidate selections excluded; user decision excluded)\n"
        f"median:   {r['median_ms']/1000:.2f} s   (target ~10 s)   "
        f"{'PASS' if r['median_pass'] else 'FAIL'}\n"
        f"p95:      {r['p95_ms']/1000:.2f} s   (target <=20 s)  "
        f"{'PASS' if r['p95_pass'] else 'FAIL'}   [nearest-rank]\n"
        f"range:    {r['min_ms']/1000:.2f}–{r['max_ms']/1000:.2f} s"
    )


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m eval.latency <logfile>", file=sys.stderr)
        return 2
    r = report(Path(sys.argv[1]).read_text())
    print(format_report(r))
    return 0 if (r.get("n") and r["median_pass"] and r["p95_pass"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
