"""Eval unit tests: report metrics, device-log parser, latency."""
from __future__ import annotations

from eval import report, parse_device_logs as pdl, latency


# ---- report (V2) ------------------------------------------------------------
def test_report_pass_and_accuracy():
    outs = []
    # 85 correct auto, 13 recaptures, 2 incorrect finals -> PASS
    outs += [report.TrialOutcome("P1", "accepted", "P1") for _ in range(85)]
    outs += [report.TrialOutcome("P2", "recapture") for _ in range(13)]
    outs += [report.TrialOutcome("P3", "accepted", "PX") for _ in range(2)]
    m = report.compute(outs)
    assert m["correct_total"] == 85
    assert m["incorrect_finals"] == 2
    assert m["recaptures"] == 13
    assert m["pass"] is True


def test_report_fail_on_too_many_incorrect():
    outs = [report.TrialOutcome("P1", "accepted", "P1") for _ in range(90)]
    outs += [report.TrialOutcome("P2", "accepted", "PX") for _ in range(3)]  # 3 incorrect
    m = report.compute(outs)
    assert m["pass"] is False


def test_assisted_counted_separately():
    outs = [report.TrialOutcome("P1", "candidate_selection", "P1", assisted=True),
            report.TrialOutcome("P2", "candidate_selection", "PX", assisted=True)]
    m = report.compute(outs)
    assert m["user_assisted_n"] == 2
    assert abs(m["user_assisted_accuracy"] - 0.5) < 1e-9
    assert m["automatic_only_n"] == 0


# ---- device log parser ------------------------------------------------------
GOOD_LOG = """
EVT 1000 BTN_PRESS
EVT 1005 STATE led=processing dur_ms=1
EVT 1010 WORKLIGHT state=on
EVT 1020 FOCUS_START req=aaaa
EVT 1500 FOCUS_DONE req=aaaa dur_ms=480
EVT 1600 CAPTURE_DONE req=aaaa bytes=1048576 w=2592 h=1944
EVT 1605 WORKLIGHT state=off
EVT 1650 UPLOAD_TRY req=aaaa attempt=0
EVT 1800 ACK_OK req=aaaa
EVT 9000 STATE led=accepted dur_ms=2
"""


def test_parser_all_pass():
    results = {r["id"]: r for r in pdl.check(pdl.parse_lines(GOOD_LOG))}
    assert results["C1"]["pass"] is True
    assert results["C2"]["pass"] is True
    assert results["E1"]["pass"] is True
    assert results["I3"]["pass"] is True


def test_parser_detects_oversize_and_slow_focus():
    bad = """
EVT 10 CAPTURE_DONE req=x bytes=3000000 w=2592 h=1944
EVT 20 FOCUS_DONE req=x dur_ms=1900
EVT 30 STATE led=processing dur_ms=400
"""
    results = {r["id"]: r for r in pdl.check(pdl.parse_lines(bad))}
    assert results["C1"]["pass"] is False      # oversized frame accepted
    assert results["C2"]["pass"] is False      # focus too slow
    assert results["I3"]["pass"] is False      # LED update too slow


def test_parser_wrong_ack_ignored_ok():
    log = "EVT 1 UPLOAD_TRY req=aaaa attempt=0\nEVT 2 ACK_IGNORED req=aaaa got=bbbb\nEVT 3 ACK_OK req=aaaa\n"
    results = {r["id"]: r for r in pdl.check(pdl.parse_lines(log))}
    assert results["E3"]["pass"] is True
    assert results["E1"]["pass"] is True


def test_parser_upload_fail_attempt_count():
    log = "EVT 1 UPLOAD_TRY req=a attempt=0\nEVT 2 UPLOAD_TIMEOUT req=a attempt=0\nEVT 3 UPLOAD_FAIL req=a attempts=3\n"
    results = {r["id"]: r for r in pdl.check(pdl.parse_lines(log))}
    assert results["E2"]["pass"] is True


# ---- latency (U3) -----------------------------------------------------------
def test_latency_excludes_candidate_and_computes_percentiles():
    # press->accepted at 5s and 15s; a candidate press with NO terminal led (excluded)
    log = """
EVT 0 BTN_PRESS
EVT 5000 STATE led=accepted dur_ms=1
EVT 6000 BTN_PRESS
EVT 6050 STATE led=processing dur_ms=1
EVT 20000 BTN_PRESS
EVT 35000 STATE led=recapture dur_ms=1
"""
    r = latency.report(log)
    assert r["n"] == 2                 # the middle (candidate/processing) press excluded
    assert r["median_ms"] in (10000, 10000.0)  # (5000+15000)/2
    assert r["p95_ms"] == 15000
