"""Datasheet page index (PyMuPDF) — per-page text + section extraction.

Every displayed spec must cite its source page (D3). This builds a per-page text
index and separately captures two safety-critical sections that must never be
conflated:
  * absolute_maximum_ratings       (stress limits — exceeding can destroy the part)
  * recommended_operating_conditions (the normal-use envelope)

An unsupported question returns an explicit "unavailable" result (handled by the
caller, e.g. server/datasheet_qa.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ABS_MAX_RE = re.compile(r"absolute\s+maximum\s+ratings", re.I)
REC_OP_RE = re.compile(r"recommended\s+operating\s+conditions", re.I)
# a heading that ends the captured section
NEXT_HEADING_RE = re.compile(
    r"^\s*\d*\.?\s*(electrical characteristics|thermal information|dissipation|"
    r"recommended operating|absolute maximum|features|applications|description|"
    r"pin (configuration|functions)|revision history)\b", re.I | re.M)


@dataclass
class Page:
    number: int          # 1-indexed
    text: str


@dataclass
class Section:
    name: str
    page: int
    text: str


@dataclass
class DatasheetIndex:
    path: Path
    pages: list[Page] = field(default_factory=list)
    absolute_maximum_ratings: list[Section] = field(default_factory=list)
    recommended_operating_conditions: list[Section] = field(default_factory=list)

    def search(self, query: str, k: int = 3) -> list[tuple[int, str]]:
        """Keyword-overlap page search. Returns [(page_number, snippet), ...].

        Deterministic and offline; no LLM. Empty list => 'unavailable' upstream.
        """
        terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
        if not terms:
            return []
        scored = []
        for pg in self.pages:
            low = pg.text.lower()
            score = sum(low.count(t) for t in terms)
            if score:
                scored.append((score, pg))
        scored.sort(key=lambda s: s[0], reverse=True)
        out = []
        for _, pg in scored[:k]:
            out.append((pg.number, _snippet(pg.text, terms)))
        return out


def _snippet(text: str, terms: list[str], width: int = 240) -> str:
    low = text.lower()
    pos = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
    start = max(0, pos - width // 2)
    return " ".join(text[start:start + width].split())


def _capture_section(text: str, start: int) -> str:
    rest = text[start:]
    m = NEXT_HEADING_RE.search(rest, pos=40)  # skip the heading itself
    return rest[: m.start()] if m else rest[:1200]


def build_index(pdf_path: Path | str) -> DatasheetIndex:
    import fitz  # PyMuPDF

    pdf_path = Path(pdf_path)
    idx = DatasheetIndex(path=pdf_path)
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            idx.pages.append(Page(number=i, text=text))
            for m in ABS_MAX_RE.finditer(text):
                idx.absolute_maximum_ratings.append(
                    Section("absolute_maximum_ratings", i, _capture_section(text, m.start())))
            for m in REC_OP_RE.finditer(text):
                idx.recommended_operating_conditions.append(
                    Section("recommended_operating_conditions", i, _capture_section(text, m.start())))
    return idx


def index_for_part(part_number: str) -> Optional[DatasheetIndex]:
    """Resolve a part's cached PDF via the catalog and build its index, offline."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from server.catalog_api import load

    cat = load()
    part = cat.by_number(part_number)
    if not part or not part.datasheet.get("path"):
        return None
    pdf = Path(__file__).resolve().parent.parent / part.datasheet["path"]
    if not pdf.exists():
        return None
    return build_index(pdf)
