"""Per-request state machine (docs/protocol.md §6), keyed by request_id.

Thread-safe in-memory store. Each record tracks status, per-stage timestamps,
the frozen-threshold version+hash used, and the outcome payload. Dedup lives
here: `start()` returns (record, created) and refuses to recreate an existing id,
which is what makes retries idempotent (E1/E2, V1 step 4).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import protocol


@dataclass
class RequestRecord:
    request_id: str
    status: str = "received"
    created_monotonic: float = field(default_factory=time.monotonic)
    created_wall: float = field(default_factory=time.time)
    timestamps: dict[str, float] = field(default_factory=dict)   # status -> seconds since created
    config_version: Optional[str] = None
    config_hash: Optional[str] = None
    result: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    # U3: user decision time on the candidate screen, logged SEPARATELY from
    # machine latency. Set by the UI when the operator picks a candidate.
    user_decision_ms: Optional[float] = None

    def stamp(self, status: str) -> None:
        if status not in protocol.STATES:
            raise ValueError(f"unknown status {status!r}")
        self.status = status
        self.timestamps[status] = time.monotonic() - self.created_monotonic

    def to_public(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "timestamps": {k: round(v, 6) for k, v in self.timestamps.items()},
            "config_version": self.config_version,
            "config_hash": self.config_hash,
            "result": self.result,
            "battery_mv": self.meta.get("battery_mv"),
            "user_decision_ms": self.user_decision_ms,
        }


class StateStore:
    def __init__(self) -> None:
        self._records: dict[str, RequestRecord] = {}
        self._lock = threading.Lock()
        self._last_accepted: Optional[str] = None   # request_id, by time of acceptance

    def start(self, request_id: str, meta: dict[str, Any]) -> tuple[RequestRecord, bool]:
        """Create a record for request_id, or return the existing one.

        Returns (record, created). created=False means this is a duplicate and
        the caller MUST NOT start a second identification task (idempotency).
        """
        with self._lock:
            rec = self._records.get(request_id)
            if rec is not None:
                return rec, False
            rec = RequestRecord(request_id=request_id, meta=meta)
            rec.timestamps["received"] = 0.0
            self._records[request_id] = rec
            return rec, True

    def get(self, request_id: str) -> Optional[RequestRecord]:
        with self._lock:
            return self._records.get(request_id)

    def set_result(self, request_id: str, status: str, result: dict[str, Any],
                   config_version: Optional[str] = None,
                   config_hash: Optional[str] = None) -> RequestRecord:
        with self._lock:
            rec = self._records[request_id]
            rec.result = result
            if config_version is not None:
                rec.config_version = config_version
            if config_hash is not None:
                rec.config_hash = config_hash
            rec.stamp(status)
            if status == "accepted":
                self._last_accepted = request_id
            return rec

    def select_candidate(self, request_id: str, part_number: str,
                         ms: Optional[float] = None) -> Optional[RequestRecord]:
        """Operator picked one of THIS request's candidates on the candidate screen.

        Only a part that the resolver itself offered for this request can be chosen
        (nothing is invented); the record becomes 'accepted', tagged as an operator
        decision, and is now the subject of pinout/Q&A. Returns None if refused."""
        with self._lock:
            rec = self._records.get(request_id)
            if rec is None or rec.status != "candidate_selection":
                return None
            res = rec.result or {}
            cands = res.get("candidates") or []
            pick = next((c for c in cands if c.get("part_number") == part_number), None)
            if pick is None:
                return None
            rec.result = {
                "part_number": pick["part_number"],
                "manufacturer": pick.get("manufacturer"),
                "package": pick.get("package"),
                "score": pick.get("score"),
                "margin": res.get("margin"),
                "ocr_text": res.get("ocr_text", ""),
                "matched_string": "",
                "datasheet": pick.get("datasheet"),
                "source": "operator",
                "verified": True,
                "selected_by": "operator",
                "candidates": cands,
            }
            if ms is not None:
                rec.user_decision_ms = ms
            rec.stamp("accepted")
            self._last_accepted = request_id
            return rec

    def set_user_decision(self, request_id: str, ms: float) -> None:
        with self._lock:
            rec = self._records.get(request_id)
            if rec is not None:
                rec.user_decision_ms = ms

    def all_ids(self) -> list[str]:
        with self._lock:
            return list(self._records)

    def latest_accepted(self) -> Optional[RequestRecord]:
        """Most recently accepted record — the subject of voice/pinout commands.
        'Most recent' is by time of acceptance, so picking a candidate on an older
        capture makes it the subject."""
        with self._lock:
            last = self._records.get(self._last_accepted or "")
            if last is not None and last.status == "accepted":
                return last
            for rid in reversed(self._records):
                rec = self._records[rid]
                if rec.status == "accepted":
                    return rec
            return None

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._last_accepted = None
