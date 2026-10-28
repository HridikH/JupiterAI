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


def test_header_line_penalized():
    # a line that is only a table header should score below a real value line
    assert q._score_anchor("unless otherwise noted MIN MAX", "unless otherwise noted MIN MAX",
                           ["max", "min"]) < \
           q._score_anchor("Supply voltage 18 V", "Supply voltage 18 V", ["supply", "voltage"])
