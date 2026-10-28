"""Calibration on a held-out calibration image set, then freeze.

Runs the identification pipeline over a labeled CALIBRATION set (disjoint from the
trial set) and chooses accept_score / accept_margin that admit as many correct
top-1 matches as possible while admitting ZERO incorrect ones on this set. Writes
the chosen values into the (still unfrozen) thresholds file; run `eval.freeze` after
reviewing.

    python -m eval.calibrate --dataset eval/datasets/calibration.yaml
    python -m eval.freeze

Dataset YAML is the same shape as the trial set: {trials:[{image,truth}]}.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.pipeline import Pipeline  # noqa: E402
from server.state import StateStore  # noqa: E402
from server.ws import WSHub  # noqa: E402
from server import config, thresholds as th_mod  # noqa: E402


def _top_scores(dataset: list[dict]):
    """Return [(truth, top_part, top_score, margin)] over the calibration set."""
    pipe = Pipeline(StateStore(), WSHub())   # uses current (unfrozen) thresholds for ranking only
    rows = []
    for t in dataset:
        img = ROOT / "eval" / t["image"]
        if not img.exists():
            img = ROOT / t["image"]
        d = pipe.identify(img.read_bytes())
        if d.ranked:
            top = d.ranked[0]
            rows.append((t["truth"], top.part.part_number, d.top_score, d.margin))
        else:
            rows.append((t["truth"], None, 0.0, 0.0))
    return rows


def choose_thresholds(rows) -> tuple[float, float]:
    """Pick the LOWEST (score, margin) that still admits zero incorrect accepts.

    We scan candidate score/margin cutoffs taken from the correct-match
    distribution and keep the pair maximizing correct accepts with 0 wrong accepts.
    """
    correct = [(s, m) for (truth, part, s, m) in rows if part == truth]
    wrong = [(s, m) for (truth, part, s, m) in rows if part is not None and part != truth]
    if not correct:
        return (th_mod.DEFAULTS["accept_score"], th_mod.DEFAULTS["accept_margin"])

    score_cuts = sorted({round(s, 3) for s, _ in correct})
    margin_cuts = sorted({round(m, 3) for _, m in correct})
    best = None  # (n_correct, -score_cut, -margin_cut, score_cut, margin_cut)
    for sc in score_cuts:
        for mc in margin_cuts:
            n_wrong = sum(1 for s, m in wrong if s >= sc and m >= mc)
            if n_wrong > 0:
                continue
            n_correct = sum(1 for s, m in correct if s >= sc and m >= mc)
            cand = (n_correct, -sc, -mc, sc, mc)
            if best is None or cand > best:
                best = cand
    if best is None:
        return (th_mod.DEFAULTS["accept_score"], th_mod.DEFAULTS["accept_margin"])
    return (best[3], best[4])


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibrate thresholds on a held-out set")
    ap.add_argument("--dataset", default="eval/datasets/calibration.yaml")
    ap.add_argument("--path", default=str(config.THRESHOLDS_FILE))
    args = ap.parse_args()

    ds_path = ROOT / args.dataset if not Path(args.dataset).is_absolute() else Path(args.dataset)
    doc = yaml.safe_load(ds_path.read_text()) or {}
    dataset = doc["trials"] if isinstance(doc, dict) else doc

    rows = _top_scores(dataset)
    acc_score, acc_margin = choose_thresholds(rows)
    print(f"[calibrate] over {len(rows)} images -> accept_score={acc_score} accept_margin={acc_margin}")

    path = Path(args.path)
    raw = json.loads(path.read_text()) if path.exists() else dict(th_mod.DEFAULTS)
    raw["accept_score"] = acc_score
    raw["accept_margin"] = acc_margin
    raw["frozen"] = False            # calibration leaves it UNFROZEN; review then freeze
    raw.pop("hash", None)
    raw["version"] = f"thresholds/calibrated-{len(rows)}img"
    path.write_text(json.dumps(raw, indent=2))
    print(f"[calibrate] wrote {path} (unfrozen). Review, then run: python -m eval.freeze")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
