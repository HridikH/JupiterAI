"""State machine + store tests (idempotency, stamping, U3 decision time)."""
from __future__ import annotations

import pytest

from server import protocol
from server.state import StateStore, RequestRecord


def test_start_creates_then_dedupes():
    s = StateStore()
    rec, created = s.start("req_000001", {"battery_mv": 3900})
    assert created and rec.status == "received"
    rec2, created2 = s.start("req_000001", {})
    assert not created2 and rec2 is rec


def test_stamp_transitions_and_timestamps():
    rec = RequestRecord(request_id="req_000001")
    rec.timestamps["received"] = 0.0
    rec.stamp("processing")
    rec.stamp("accepted")
    assert rec.status == "accepted"
    assert "processing" in rec.timestamps and "accepted" in rec.timestamps
    assert rec.timestamps["accepted"] >= 0.0


def test_unknown_status_rejected():
    rec = RequestRecord(request_id="req_000001")
    with pytest.raises(ValueError):
        rec.stamp("teleported")


def test_set_result_stamps_config():
    s = StateStore()
    s.start("req_000001", {})
    rec = s.set_result("req_000001", "accepted", {"part_number": "NE555P"},
                       config_version="thresholds/v1", config_hash="sha256:abc")
    pub = rec.to_public()
    assert pub["status"] == "accepted"
    assert pub["config_version"] == "thresholds/v1"
    assert pub["config_hash"] == "sha256:abc"
    assert pub["result"]["part_number"] == "NE555P"


def test_user_decision_recorded_separately():
    s = StateStore()
    s.start("req_000001", {})
    s.set_user_decision("req_000001", 4200.0)
    assert s.get("req_000001").to_public()["user_decision_ms"] == 4200.0


def test_terminal_states_constant_matches_protocol():
    assert set(protocol.TERMINAL_STATES) <= set(protocol.STATES)
    assert "processing" not in protocol.TERMINAL_STATES


def test_select_candidate_accepts_only_offered_parts_and_becomes_subject():
    s = StateStore()
    s.start("req_newer01", {})
    s.set_result("req_newer01", "accepted", {"part_number": "NE555P"})
    s.start("req_older01", {})          # pretend this is an earlier capture still on screen
    s.set_result("req_older01", "candidate_selection",
                 {"candidates": [{"part_number": "SN74HC04N", "package": "PDIP-14"},
                                 {"part_number": "SN74HC27N", "package": "PDIP-14"}],
                  "ocr_text": "SN74HC27N"})
    # a part that wasn't offered can't be chosen (nothing is invented)
    assert s.select_candidate("req_older01", "LM358P") is None
    assert s.get("req_older01").status == "candidate_selection"
    rec = s.select_candidate("req_older01", "SN74HC27N", 1500.0)
    assert rec.status == "accepted" and rec.result["part_number"] == "SN74HC27N"
    assert rec.result["selected_by"] == "operator" and rec.user_decision_ms == 1500.0
    assert s.latest_accepted().request_id == "req_older01"   # pinout/Q&A follow the pick
    assert s.select_candidate("req_older01", "SN74HC04N") is None   # already decided
    # a later automatic accept becomes the subject again
    s.set_result("req_newer01", "accepted", {"part_number": "NE555P"})
    assert s.latest_accepted().request_id == "req_newer01"
