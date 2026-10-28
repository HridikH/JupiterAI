"""Datasheet page index tests (D3) — section separation + cited search."""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")  # PyMuPDF

from catalog import page_index


def _make_pdf(path: Path):
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "NE555 Precision Timer\nDescription\nThe device is a timer.")
    p2 = doc.new_page()
    p2.insert_text((72, 72),
                   "Absolute Maximum Ratings\nSupply voltage 18 V\nStorage temperature 150 C\n"
                   "Electrical Characteristics\nfoo")
    p3 = doc.new_page()
    p3.insert_text((72, 72),
                   "Recommended Operating Conditions\nSupply voltage 4.5 to 15 V\n"
                   "Thermal Information\nbar")
    doc.save(path)
    doc.close()


def test_sections_are_separated(tmp_path):
    pdf = tmp_path / "ne555.pdf"
    _make_pdf(pdf)
    idx = page_index.build_index(pdf)
    assert len(idx.pages) == 3
    assert idx.absolute_maximum_ratings and idx.absolute_maximum_ratings[0].page == 2
    assert idx.recommended_operating_conditions and \
        idx.recommended_operating_conditions[0].page == 3
    # abs-max text must NOT bleed the recommended-op section
    amr = idx.absolute_maximum_ratings[0].text.lower()
    assert "storage temperature" in amr
    assert "recommended operating" not in amr


def test_search_returns_cited_page(tmp_path):
    pdf = tmp_path / "ne555.pdf"
    _make_pdf(pdf)
    idx = page_index.build_index(pdf)
    hits = idx.search("storage temperature", k=1)
    assert hits and hits[0][0] == 2   # cited to page 2


def test_search_unavailable_returns_empty(tmp_path):
    pdf = tmp_path / "ne555.pdf"
    _make_pdf(pdf)
    idx = page_index.build_index(pdf)
    assert idx.search("quantum chromodynamics") == []
