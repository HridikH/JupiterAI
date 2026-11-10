"""Pin-map extraction for the live pinout overlay + pin Q&A.

Sources, in priority order (first that yields a usable map wins):

1. ``catalog``  — a hand-reviewed ``pins:`` list in catalog/parts.yaml. Used for
   datasheets with no machine-readable pin table (e.g. scanned Harris CD4000
   sheets). Reviewed data always beats parsing.
2. ``table``    — the datasheet's "Pin Functions" / "Terminal Functions" table,
   read with PyMuPDF's table finder (keeps cells intact: wrapped names such as
   "INVERTING\\nINPUT", subscripts such as "V\\nCC", multi-pin cells "1, 5").
3. ``diagram``  — the text layer of a "(TOP VIEW)" pin diagram, where DIP pins
   come out as ``LEFT RIGHT n N+1-n`` quadruples (older TI logic sheets).
4. ``text``     — legacy line-based parser over the captured section text.

Everything is deterministic and offline. A pin number is only *inferred* in one
case — the pigeonhole fill (exactly one package pin unassigned and exactly one
named signal without a number) — and such pins carry ``inferred: True``.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent

_DASHES = "‒–—―−"
_PIN_HDR_RE = re.compile(r"\b(pin|terminal)\s+(functions|assignments?|description)\b", re.I)
_TOPVIEW_RE = re.compile(r"\(?\s*TOP\s+VIEW\s*\)?", re.I)


# ---- package helpers -----------------------------------------------------------
def pkg_pin_count(package: str) -> Optional[int]:
    p = (package or "").upper().replace(" ", "")
    if any(k in p for k in ("TO-220", "TO220", "TO-92", "TO92", "TO-263", "SOT-223")):
        return 3
    m = re.findall(r"(\d+)", p)                 # PDIP-8 / SOIC-14 / SOT23-5
    return int(m[-1]) if m else None


def pkg_layout(package: str) -> str:
    """'dip' (two rows, pin 1 bottom-left, counter-clockwise) or 'inline' (one row)."""
    p = (package or "").upper()
    if any(k in p for k in ("TO-220", "TO220", "TO-92", "TO92", "SIP")):
        return "inline"
    return "dip"


def _pkg_tokens(package: str) -> list[str]:
    """Header words that identify the package's column, e.g. PDIP-8 -> PDIP, DIP, P."""
    p = (package or "").upper()
    fam = re.sub(r"[-\s]?\d+$", "", p)          # PDIP-8 -> PDIP
    toks = [fam] if fam else []
    if "DIP" in fam:
        toks += ["PDIP", "DIP", "N", "P", "E"]  # TI/Harris suffix letters for DIP
    if "TO-220" in p or "TO220" in p:
        toks += ["TO-220", "KCS", "KCT"]
    return toks


# ---- name cleanup -------------------------------------------------------------
def _clean_name(cell: str) -> str:
    """Join wrapped/subscripted table names. 'V\\nCC' -> 'VCC', 'Q\\nH'' -> "QH'",
    'INVERTING\\nINPUT' -> 'INVERTING INPUT', 'V–' -> 'V-'."""
    parts = [p.strip() for p in (cell or "").split("\n") if p.strip()]
    if not parts:
        return ""
    out = parts[0]
    for p in parts[1:]:
        # a 1-char base followed by a short subscript joins without a space
        if len(out) <= 2 and len(p) <= 4:
            out += p
        else:
            out += " " + p
    out = re.sub(f"[{_DASHES}]", "-", out)
    return re.sub(r"\(\d\)$", "", out).strip().upper()


def _cell_numbers(cell: Any) -> Optional[list[int]]:
    """'8' -> [8]; '1, 5' -> [1, 5]; '2, TAB' -> [2]; '—' / None -> None."""
    if cell is None:
        return None
    s = str(cell).replace("\n", " ").strip()
    if not s or s.strip(_DASHES + "- ") == "":
        return None
    nums = [int(n) for n in re.findall(r"\b(\d{1,3})\b", s)]
    # reject cells that are clearly prose (descriptions with numbers in them)
    letters = re.sub(r"(?i)\bTAB\b|[\d,\s]", "", s)
    if not nums or len(letters) > 2:
        return None
    return nums


