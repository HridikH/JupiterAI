"""OCR confusion map + confusion-aware normalized edit distance.

The resolver matches observed markings to catalog strings. Plain Levenshtein
over-penalizes the mistakes OCR actually makes on tiny silk-screen markings
(0 vs O, 1 vs I, 5 vs S...). We give substitutions WITHIN a known confusable
group a reduced cost, so "NE555" read as "NE5S5" still scores high, while a
genuinely different part stays far away.

Everything here is pure, deterministic, and LLM-free.
"""
from __future__ import annotations

import re
from functools import lru_cache

# Groups of mutually-confusable glyphs (documented, auditable). A substitution
# between two members of the same group costs CONFUSE_COST instead of 1.0.
CONFUSION_GROUPS: list[set[str]] = [
    {"0", "O", "D", "Q"},
    {"1", "I", "L"},
    {"5", "S"},
    {"8", "B"},
    {"2", "Z"},
    {"6", "G"},
    {"9", "G", "Q"},
    {"4", "A"},
    {"7", "T"},
    {"U", "V"},
    {"M", "N"},
    {"C", "G"},
]

CONFUSE_COST = 0.25   # cost of a within-group substitution (vs 1.0 for unrelated)

# char -> set of chars it is confusable with (built from the groups)
_CONF: dict[str, set[str]] = {}
for _g in CONFUSION_GROUPS:
    for _c in _g:
        _CONF.setdefault(_c, set()).update(_g - {_c})


def normalize(s: str) -> str:
    """Uppercase, drop everything but [A-Z0-9], so 'NE555P' ~ 'ne-555 p'."""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if b in _CONF.get(a, ()):  # symmetric by construction
        return CONFUSE_COST
    return 1.0


@lru_cache(maxsize=4096)
def confusion_distance(a: str, b: str) -> float:
    """Weighted Levenshtein using the confusion substitution cost."""
    if a == b:
        return 0.0
    if not a:
        return float(len(b))
    if not b:
        return float(len(a))
    prev = [float(j) for j in range(len(b) + 1)]
    for i in range(1, len(a) + 1):
        cur = [float(i)] + [0.0] * len(b)
        ca = a[i - 1]
        for j in range(1, len(b) + 1):
            cur[j] = min(
                prev[j] + 1.0,                      # deletion
                cur[j - 1] + 1.0,                   # insertion
                prev[j - 1] + sub_cost(ca, b[j - 1]),  # substitution
            )
        prev = cur
    return prev[len(b)]


def similarity(observed: str, target: str) -> float:
    """1.0 == identical, 0.0 == maximally different. Normalizes both sides.

    Score = 1 - dist / max(len). Confusable mismatches barely dent the score;
    unrelated chars drop it fast.
    """
    a, b = normalize(observed), normalize(target)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    d = confusion_distance(a, b)
    return max(0.0, 1.0 - d / max(len(a), len(b)))
