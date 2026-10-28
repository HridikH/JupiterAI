"""Trial reporting + metrics (V2).

Pure functions over a list of TrialOutcome so they're unit-testable without
running the pipeline. Produces automatic-only / user-assisted / overall accuracy,
incorrect-final count, recapture/error counts, a confusion matrix, and the V2
pass/fail verdict (>=80 correct, <=2 incorrect on 100 trials).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TrialOutcome:
    truth: str                      # ground-truth orderable number
    status: str                     # accepted | candidate_selection | recapture | error
    final_part: Optional[str] = None  # the part finally presented (auto or operator-picked)
    assisted: bool = False          # True if the operator picked from a candidate list

    @property
    def is_final(self) -> bool:
        return self.final_part is not None

    @property
    def correct(self) -> bool:
        return self.is_final and self.final_part == self.truth


def compute(results: list[TrialOutcome],
            pass_correct: int = 80, pass_incorrect_max: int = 2) -> dict:
    n = len(results)
    auto = [r for r in results if r.is_final and not r.assisted]
    assisted = [r for r in results if r.assisted]
    finals = [r for r in results if r.is_final]
    incorrect_finals = [r for r in finals if not r.correct]
    recaptures = [r for r in results if r.status == "recapture"]
    errors = [r for r in results if r.status == "error"]
    correct_total = sum(1 for r in results if r.correct)

    def acc(sub):
        return (sum(1 for r in sub if r.correct) / len(sub)) if sub else None

    # confusion matrix: truth -> {presented_or_outcome: count}
    cm: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        key = r.final_part if r.is_final else f"<{r.status}>"
        cm[r.truth][key] += 1

    return {
        "n": n,
        "automatic_only_accuracy": acc(auto),
        "automatic_only_n": len(auto),
        "user_assisted_accuracy": acc(assisted),
        "user_assisted_n": len(assisted),
        "overall_final_accuracy": (correct_total / n) if n else None,
        "correct_total": correct_total,
        "incorrect_finals": len(incorrect_finals),
        "incorrect_examples": [(r.truth, r.final_part) for r in incorrect_finals][:20],
        "recaptures": len(recaptures),
        "errors": len(errors),
        "confusion_matrix": {k: dict(v) for k, v in cm.items()},
        "pass": correct_total >= pass_correct and len(incorrect_finals) <= pass_incorrect_max,
        "pass_criteria": f">={pass_correct} correct AND <={pass_incorrect_max} incorrect",
    }


def format_report(m: dict) -> str:
    def pct(x):
        return "—" if x is None else f"{x*100:.1f}%"
    lines = [
        "===== Blinded Trial Report (V2) =====",
        f"trials:                {m['n']}",
        f"automatic-only acc:    {pct(m['automatic_only_accuracy'])} (n={m['automatic_only_n']})",
        f"user-assisted acc:     {pct(m['user_assisted_accuracy'])} (n={m['user_assisted_n']})",
        f"overall final acc:     {pct(m['overall_final_accuracy'])} ({m['correct_total']}/{m['n']})",
        f"incorrect finals:      {m['incorrect_finals']}",
        f"recaptures:            {m['recaptures']}",
        f"errors:                {m['errors']}",
        f"VERDICT:               {'PASS' if m['pass'] else 'FAIL'}  ({m['pass_criteria']})",
    ]
    if m["incorrect_examples"]:
        lines.append("incorrect (truth -> presented):")
        for t, p in m["incorrect_examples"]:
            lines.append(f"    {t} -> {p}")
    return "\n".join(lines)
