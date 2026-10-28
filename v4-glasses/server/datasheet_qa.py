"""OPTIONAL datasheet Q&A — answers strictly from retrieved page text, with citation.

HARD RULES (enforced by construction):
  * This module NEVER identifies a part and is never called during identification.
    It is handed an already-accepted part_number and a question about its datasheet.
  * Answers come ONLY from text retrieved out of the cached PDF (offline). Every
    answer carries a {part_number, page} citation (D3).
  * If no page matches, it returns {"available": False} — an explicit "unavailable"
    result, never a guess.

Default backend is EXTRACTIVE (no LLM): it returns the best-matching page snippet.
If GLASSES_DATASHEET_QA=1 AND the parent repo's MLX stack is importable, it will
instead have the local Qwen model phrase an answer *grounded in the retrieved page
text only*, still with the citation and the same "unavailable" fallback.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from . import config
from .logging_setup import get

log = get("datasheet_qa")

# A number followed by an electrical unit — ratings answers almost always carry one.
_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s?(?:v|mv|kv|a|ma|ua|µa|°c|c|w|mw|kw|hz|khz|mhz|ns|us|µs|ms|ohm|Ω|%)\b",
    re.I)
# Words that signal a datasheet *ratings* question -> search the ratings sections first.
_RATINGS_RE = re.compile(
    r"\b(voltage|current|temperature|power|rating|ratings|supply|vcc|vdd|operating|"
    r"absolute|maximum|minimum|max|min|dissipation|frequency|threshold|range)\b", re.I)


def _terms(question: str) -> list[str]:
    return [t for t in re.split(r"\W+", question.lower()) if len(t) > 2]


_HEADER_RE = re.compile(r"\bmin\b.*\bmax\b|\bmax\b.*\bmin\b|unless otherwise noted", re.I)


def _score_anchor(line: str, window: str, terms: list[str]) -> int:
    """Score an anchor line by query-term overlap, with the numeric-value bonus
    taken over the whole WINDOW (the value often sits a line or two below the
    label in an extracted table), and a penalty for boilerplate table headers."""
    low = line.lower()
    present = sum(1 for t in set(terms) if t in low)
    if present == 0:
        return 0
    score = present * 2
    if _UNIT_RE.search(window):
        score += 3                      # a real value with a unit — what we want
    elif any(ch.isdigit() for ch in window):
        score += 1
    if _HEADER_RE.search(low):
        score -= 2                      # "MIN MAX", "unless otherwise noted" — not an answer
    return score


def _clean(s: str, width: int = 220) -> str:
    return " ".join(s.split())[:width]


def rank_snippets(pages, sections, question: str, k: int = 3) -> list[dict]:
    """Rank line-level snippets. Ratings questions look in the abs-max /
    recommended-operating sections FIRST (so a value table beats prose). Returns
    [{'page','text','section'}], best first. Pure + deterministic (no LLM).

    `pages`    : iterable of (page_number, page_text)
    `sections` : iterable of objects with .page and .text (abs-max + recommended-op)
    """
    terms = _terms(question)
    if not terms:
        return []
    ratings = bool(_RATINGS_RE.search(question))
    cands: list[tuple[int, int, str, str | None]] = []

    def scan(text, page, section, bonus=0):
        lines = [l for l in text.splitlines() if l.strip()]
        for i, l in enumerate(lines):
            window = _clean(" ".join(lines[i:i + 3]))  # value may be a line or two below
            sc = _score_anchor(l, window, terms)
            if sc > 0:
                cands.append((sc + bonus, page, window, section))

    if ratings:
        for sec in sections:
            scan(sec.text, sec.page, sec.name, bonus=3)
    for num, text in pages:
        scan(text, num, None, bonus=0)

    cands.sort(key=lambda x: x[0], reverse=True)
    out, seen = [], set()
    for _, page, text, section in cands:
        key = (page, text[:40])
        if key in seen:
            continue
        seen.add(key)
        out.append({"page": page, "text": text, "section": section})
        if len(out) >= k:
            break
    return out


# ---- pinout questions ------------------------------------------------------
_PIN_Q_RE = re.compile(r"\b(pin|pins|pinout|pin-?out|connect|wire|hook\s*up|terminal)\b", re.I)
_PIN_NAMES = {"GND", "VCC", "VDD", "OUT", "TRIG", "RESET", "THRES", "DISCH", "CONT", "NC", "CV"}
# ordered signal -> question keywords (first match wins)
_SIGNAL_ALIASES = [
    ("GND", ["ground", "gnd", "vss", "0v", "negative"]),
    ("VCC", ["vcc", "vdd", "supply", "power", "v+", "positive"]),
    ("TRIG", ["trigger", "trig"]),
    ("RESET", ["reset"]),
    ("THRES", ["threshold", "thres"]),
    ("DISCH", ["discharge", "disch"]),
    ("CONT", ["control voltage", "control", "cont", "cv"]),
    ("OUT", ["output", "out"]),
]


def parse_pin_table(text: str) -> dict[str, dict]:
    """Parse a 'Pin Functions' section into {NAME: {numbers:[...], desc:str}}.

    Handles the typical TI layout where NAME, pin NO.(s), TYPE, and DESCRIPTION
    extract as separate lines. The first number is the primary (DIP/SOIC) pin."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    res: dict[str, dict] = {}
    i = 0
    while i < len(lines):
        nm = re.sub(r"[^A-Z]", "", lines[i].upper())
        if nm in _PIN_NAMES and lines[i].strip().upper() == nm:
            j, nums = i + 1, []
            while j < len(lines) and re.fullmatch(r"[\d,\s]+", lines[j]):
                nums += [int(x) for x in re.findall(r"\d+", lines[j])]
                j += 1
            desc = []
            while j < len(lines):
                cand = re.sub(r"[^A-Z]", "", lines[j].upper())
                if (cand in _PIN_NAMES and lines[j].strip().upper() == cand) \
                        or re.fullmatch(r"[\d,\s]+", lines[j]):
                    break
                desc.append(lines[j])
                j += 1
            d = " ".join(desc).strip(" —").strip()
            d = re.split(r"\s+www\.|\s+SLFS|\s+Copyright|\s+Submit Document", d)[0]  # drop footer
            res[nm] = {"numbers": nums, "desc": d[:90].strip()}
            i = j
        else:
            i += 1
    return res