def _clean_desc(cell: Any) -> str:
    s = " ".join(str(cell or "").split())
    return s[:90]


# ---- 1. catalog ---------------------------------------------------------------
def from_catalog(part) -> Optional[list[dict]]:
    pins = getattr(part, "pins", None) or []
    out = []
    for p in pins:
        no = p.get("pin", p.get("no"))           # `pin:` in YAML (`no:` parses as False)
        if no is not None and "name" in p:
            out.append({"name": str(p["name"]).upper(), "no": int(no),
                        "desc": str(p.get("desc", ""))})
    return out or None


# ---- 2. tables ----------------------------------------------------------------
def _choose_column(rows: list[tuple[str, list, str]], ncols: int, headers: list[str],
                   package: str, pkg_n: Optional[int]) -> Optional[int]:
    toks = _pkg_tokens(package)

    def vals(col):
        out = []
        for _, cells, _ in rows:
            if col < len(cells) and cells[col]:
                out.extend(cells[col])
        return out

    best, best_score = None, None
    for col in range(ncols):
        v = vals(col)
        if not v:
            continue
        s = set(v)
        hdr = headers[col] if col < len(headers) else ""
        hdr_hit = any(re.search(rf"(?<![A-Z]){re.escape(t)}(?![A-Z])", hdr) for t in toks if len(t) > 1)
        if pkg_n:
            in_range = len({x for x in s if 1 <= x <= pkg_n})
            over = sum(1 for x in s if x > pkg_n)
            score = (in_range - 3 * over + (2 if hdr_hit else 0), -col)
        else:
            run = 0
            while (run + 1) in s:
                run += 1
            score = (run + (2 if hdr_hit else 0), -col)
        if best_score is None or score > best_score:
            best, best_score = col, score
    return best


def parse_table_rows(table: list[list[Any]], package: str) -> Optional[list[dict]]:
    """Turn one extracted table (list of rows of cells) into a pin list, or None."""
    if not table:
        return None
    # locate the header row containing NAME
    hdr_i = None
    for i, r in enumerate(table[:4]):
        if any(c and re.fullmatch(r"\s*NAME\s*", str(c), re.I) for c in r):
            hdr_i = i
            break
    if hdr_i is None:
        return None
    hdr = [(" ".join(str(c or "").split())).upper() for c in table[hdr_i]]
    # merge a "PIN / NO." super-header above into the header (e.g. LM741: P|IN / NAME|NO.)
    if hdr_i > 0:
        above = [(" ".join(str(c or "").split())).upper() for c in table[hdr_i - 1]]
        hdr = [(a + " " + h).strip() for a, h in zip(above, hdr)]
    name_col = next(i for i, c in enumerate(hdr) if re.search(r"\bNAME\b", c))
    desc_col = next((i for i, c in enumerate(hdr) if "DESCRIPTION" in c or "FUNCTION" in c), None)
    type_cols = {i for i, c in enumerate(hdr) if re.fullmatch(r".*\bI/?O\b.*|.*\bTYPE\b.*", c)
                 and "NAME" not in c}
    num_cols = [i for i in range(len(hdr)) if i not in (name_col, desc_col) and i not in type_cols]
    if not num_cols:
        return None

    rows: list[tuple[str, list, str]] = []   # (name, [nums|None per num_col], desc)
    for r in table[hdr_i + 1:]:
        name = _clean_name(str(r[name_col] or "")) if name_col < len(r) else ""
        if not name or not re.match(r"^[A-Z0-9~/]", name):
            continue
        cells = [_cell_numbers(r[c]) if c < len(r) else None for c in num_cols]
        desc = _clean_desc(r[desc_col]) if desc_col is not None and desc_col < len(r) else ""
        rows.append((name, cells, desc))
    if not rows:
        return None

    pkg_n = pkg_pin_count(package)
    col = _choose_column(rows, len(num_cols), [hdr[c] for c in num_cols], package, pkg_n)
    if col is None:
        return None

    pins: list[dict] = []
    unnumbered: list[tuple[str, str]] = []
    seen: set[int] = set()
    for name, cells, desc in rows:
        nums = cells[col]
        if not nums:
            if name not in ("NC", "DNC"):
                unnumbered.append((name, desc))
            continue
        for n in nums:
            if pkg_n and not (1 <= n <= pkg_n):
                continue
            if n in seen:
                continue
            seen.add(n)
            pins.append({"name": name, "no": n, "desc": desc})

    # Pigeonhole: one package pin left and one named signal without a number in this
    # column (table extraction sometimes drops a single cell, e.g. 74HC595 VCC=16).
    if pkg_n:
        missing = sorted(set(range(1, pkg_n + 1)) - seen)
        real_unnumbered = [(n, d) for n, d in unnumbered if n not in {p["name"] for p in pins}]
        if len(missing) == 1 and len(real_unnumbered) == 1:
            n, d = real_unnumbered[0]
            pins.append({"name": n, "no": missing[0], "desc": d, "inferred": True})
    return pins or None


