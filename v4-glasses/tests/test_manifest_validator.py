"""Catalog + manifest validator tests (D1, D2)."""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest
import yaml

from catalog import validate


def _write(p: Path, obj):
    p.write_text(yaml.safe_dump(obj, sort_keys=False))


def _parts(extra=None):
    rows = [
        {"part_number": "NE555P", "manufacturer": "TI", "package": "PDIP-8",
         "top_markings": ["NE555P", "NE555"]},
    ]
    if extra:
        rows.extend(extra)
    return {"parts": rows}


def _entry(num, cache_path, sha, reviewed=True):
    return {"orderable_number": num, "manufacturer": "TI", "package": "PDIP-8",
            "url": "https://example.com/x.pdf", "retrieval_date": "2026-01-01",
            "sha256": sha, "cache_path": str(cache_path), "reviewed": reviewed,
            "cite_page": 1}


def test_example_catalog_passes_default():
    assert validate.validate() == []   # the shipped example is structurally valid


def test_example_catalog_strict_flags_unreviewed():
    errs = validate.validate(strict=True)
    assert any("not reviewed" in e for e in errs)


def test_unmapped_part_fails(tmp_path):
    parts = tmp_path / "parts.yaml"
    man = tmp_path / "manifest.yaml"
    _write(parts, _parts([
        {"part_number": "LM358P", "manufacturer": "TI", "package": "PDIP-8",
         "top_markings": ["LM358P"]}]))
    # manifest maps NE555P only -> LM358P unmapped
    _write(man, {"entries": [_entry("NE555P", tmp_path / "ne.pdf", "0" * 64, reviewed=False)]})
    errs = validate.validate(parts, man)
    assert any("LM358P" in e and "unmapped" in e for e in errs)


def test_hash_mismatch_fails(tmp_path):
    pdf = tmp_path / "ne.pdf"
    pdf.write_bytes(b"real pdf bytes")
    wrong = "a" * 64
    parts = tmp_path / "parts.yaml"
    man = tmp_path / "manifest.yaml"
    _write(parts, _parts())
    _write(man, {"entries": [_entry("NE555P", pdf, wrong)]})
    errs = validate.validate(parts, man)
    assert any("sha256 mismatch" in e for e in errs)


def test_correct_hash_passes_strict(tmp_path):
    pdf = tmp_path / "ne.pdf"
    data = b"the real datasheet pdf"
    pdf.write_bytes(data)
    good = hashlib.sha256(data).hexdigest()
    parts = tmp_path / "parts.yaml"
    man = tmp_path / "manifest.yaml"
    _write(parts, _parts())
    _write(man, {"entries": [_entry("NE555P", pdf, good, reviewed=True)]})
    assert validate.validate(parts, man, strict=True) == []


def test_bad_sha_pattern_is_schema_error(tmp_path):
    parts = tmp_path / "parts.yaml"
    man = tmp_path / "manifest.yaml"
    _write(parts, _parts())
    _write(man, {"entries": [_entry("NE555P", tmp_path / "x.pdf", "NOTAHASH")]})
    errs = validate.validate(parts, man)
    assert any("schema" in e for e in errs)
