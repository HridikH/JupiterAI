"""Datasheet Q&A ranking tests (D3) — ratings questions hit value rows, not prose."""
from __future__ import annotations

from dataclasses import dataclass

from server import datasheet_qa as q


@dataclass
class Sec:
    page: int
    text: str
    name: str


# A prose page that mentions 'voltage' a lot (the old failure mode) + a ratings
# section page that holds the actual value.
PAGES = [
    (3, "Feature Description Monostable Operation the output voltage goes low when "
        "the trigger voltage output voltage operation voltage"),
    (4, "Absolute Maximum Ratings\nSupply voltage 18 V\nOutput current 225 mA\n"
        "unless otherwise noted MIN MAX"),
]
SECTIONS = [Sec(4, "Absolute Maximum Ratings\nSupply voltage 18 V\n"
                   "Output current 225 mA\nunless otherwise noted MIN MAX", "absolute_maximum_ratings")]


def test_ratings_question_prefers_value_row_over_prose():
    hits = q.rank_snippets(PAGES, SECTIONS, "maximum supply voltage")
    assert hits
    top = hits[0]
    assert top["page"] == 4                      # the ratings page, not the prose page 3
    assert "18 V" in top["text"]
    assert top["section"] == "absolute_maximum_ratings"


def test_value_with_unit_beats_header_line():
    hits = q.rank_snippets(PAGES, SECTIONS, "output current")
    assert hits[0]["page"] == 4
    assert "225 mA" in hits[0]["text"]
    assert "MIN MAX" not in hits[0]["text"][:10]   # not the boilerplate header


def test_empty_question_returns_nothing():
    assert q.rank_snippets(PAGES, SECTIONS, "a") == []


def test_no_match_returns_empty():
    assert q.rank_snippets(PAGES, [], "quantum chromodynamics lagrangian") == []


# ---- pinout questions ----
PIN_TABLE_TEXT = (
    "Pin Functions\nPIN\nTYPE\nDESCRIPTION\nNAME\nNO.\n"
    "P (PDIP),\nFK (LCCC)\n"
    "GND\n1\n2\n—\nGround\n"
    "TRIG\n2\n5\nInput\nStart of timing input\n"
    "OUT\n3\n7\nOutput\nHigh current timer output\n"
    "VCC\n8\n20\n—\nInput supply voltage, 4.5V to 16V.\nwww.ti.com footer junk\n"
)


def test_parse_pin_table_picks_dip_column():
    # col0 = DIP (1,2,3,8), col1 = LCCC (2,5,7,20). With pkg=PDIP-8 the DIP column
    # (numbers that fit 1..8) must be chosen, not the LCCC column.
    t = q.parse_pin_table(PIN_TABLE_TEXT, package="PDIP-8")
    assert t["GND"]["no"] == 1
    assert t["TRIG"]["no"] == 2
    assert t["OUT"]["no"] == 3
    assert t["VCC"]["no"] == 8
    assert "www." not in t["VCC"]["desc"]      # footer trimmed


def test_parse_pin_table_handles_endash_minus():
    # op-amp names with en-dash minus must parse (IN1– == IN1-)
    txt = ("Pin Functions\nNAME\nNO.\n"
           "OUT1\n1\nOutput\nOutput\n"
           "IN1–\n2\nInput\nNeg input\n"   # en-dash
           "IN1+\n3\nInput\nPos input\n"
           "V–\n4\n—\nGround\n")
    t = q.parse_pin_table(txt, package="PDIP-8")
    assert t["IN1-"]["no"] == 2
    assert t["V-"]["no"] == 4


def test_pin_answer_maps_ground(monkeypatch):
    monkeypatch.setattr(q, "get_pins", lambda pn: {
        "part": pn, "page": 3,
        "pins": [{"name": "GND", "no": 1, "desc": "Ground"},
                 {"name": "VCC", "no": 8, "desc": "Supply"}]})
    r = q._pin_answer(object(), "NE555P", "which pin do i connect to ground")
    assert r and r["source"] == "pin_table"
    assert "pin 1" in r["answer"] and r["citation"]["page"] == 3


def test_pin_answer_lists_all_for_generic(monkeypatch):
    monkeypatch.setattr(q, "get_pins", lambda pn: {
        "part": pn, "page": 3,
        "pins": [{"name": "GND", "no": 1, "desc": ""},
                 {"name": "VCC", "no": 8, "desc": ""}]})
    r = q._pin_answer(object(), "NE555P", "what are the pins")
    assert "GND = pin 1" in r["answer"] and "VCC = pin 8" in r["answer"]


def test_header_line_penalized():
    # a line that is only a table header should score below a real value line
    assert q._score_anchor("unless otherwise noted MIN MAX", "unless otherwise noted MIN MAX",
                           ["max", "min"]) < \
           q._score_anchor("Supply voltage 18 V", "Supply voltage 18 V", ["supply", "voltage"])
