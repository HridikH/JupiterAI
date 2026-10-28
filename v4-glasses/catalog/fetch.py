"""One-time, ONLINE datasheet fetch — the ONLY component allowed to touch the network.

    python -m catalog.fetch                 # fetch any manifest entry whose cache is missing
    python -m catalog.fetch --force         # re-download everything
    python -m catalog.fetch --only NE555P   # one entry

For each manifest entry it downloads the PDF to cache_path, computes the sha256,
and writes it back into manifest.yaml. It does NOT set reviewed:true — a human
must open each PDF, confirm it's the correct part/revision, and flip the flag.
Runtime (server/) never imports this module.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "catalog" / "manifest.yaml"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(force: bool = False, only: str | None = None) -> int:
    import requests  # imported here so runtime never pulls a network client by accident

    doc = yaml.safe_load(MANIFEST.read_text()) or {}
    entries = doc["entries"] if isinstance(doc, dict) else doc
    changed = 0
    for entry in entries:
        num = entry["orderable_number"]
        if only and num != only:
            continue
        dest = ROOT / entry["cache_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and not force:
            print(f"[skip] {num}: already cached ({dest.name})")
            # still refresh hash if it's the placeholder
            if set(entry.get("sha256", "")) == {"0"}:
                entry["sha256"] = _sha256(dest.read_bytes())
                changed += 1
            continue
        print(f"[get ] {num}: {entry['url']}")
        try:
            r = requests.get(entry["url"], timeout=30,
                             headers={"User-Agent": "glasses-catalog-fetch/1"})
            r.raise_for_status()
        except Exception as e:
            print(f"   FAILED: {e}")
            continue
        dest.write_bytes(r.content)
        entry["sha256"] = _sha256(r.content)
        entry["reviewed"] = False  # force re-review on any (re)download
        changed += 1
        print(f"   saved {len(r.content)} bytes, sha256={entry['sha256'][:12]}… "
              f"(reviewed set to false — verify + flip manually)")

    if changed:
        MANIFEST.write_text(yaml.safe_dump(doc, sort_keys=False))
        print(f"\nupdated {changed} manifest entr(y/ies). "
              "Now open each PDF, verify the revision, and set reviewed:true.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch datasheet PDFs (online, one-time)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()
    return fetch(force=args.force, only=args.only)


if __name__ == "__main__":
    raise SystemExit(main())
