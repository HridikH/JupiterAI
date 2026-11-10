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
    ("GND", ["ground", "gnd", "vss", "0v", "negative supply", "v-"]),
    ("VCC", ["vcc", "vdd", "supply", "power", "v+", "positive"]),
    ("TRIG", ["trigger", "trig"]),
    ("RESET", ["reset"]),
    ("THRES", ["threshold", "thres"]),
    ("DISCH", ["discharge", "disch"]),
    ("CONT", ["control voltage", "control", "cont", "cv"]),
    ("OUT", ["output", "out"]),
]
# datasheet pin names that carry each signal (first present wins)
_SIGNAL_NAMES = {
    "GND": ["GND", "VSS", "V-", "VCC-", "VEE"],
    "VCC": ["VCC", "VDD", "V+", "VCC+", "INPUT"],
    "OUT": ["OUT", "OUTPUT", "1OUT", "OUT1"],
}


# Pin-name shapes across op-amps, logic, timers, comparators, regulators.
_PIN_NAME_RE = re.compile(
    r"^(?:"
    r"GND|NC|DNC|"
    r"V(?:CC|DD|SS|EE|REF|IN|OUT|BIAS)?[+\-]?|"          # V+, V-, VCC+, VDD, VIN...
    r"[1-4]?IN[+\-]?|IN[1-4]?[+\-]?|"                      # IN+, 1IN-, IN1+
    r"[1-4]?OUT|OUT[1-4]?|"                                # OUT, 1OUT, OUT2
    r"Q[A-H]'?|SER|R?CLK|RCLK|SRCLK|SRCLR|OE|"            # shift-register / logic
    r"RESET|TRIG|THRES|DISCH|CONT|CV|"                     # 555
    r"ADJ|EN|CS|A|B|C|D|Y|J|K"
    r")$", re.I)

_TYPE_WORDS = {"I", "O", "IO", "INPUT", "OUTPUT", "INPUTOUTPUT", "POWER", "GROUND",
               "PASSIVE", "SUPPLY", "ANALOG", "DIGITAL"}


def _pkg_pin_count(package: str) -> int | None:
    p = (package or "").upper().replace(" ", "")
    if "TO-220" in p or "TO220" in p or "TO-92" in p or "TO92" in p:
        return 3
    m = re.findall(r"(\d+)", p)                 # trailing number of PDIP-8 / SOIC-14 / SOT23-8
    return int(m[-1]) if m else None


def _norm_name(line: str) -> str:
    # unify dash variants (en/em/minus) to ASCII '-' so 'IN1–' == 'IN1-'
    return re.sub(r"[‒–—―−]", "-", re.sub(r"\s", "", line)).upper()


def _is_name(line: str) -> bool:
    norm = _norm_name(line)
    return bool(_PIN_NAME_RE.match(norm)) and len(norm) <= 7


