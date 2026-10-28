"""Blinded trial harness (V2).

Runs a randomized trial over a labeled image set. The operator, when picking from
a candidate list, is NEVER shown the ground truth — only the candidate part
numbers. Reports automatic-only / user-assisted / overall accuracy, incorrect
finals, recaptures, errors, and a confusion matrix, and the V2 verdict
(>=80 correct, <=2 incorrect on 100 trials).

Requires FROZEN thresholds — it refuses to run on an unfrozen config (so a trial
can never use hand-tweaked numbers).

    python -m eval.blinded_trial --dataset eval/datasets/trials.yaml --seed 7
    python -m eval.blinded_trial ... --simulate-pick top    # non-interactive (testing)

Dataset YAML:
    trials:
      - {image: datasets/trial/ne555_01.jpg, truth: NE555P}
      - ...
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.report import TrialOutcome, compute, format_report  # noqa: E402
from server.pipeline import Pipeline  # noqa: E402
from server.state import StateStore  # noqa: E402
from server.ws import WSHub  # noqa: E402
from server.thresholds import UnfrozenConfigError, load as load_th  # noqa: E402


def load_dataset(path: Path) -> list[dict]:
    doc = yaml.safe_load(path.read_text()) or {}
    trials = doc["trials"] if isinstance(doc, dict) else doc
    return trials


def _operator_pick(candidates: list[dict], simulate: str | None) -> int | None:
    """Return chosen index, or None if operator declines. Ground truth NOT shown."""
    if simulate == "top":
        return 0
    if simulate == "none":
        return None
    print("\n  Candidates (choose the correct part — ground truth is hidden):")
    for i, c in enumerate(candidates):
        print(f"    [{i}] {c['part_number']}  {c.get('manufacturer','')}  {c.get('package','')}")
    print("    [s] skip / none match")
    while True:
        ans = input("  pick> ").strip().lower()
        if ans == "s":
            return None
        if ans.isdigit() and 0 <= int(ans) < len(candidates):
            return int(ans)


def run(dataset_path: Path, seed: int = 0, simulate_pick: str | None = None) -> dict:
    # refuse unfrozen thresholds (evaluation mode)
    th = load_th(require_frozen=True)   # raises UnfrozenConfigError if not frozen
    print(f"[trial] thresholds {th.version} {th.config_hash}")

    trials = load_dataset(dataset_path)
    truths = Counter(t["truth"] for t in trials)
    if len(truths) < 20 or any(c < 5 for c in truths.values()):
        print(f"[trial] WARNING: V2 wants >=20 parts x >=5 trials (have "
              f"{len(truths)} parts; min per-part {min(truths.values())}).")

    order = list(range(len(trials)))
    random.Random(seed).shuffle(order)   # randomized presentation order

    pipe = Pipeline(StateStore(), WSHub(), require_frozen=True)
    outcomes: list[TrialOutcome] = []
    for n, idx in enumerate(order, 1):
        t = trials[idx]
        img = (ROOT / "eval" / t["image"]) if not Path(t["image"]).is_absolute() else Path(t["image"])
        if not img.exists():
            img = ROOT / t["image"]
        decision = pipe.identify(img.read_bytes())
        status = decision.status
        final_part, assisted = None, False
        if status == "accepted":
            final_part = decision.result["part_number"]
        elif status == "candidate_selection":
            pick = _operator_pick(decision.result["candidates"], simulate_pick)
            if pick is not None:
                final_part = decision.result["candidates"][pick]["part_number"]
                assisted = True
        outcomes.append(TrialOutcome(truth=t["truth"], status=status,
                                     final_part=final_part, assisted=assisted))
        print(f"[trial] {n}/{len(order)} truth={t['truth']} -> {status}"
              f"{' pick='+final_part if assisted else ''}")

    metrics = compute(outcomes)
    print("\n" + format_report(metrics))
    return metrics


def main() -> int:
    ap = argparse.ArgumentParser(description="Blinded trial harness (V2)")
    ap.add_argument("--dataset", default="eval/datasets/trials.yaml")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--simulate-pick", choices=["top", "none"], default=None,
                    help="non-interactive candidate pick (testing only)")
    ap.add_argument("--out", default="eval/trial_report.json")
    args = ap.parse_args()
    try:
        metrics = run(ROOT / args.dataset if not Path(args.dataset).is_absolute() else Path(args.dataset),
                      seed=args.seed, simulate_pick=args.simulate_pick)
    except UnfrozenConfigError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    (ROOT / args.out).write_text(json.dumps(metrics, indent=2))
    return 0 if metrics["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
