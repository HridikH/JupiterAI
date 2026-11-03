"""Auto-cache a datasheet the first time a part is seen via web lookup.

When the OCR reads a part that is NOT in the local catalog and a web search finds
its EXACT datasheet PDF, this downloads + hashes the PDF, writes it into the
offline cache, and appends entries to parts.yaml + manifest.yaml. The entry is
marked `auto: true, reviewed: false` so:
  * the next sighting of that part matches OFFLINE (no web needed), AND
  * the resolver OFFERS it as a candidate but NEVER auto-accepts it until a human
    reviews it (see server/resolver.py). A human flips `reviewed: true` after
    confirming the manufacturer/revision.

Entries are APPENDED as raw YAML so the hand-curated files (and their comments)
are preserved. Safe to call repeatedly — a part already present is a no-op.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"
PARTS = CATALOG / "parts.yaml"
MANIFEST = CATALOG / "manifest.yaml"
CACHE = CATALOG / "cache"
MAX_PDF_BYTES = 25 * 1024 * 1024
HEADERS = {"User-Agent": "Mozilla/5.0 (glasses-autocache)"}


def _norm(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (s or "").upper())


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "part"


def existing_part_numbers() -> set[str]:
    import yaml
    if not PARTS.exists():
        return set()
    rows = yaml.safe_load(PARTS.read_text()) or {}
    rows = rows.get("parts", rows) if isinstance(rows, dict) else rows
    return {_norm(r.get("part_number", "")) for r in (rows or [])}


def is_cached(part: str) -> bool:
    return _norm(part) in existing_part_numbers()


def cache_part(part: str, url: str, *, manufacturer: str = "Unknown",
               package: str = "unknown", markings: Optional[list[str]] = None,
               mark_reviewed: bool = False) -> dict:
    """Download `url` and register `part` in the catalog. Returns a status dict.

    No-ops (cached=False, reason='exists') if the part is already present."""
    part = (part or "").strip()
    if len(_norm(part)) < 3:
        return {"cached": False, "reason": "part string too short"}
    if is_cached(part):
        return {"cached": False, "reason": "exists", "part": part}
    if not url.lower().split("?")[0].endswith(".pdf"):
        return {"cached": False, "reason": "not a pdf url", "part": part}

    try:
        r = requests.get(url, timeout=20, headers=HEADERS, stream=True)
        r.raise_for_status()
        data = r.content
    except Exception as e:
        return {"cached": False, "reason": f"download failed: {e}", "part": part}
    if not data[:5] == b"%PDF-":
        return {"cached": False, "reason": "not a valid PDF", "part": part}
    if len(data) > MAX_PDF_BYTES:
        return {"cached": False, "reason": "pdf too large", "part": part}

    CACHE.mkdir(parents=True, exist_ok=True)
    slug = _slug(part)
    dest = CACHE / f"{slug}.pdf"
    dest.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    cache_path = f"catalog/cache/{slug}.pdf"
    marks = markings or [part]
    marks_yaml = ", ".join(f'"{m}"' for m in marks)

    # append to parts.yaml (2-space list style, matches the file)
    with PARTS.open("a") as f:
        f.write(
            f"\n  - part_number: {part}\n"
            f"    manufacturer: {manufacturer}\n"
            f"    package: {package}\n"
            f"    top_markings: [{marks_yaml}]\n"
            f"    auto: true\n"
        )
    # append to manifest.yaml (0-indent list style, matches the file)
    with MANIFEST.open("a") as f:
        f.write(
            f"- orderable_number: {part}\n"
            f"  manufacturer: {manufacturer}\n"
            f"  package: {package}\n"
            f"  url: {url}\n"
            f"  retrieval_date: '2026-01-01'\n"
            f"  sha256: {sha}\n"
            f"  cache_path: {cache_path}\n"
            f"  reviewed: {str(bool(mark_reviewed)).lower()}\n"
            f"  cite_page: 1\n"
            f"  auto: true\n"
        )
    return {"cached": True, "part": part, "cache_path": cache_path,
            "sha256": sha, "bytes": len(data), "reviewed": bool(mark_reviewed)}
