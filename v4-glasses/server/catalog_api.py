"""Catalog data model + loader (consumed by the resolver).

Stage 2 defines the in-memory model (Part / Catalog) the resolver scores against.
Stage 3 fills in YAML loading from catalog/parts.yaml + manifest.yaml and PDF
access. Keeping the model here lets the resolver and its tests be written and
verified before the real catalog data exists.

No part number is ever invented here. Every Part is an exact orderable number
with explicitly curated marking strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config


@dataclass
class Part:
    part_number: str                       # exact orderable number
    manufacturer: str
    package: str
    top_markings: list[str] = field(default_factory=list)   # strings seen on the part
    aliases: list[str] = field(default_factory=list)
    min_char_height_mm: Optional[float] = None
    # Filled from manifest.yaml at load (stage 3): {"path": ..., "cite_page": ...}
    datasheet: dict[str, Any] = field(default_factory=dict)
    # Provenance: auto=True means this entry was auto-cached from a web datasheet
    # lookup of an OCR read (not hand-curated). reviewed mirrors the manifest flag.
    # The resolver NEVER auto-accepts an auto&&unreviewed part — it is candidate-only
    # until a human verifies it (preserves "never present uncertain as definite").
    auto: bool = False
    reviewed: bool = True
    # Hand-reviewed pin map [{no, name, desc}] for datasheets whose pin table isn't
    # machine-readable (scanned sheets). Takes priority over datasheet parsing.
    pins: list[dict[str, Any]] = field(default_factory=list)

    def candidate_strings(self) -> list[str]:
        """All strings an OCR read could legitimately match for this part.

        Top markings first (they are what's physically printed and differ from
        the orderable number on small packages), then aliases, then the
        orderable number itself as a fallback.
        """
        seen: list[str] = []
        for s in [*self.top_markings, *self.aliases, self.part_number]:
            if s and s not in seen:
                seen.append(s)
        return seen


@dataclass
class Catalog:
    parts: list[Part] = field(default_factory=list)
    source: Optional[Path] = None

    @classmethod
    def from_dicts(cls, rows: list[dict[str, Any]]) -> "Catalog":
        parts = [
            Part(
                part_number=r["part_number"],
                manufacturer=r.get("manufacturer", ""),
                package=r.get("package", ""),
                top_markings=list(r.get("top_markings", [])),
                aliases=list(r.get("aliases", [])),
                min_char_height_mm=r.get("min_char_height_mm"),
                datasheet=dict(r.get("datasheet", {})),
                auto=bool(r.get("auto", False)),
                pins=list(r.get("pins", []) or []),
            )
            for r in rows
        ]
        return cls(parts=parts)

    def __len__(self) -> int:
        return len(self.parts)

    def by_number(self, part_number: str) -> Optional[Part]:
        for p in self.parts:
            if p.part_number == part_number:
                return p
        return None


# ---- loading ----------------------------------------------------------------
# Stage 3 extends this to merge manifest.yaml (datasheet path + cite page) and to
# validate. For now it reads parts.yaml if present so the pipeline is runnable.
def load(parts_path: Optional[Path] = None,
         manifest_path: Optional[Path] = None) -> Catalog:
    import yaml

    parts_path = parts_path or (config.CATALOG_DIR / "parts.yaml")
    manifest_path = manifest_path or (config.CATALOG_DIR / "manifest.yaml")

    if not parts_path.exists():
        return Catalog(parts=[], source=parts_path)

    rows = yaml.safe_load(parts_path.read_text()) or []
    if isinstance(rows, dict):          # allow {parts: [...]} or a bare list
        rows = rows.get("parts", [])
    cat = Catalog.from_dicts(rows)
    cat.source = parts_path

    # merge datasheet pointers from manifest.yaml (keyed by orderable number)
    if manifest_path.exists():
        man = yaml.safe_load(manifest_path.read_text()) or []
        if isinstance(man, dict):
            man = man.get("entries", man.get("datasheets", []))
        by_num = {m.get("orderable_number", m.get("part_number")): m for m in man}
        for p in cat.parts:
            m = by_num.get(p.part_number)
            if m:
                p.datasheet = {"path": m.get("cache_path"),
                               "cite_page": m.get("cite_page", 1),
                               "url": m.get("url")}
                p.reviewed = bool(m.get("reviewed", True))
    return cat


def load_default_catalog() -> Catalog:
    return load()