def _pin_pages(doc) -> list[int]:
    pages = []
    for i, page in enumerate(doc):
        t = page.get_text("text")
        # skip TOC / revision-history mentions: need a header AND a NAME column
        if _PIN_HDR_RE.search(t) and re.search(r"^\s*NAME\s*$", t, re.M | re.I):
            pages.append(i)
    return pages


_CAPTION_DEV_RE = re.compile(r"Pin\s+Functions\s*:\s*([A-Z0-9][A-Z0-9x/ ,]*)", re.I)


def _caption_devices(page, bbox) -> list[str]:
    """Device names from a 'Table 4-3. Pin Functions: TL072x' caption just above a table."""
    import fitz
    x0, y0, x1, _ = bbox
    cap = page.get_text("text", clip=fitz.Rect(0, max(0, y0 - 70), page.rect.width, y0))
    m = None
    for m in _CAPTION_DEV_RE.finditer(cap):
        pass                                     # the LAST caption above is this table's
    if not m:
        return []
    devs = [d.strip() for d in re.split(r"[,/]|\band\b", m.group(1)) if d.strip()]
    return [d for d in devs if re.search(r"\d", d)]      # device names, not "Metal Can Packages"


def _caption_matches(devs: list[str], part_number: str) -> Optional[bool]:
    """True/False if the caption names devices, None if it names none."""
    if not devs:
        return None
    pn = part_number.upper()
    for d in devs:
        stem = re.sub(r"[xX]+$", "", d.split()[0]).upper()   # TL072x -> TL072 (x = wildcard)
        if stem and pn.startswith(stem):
            return True
    return False


def from_tables(pdf: Path, package: str, part_number: str = "") -> Optional[tuple[list[dict], int]]:
    import fitz
    best, best_key = None, None
    with fitz.open(pdf) as doc:
        for i in _pin_pages(doc):
            try:
                tables = doc[i].find_tables().tables
            except Exception:
                continue
            for t in tables:
                match = _caption_matches(_caption_devices(doc[i], t.bbox), part_number) \
                    if part_number else None
                if match is False:
                    continue                     # a sibling device's table (e.g. TL071 in a TL07x sheet)
                pins = parse_table_rows(t.extract(), package)
                if not pins:
                    continue
                key = (match is True, _fit(pins, package), -i)
                if best_key is None or key > best_key:
                    best, best_key = (pins, i + 1), key
    return best


# ---- 3. TOP VIEW diagram text -------------------------------------------------
_DIAG_NAME_RE = re.compile(r"^[A-Z0-9][A-Z0-9+\-/'~]{0,6}$")