def _pin_answer(idx, part_number: str, question: str) -> dict | None:
    secs = list(getattr(idx, "pin_functions", []))
    if not secs:
        return None
    table: dict[str, dict] = {}
    page = None
    for s in secs:
        parsed = parse_pin_table(s.text)
        if parsed:
            table.update(parsed)
            page = page or s.page
    if not table:
        return None
    ql = question.lower()
    for sig, keys in _SIGNAL_ALIASES:
        if sig in table and table[sig]["numbers"] and any(k in ql for k in keys):
            num = table[sig]["numbers"][0]
            desc = table[sig]["desc"]
            ans = f"{sig} ({desc}) is pin {num}." if desc else f"{sig} is pin {num}."
            return {"available": True, "answer": ans, "source": "pin_table",
                    "citation": {"part_number": part_number, "page": page, "section": "pin_functions"}}
    # generic "what are the pins" -> list them
    listing = ", ".join(f"{n} = pin {v['numbers'][0]}" for n, v in table.items() if v["numbers"])
    if listing:
        return {"available": True, "answer": f"Pin map (primary package): {listing}.",
                "source": "pin_table",
                "citation": {"part_number": part_number, "page": page, "section": "pin_functions"}}
    return None


_GROUNDED_SYSTEM = (
    "You answer ONLY from the datasheet excerpt provided. If the excerpt does not "
    "contain the answer, reply exactly 'UNAVAILABLE'. Never state or guess a part "
    "number. Be concise and quote values verbatim."
)


def _search(part_number: str, question: str):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from catalog.page_index import index_for_part

    idx = index_for_part(part_number)
    if idx is None:
        return None, []
    pages = [(p.number, p.text) for p in idx.pages]
    sections = list(idx.absolute_maximum_ratings) + list(idx.recommended_operating_conditions)
    hits = rank_snippets(pages, sections, question, k=3)
    return idx, hits


def _qwen_answer(question: str, page_text: str) -> str | None:
    """Best-effort local Qwen answer grounded in page_text. None if unavailable."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from jarvis.llm import ModelManager  # parent repo MLX stack
    except Exception as e:  # MLX not installed in this venv — fall back extractive
        log.info(f"MLX unavailable, using extractive QA: {e}")
        return None
    try:
        mm = ModelManager(load_now=True)
        prompt = f"Datasheet excerpt:\n{page_text[:2000]}\n\nQuestion: {question}"
        chunks = mm.chat_stream(
            [{"role": "system", "content": _GROUNDED_SYSTEM},
             {"role": "user", "content": prompt}])
        text = "".join(chunks) if hasattr(chunks, "__iter__") else str(chunks)
        text = text.strip()
        if not text or text.upper().startswith("UNAVAILABLE"):
            return None
        return text
    except Exception as e:  # pragma: no cover
        log.warning(f"Qwen QA failed, extractive fallback: {e}")
        return None


def answer(part_number: str, question: str) -> dict[str, Any]:
    """Return a grounded, cited answer or an explicit unavailable result."""
    idx, hits = _search(part_number, question)

    # Pinout questions -> deterministic pin-table lookup (e.g. "which pin is ground").
    if idx is not None and _PIN_Q_RE.search(question):
        pin = _pin_answer(idx, part_number, question)
        if pin:
            return pin

    if not hits:
        return {"available": False, "part_number": part_number,
                "reason": "no matching datasheet page"}

    top = hits[0]
    page_no, snippet, section = top["page"], top["text"], top.get("section")
    citation = {"part_number": part_number, "page": page_no, "section": section}

    if config.DATASHEET_QA_ENABLED:
        # Ground Qwen on the matched section (if any) else the page, for cleaner phrasing.
        if section:
            secs = list(idx.absolute_maximum_ratings) + list(idx.recommended_operating_conditions)
            page_text = next((s.text for s in secs if s.page == page_no and s.name == section), snippet)
        else:
            page_text = next((p.text for p in idx.pages if p.number == page_no), snippet)
        llm = _qwen_answer(question, page_text)
        if llm is not None:
            return {"available": True, "answer": llm, "citation": citation,
                    "source": "qwen", "snippet": snippet}

    # extractive default
    return {"available": True, "answer": snippet, "citation": citation,
            "source": "extractive", "snippet": snippet}
