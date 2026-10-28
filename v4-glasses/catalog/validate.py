"""Catalog validator — fails if any acceptable part is unmapped or a hash mismatches.

    python -m catalog.validate            # schema + mapping + hash-if-cached
    python -m catalog.validate --strict   # also require reviewed:true + cached PDF present

Checks:
  1. every parts.yaml entry validates against parts.schema.yaml
  2. every manifest.yaml entry validates against manifest.schema.yaml
  3. every orderable part number is unique (parts) and mapped (manifest)      [D1]
  4. for each cached PDF present, its sha256 matches the manifest              [D1]
  5. (strict) every entry reviewed:true and its cache file exists             [D2]

Exit code 0 = pass, 1 = fail.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"


def _load_yaml(p: Path):
    return yaml.safe_load(p.read_text()) or {}


def _rows(doc, *keys):
    if isinstance(doc, list):
        return doc
    for k in keys:
        if k in doc:
            return doc[k]
    return []


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate(parts_path: Path | None = None, manifest_path: Path | None = None,
             strict: bool = False) -> list[str]:
    parts_path = parts_path or (CATALOG / "parts.yaml")
    manifest_path = manifest_path or (CATALOG / "manifest.yaml")
    parts_schema = _load_yaml(CATALOG / "schema" / "parts.schema.yaml")
    man_schema = _load_yaml(CATALOG / "schema" / "manifest.schema.yaml")
    pv = Draft202012Validator(parts_schema)
    mv = Draft202012Validator(man_schema)

    errors: list[str] = []

    parts = _rows(_load_yaml(parts_path), "parts")
    manifest = _rows(_load_yaml(manifest_path), "entries", "datasheets")

    # 1. parts schema + uniqueness
    seen = set()
    for i, row in enumerate(parts):
        for e in pv.iter_errors(row):
            errors.append(f"parts[{i}] schema: {e.message}")
        pn = row.get("part_number")
        if pn in seen:
            errors.append(f"parts[{i}] duplicate part_number: {pn}")
        seen.add(pn)

    # 2. manifest schema
    by_num: dict[str, dict] = {}
    for i, row in enumerate(manifest):
        for e in mv.iter_errors(row):
            errors.append(f"manifest[{i}] schema: {e.message}")
        by_num[row.get("orderable_number")] = row

    # 3. mapping completeness (every acceptable part must be mapped) (D1)
    for pn in seen:
        if pn not in by_num:
            errors.append(f"part {pn!r} has NO manifest entry (unmapped)")

    # 4 + 5. cache file + hash + reviewed
    for num, row in by_num.items():
        cache_path = ROOT / row.get("cache_path", "")
        if cache_path.exists():
            actual = _sha256_file(cache_path)
            if actual != row.get("sha256"):
                errors.append(f"{num}: sha256 mismatch for {cache_path.name} "
                              f"(file={actual[:12]}… manifest={str(row.get('sha256'))[:12]}…)")
        elif strict:
            errors.append(f"{num}: cached PDF missing at {cache_path}")
        if strict and not row.get("reviewed"):
            errors.append(f"{num}: not reviewed (set reviewed:true after human check)")

    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate catalog + datasheet manifest")
    ap.add_argument("--strict", action="store_true",
                    help="require reviewed:true + cached PDFs present (runtime readiness)")
    args = ap.parse_args()
    errors = validate(strict=args.strict)
    if errors:
        print(f"CATALOG INVALID ({len(errors)} problem(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("catalog OK" + (" (strict)" if args.strict else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