def from_diagram(pdf: Path, package: str) -> Optional[tuple[list[dict], int]]:
    """Parse DIP 'LEFT RIGHT n m' quadruples (with n + m == N + 1) under a TOP VIEW."""
    import fitz
    pkg_n = pkg_pin_count(package)
    best = None
    with fitz.open(pdf) as doc:
        for pi, page in enumerate(doc):
            text = page.get_text("text")
            m = _TOPVIEW_RE.search(text)
            if not m:
                continue
            lines = [l.strip() for l in text[m.end():].splitlines() if l.strip()][:200]
            quads = []
            for k in range(len(lines) - 3):
                a, b, c, d = lines[k:k + 4]
                if (_DIAG_NAME_RE.match(_clean_name(a)) and _DIAG_NAME_RE.match(_clean_name(b))
                        and c.isdigit() and d.isdigit()):
                    quads.append((_clean_name(a), _clean_name(b), int(c), int(d)))
            if not quads:
                continue
            # the quads of ONE package share n + m == N + 1; take the first coherent run
            total = quads[0][2] + quads[0][3]
            n = pkg_n or (total - 1)
            if total != n + 1:
                continue
            pins, seen = [], set()
            for a, b, c, d in quads:
                if c + d != total:
                    break
                for name, no in ((a, c), (b, d)):
                    if no not in seen and name not in ("NC", "DNC"):
                        seen.add(no)
                        pins.append({"name": name, "no": no, "desc": ""})
            if len(seen) >= max(4, int(0.75 * n)) and (best is None or len(pins) > len(best[0])):
                best = (pins, pi + 1)
    return best


def _fit(pins: list[dict], package: str) -> int:
    n = pkg_pin_count(package)
    nums = {p["no"] for p in pins}
    return len({x for x in nums if not n or 1 <= x <= n})


# ---- short overlay labels ----------------------------------------------------------
_SHORT = {
    "INVERTING INPUT": "IN-", "NONINVERTING INPUT": "IN+", "NON-INVERTING INPUT": "IN+",
    "OFFSET NULL": "NULL", "OUTPUT": "OUT", "INPUT": "IN", "ADJUST": "ADJ",
    "GROUND": "GND", "THRESHOLD": "THRES", "TRIGGER": "TRIG", "DISCHARGE": "DISCH",
    "CONTROL VOLTAGE": "CONT",
}


def short_label(name: str) -> str:
    n = name.upper().strip()
    if n in _SHORT:
        return _SHORT[n]
    return n if len(n) <= 7 else n.split()[0][:7]


def _with_labels(info: dict) -> dict:
    for p in info.get("pins", []):
        p.setdefault("label", short_label(p["name"]))
    return info


# ---- public -------------------------------------------------------------------
@functools.lru_cache(maxsize=64)
def _cached(part_number: str, pdf: str, package: str, mtime: float) -> dict:
    pdfp = Path(pdf)
    for fn, src in ((lambda p, k: from_tables(p, k, part_number), "table"),
                    (from_diagram, "diagram")):
        try:
            got = fn(pdfp, package)
        except Exception:
            got = None
        if got and _fit(got[0], package) >= 2:
            pins, page = got
            return {"pins": sorted(pins, key=lambda p: p["no"]), "page": page, "source": src}
    return {}


def get_pins(part_number: str, legacy=None) -> dict:
    """{part, package, layout, page, source, pins:[{name,label,no,desc[,inferred]}]}."""
    import copy
    return _with_labels(copy.deepcopy(_get_pins(part_number, legacy)))


def _get_pins(part_number: str, legacy=None) -> dict:
    from server.catalog_api import load
    part = load().by_number(part_number)
    package = part.package if part else ""
    base = {"part": part_number, "package": package, "layout": pkg_layout(package),
            "pin_count": pkg_pin_count(package), "page": None, "source": None, "pins": []}
    if part is None:
        return base

    cat = from_catalog(part)
    if cat:
        return {**base, "pins": sorted(cat, key=lambda p: p["no"]),
                "page": (part.datasheet or {}).get("pin_page") or (part.datasheet or {}).get("cite_page"),
                "source": "catalog"}

    rel = (part.datasheet or {}).get("path")
    pdf = ROOT / rel if rel else None
    if pdf and pdf.exists():
        got = _cached(part_number, str(pdf), package, pdf.stat().st_mtime)
        if got:
            return {**base, **got}
    if legacy is not None:                       # line-based fallback
        got = legacy(part_number)
        if got and got.get("pins"):
            return {**base, "pins": got["pins"], "page": got.get("page"), "source": "text"}
    return base
