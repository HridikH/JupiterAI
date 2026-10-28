"""Small timing helpers for latency/throughput measurement."""
from __future__ import annotations

import math
import time
from contextlib import contextmanager


@contextmanager
def timer(label: str = ""):
    t0 = time.monotonic()
    try:
        yield lambda: (time.monotonic() - t0) * 1000.0
    finally:
        if label:
            print(f"[timing] {label}: {(time.monotonic() - t0)*1000:.1f} ms")


def nearest_rank_p(values, pct: float) -> float:
    """Nearest-rank percentile (same method as the latency report)."""
    if not values:
        return float("nan")
    s = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(s)))
    return s[rank - 1]


def median(values) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