def parse_pin_table(text: str, package: str = "") -> dict[str, dict]:
    """Parse a 'Pin Functions' section into {NAME: {no:int, desc:str}}.

    Robust to TI's varying layouts: a row is NAME then one pin-number CELL per
    package column (digits, a comma-list for NC, or an em-dash for N/A), then a
    TYPE word and DESCRIPTION. Because the package-column ORDER differs between
    datasheets, we pick the column whose pin numbers form {1..N} for the part's
    package (N from `package`), instead of blindly taking the first number."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    rows = []  # (name, [cell,...]) where cell is int | None(dash) | 'multi'
    i = 0
    while i < len(lines):
        if _is_name(lines[i]):
            name = _norm_name(lines[i])
            j = i + 1
            cells = []
            while j < len(lines):
                c = re.sub(r"\s", "", lines[j])
                if re.fullmatch(r"\d+", c):
                    cells.append(int(c))
                elif re.fullmatch(r"[\d]+(?:,[\d]+)+", c):
                    cells.append("multi")
                elif c in ("—", "-", "–", "−"):
                    cells.append(None)
                else:
                    break
                j += 1
            desc = []
            while j < len(lines) and not _is_name(lines[j]) \
                    and not re.fullmatch(r"[\d,—\-–−]+", re.sub(r"\s", "", lines[j])):
                desc.append(lines[j])
                j += 1
            d = " ".join(desc).strip(" —")
            d = re.split(r"\s+www\.|\s+SLFS|\s+Copyright|\s+Submit", d)[0]
            rows.append((name, cells, d[:90].strip()))
            i = j if j > i else i + 1
        else:
            i += 1

    # Rows usable for column detection: those with no comma-list cell. KEEP
    # single-valued NC rows (they carry real pin numbers that complete {1..N}).
    numeric = [(n, c, d) for (n, c, d) in rows if c and "multi" not in c]
    if not numeric:
        return {}
    from statistics import mode
    try:
        ncols = mode(len(c) for _, c, _ in numeric)
    except Exception:
        ncols = max(len(c) for _, c, _ in numeric)
    # trim trailing dash (a — TYPE cell) on over-length rows
    norm_rows = []
    for n, c, d in numeric:
        cc = list(c)
        while len(cc) > ncols and cc and cc[-1] is None:
            cc.pop()
        norm_rows.append((n, cc, d))

    pkg_n = _pkg_pin_count(package)

    def col_vals(col):
        return [c[col] for _, c, _ in norm_rows if col < len(c) and isinstance(c[col], int)]

    best_col, best_score = 0, (-1e9,)
    for col in range(ncols):
        s = set(col_vals(col))
        if pkg_n and s == set(range(1, pkg_n + 1)):
            best_col = col; break               # exact package-pin match — done
        if pkg_n:
            in_range = {v for v in s if 1 <= v <= pkg_n}
            over = sum(1 for v in s if v > pkg_n)
            # most in-range distinct pins, penalize out-of-range, prefer reaching pkg_n
            score = (len(in_range) - 3 * over, max(s) if s else 0)
        else:
            run = 0
            while (run + 1) in s:
                run += 1
            score = (run,)
        if score > best_score:
            best_score, best_col = score, col

    out: dict[str, dict] = {}
    for n, c, d in norm_rows:
        if n in ("NC", "DNC"):
            continue                             # don't list no-connect pins in the overlay
        if best_col < len(c) and isinstance(c[best_col], int) and n not in out:
            out[n] = {"no": c[best_col], "desc": d}
    return out


def get_pins(part_number: str) -> dict:
    """Structured pin map for the live-feed overlay + pin Q&A.

    {part, package, layout, pin_count, page, source, pins:[{name,no,desc}]} — see
    server/pinout.py for the source order (catalog > table > diagram > text)."""
    from server import pinout
    return pinout.get_pins(part_number, legacy=_legacy_get_pins)


def _legacy_get_pins(part_number: str) -> dict:
    """Line-based fallback over the captured 'Pin Functions' section text."""
    idx = _index(part_number)
    if idx is None:
        return {"part": part_number, "page": None, "pins": []}
    package = ""
    try:
        from server.catalog_api import load
        p = load().by_number(part_number)
        package = p.package if p else ""
    except Exception:
        pass
    pkg_n = _pkg_pin_count(package)
    # A datasheet can contain several "Pin Functions" matches (real table + stray
    # references). Pick the SINGLE best section instead of merging them: the one
    # whose pin numbers best fit the package (most in [1..N], fewest over), earliest
    # page breaking ties.
    best, best_score = None, (-1e9, 0)
    for s in list(getattr(idx, "pin_functions", [])):
        parsed = parse_pin_table(s.text, package)
        if not parsed:
            continue
        nums = [v["no"] for v in parsed.values()]
        if pkg_n:
            in_range = len({n for n in nums if 1 <= n <= pkg_n})
            over = sum(1 for n in nums if n > pkg_n)
            score = (in_range - 3 * over, -s.page)
        else:
            score = (len(nums), -s.page)
        if score > best_score:
            best_score, best = score, (parsed, s.page)
    if best is None:
        return {"part": part_number, "page": None, "pins": []}
    parsed, page = best
    # keep only pins within the package's range; dedupe by pin number (first wins)
    pins, seen = [], set()
    for n, v in parsed.items():
        no = v["no"]
        if pkg_n and not (1 <= no <= pkg_n):
            continue
        if no in seen:
            continue
        seen.add(no)
        pins.append({"name": n, "no": no, "desc": v["desc"]})
    pins.sort(key=lambda p: p["no"])
    return {"part": part_number, "page": page, "pins": pins}


def _index(part_number: str):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from catalog.page_index import index_for_part
    return index_for_part(part_number)


def _pin_answer(idx, part_number: str, question: str) -> dict | None:
    data = get_pins(part_number)
    pins = data.get("pins", [])
    if not pins:
        return None
    page = data.get("page")
    by_name = {p["name"]: p for p in pins}
    ql = question.lower()
    for sig, keys in _SIGNAL_ALIASES:
        if not any(k in ql for k in keys):
            continue
        name = next((n for n in _SIGNAL_NAMES.get(sig, [sig]) if n in by_name), None)
        if name:
            p = by_name[name]
            ans = f"{name} ({p['desc']}) is pin {p['no']}." if p.get("desc") else f"{name} is pin {p['no']}."
            return {"available": True, "answer": ans, "source": "pin_table",
                    "citation": {"part_number": part_number, "page": page, "section": "pin_functions"}}
    listing = ", ".join(f"{p['name']} = pin {p['no']}" for p in pins)
    return {"available": True, "answer": f"Pin map: {listing}.", "source": "pin_table",
            "citation": {"part_number": part_number, "page": page, "section": "pin_functions"}}


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
