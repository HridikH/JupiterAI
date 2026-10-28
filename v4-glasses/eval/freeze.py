"""Freeze the thresholds config after calibration.

    python -m eval.freeze                 # freeze server/thresholds.json
    python -m eval.freeze --version v1

Sets frozen:true, stamps a content hash, and (optionally) a version label. After
this, evaluation mode (blinded_trial) will run; before it, it refuses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import config, thresholds as th_mod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Freeze thresholds")
    ap.add_argument("--version", help="optional version label to stamp before freezing")
    ap.add_argument("--path", default=str(config.THRESHOLDS_FILE))
    args = ap.parse_args()
    path = Path(args.path)

    if not path.exists():
        th_mod.write_example(path)
        print(f"wrote example thresholds to {path}")

    if args.version:
        raw = json.loads(path.read_text())
        raw["version"] = args.version
        path.write_text(json.dumps(raw, indent=2))

    frozen = th_mod.freeze(path)
    print(f"FROZEN: {frozen.version}")
    print(f"hash:   {frozen.config_hash}")
    print("evaluation mode is now permitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
