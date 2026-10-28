"""Frozen thresholds — the single source of the decision boundaries.

The resolver's accept/candidate/recapture decision is governed ENTIRELY by the
numbers here. To make results reproducible and tamper-evident, the file carries a
`version` and a content `hash`; `eval/freeze.py` computes the hash and sets
`frozen: true`. Evaluation mode refuses to run on an unfrozen or hash-mismatched
file, so a demo can never silently use hand-tweaked thresholds.

Every result logs `config_version` + `config_hash` (see state machine).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config

# Sane DEV defaults. These are NOT frozen and MUST be replaced by
# eval/calibrate.py + eval/freeze.py on your real calibration set before trials.
DEFAULTS: dict[str, Any] = {
    "version": "thresholds/example-unfrozen",
    "frozen": False,
    # match thresholds (V3): auto-accept requires BOTH.
    "accept_score": 0.82,     # top candidate similarity must be >= this
    "accept_margin": 0.12,    # top1 - top2 similarity must be >= this
    # candidate screen band
    "candidate_score": 0.60,  # minimum score to be shown as a candidate at all
    "candidate_band": 0.15,   # candidates within this of top1 are shown together
    # image-quality gates for recapture reasons (U2)
    "quality": {
        "blur_var_min": 60.0,      # variance-of-Laplacian below this => blur
        "glare_frac_max": 0.12,    # fraction of near-saturated pixels above this => glare
        "offcenter_frac_max": 0.30,  # text centroid offset (frac of crop) above this => off_center
        "min_char_px": 14.0,       # estimated char height below this => too_far
    },
}

# Fields excluded from the content hash (the hash can't cover itself; frozen is
# the freeze marker, not content).
_HASH_EXCLUDE = {"hash", "frozen"}


@dataclass
class Thresholds:
    version: str
    frozen: bool
    accept_score: float
    accept_margin: float
    candidate_score: float
    candidate_band: float
    quality: dict[str, float] = field(default_factory=dict)
    config_hash: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Thresholds":
        return cls(
            version=d["version"],
            frozen=bool(d.get("frozen", False)),
            accept_score=float(d["accept_score"]),
            accept_margin=float(d["accept_margin"]),
            candidate_score=float(d["candidate_score"]),
            candidate_band=float(d["candidate_band"]),
            quality=dict(d.get("quality", {})),
            config_hash=d.get("hash"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "frozen": self.frozen,
            "accept_score": self.accept_score,
            "accept_margin": self.accept_margin,
            "candidate_score": self.candidate_score,
            "candidate_band": self.candidate_band,
            "quality": self.quality,
        }


def compute_hash(d: dict[str, Any]) -> str:
    payload = {k: v for k, v in d.items() if k not in _HASH_EXCLUDE}
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()


class UnfrozenConfigError(RuntimeError):
    pass


def load(path: Optional[Path] = None, *, require_frozen: bool = False) -> Thresholds:
    """Load thresholds. With require_frozen=True (evaluation mode), refuse unless
    the file is frozen AND its content hash verifies."""
    path = path or config.THRESHOLDS_FILE
    if path.exists():
        raw = json.loads(path.read_text())
    else:
        raw = dict(DEFAULTS)  # dev fallback; always unfrozen

    if require_frozen:
        if not raw.get("frozen"):
            raise UnfrozenConfigError(
                f"thresholds at {path} are not frozen — run `python -m eval.freeze` "
                "after calibration before evaluation mode."
            )
        expected = compute_hash(raw)
        if raw.get("hash") != expected:
            raise UnfrozenConfigError(
                f"thresholds hash mismatch (file tampered?): {raw.get('hash')} != {expected}"
            )

    t = Thresholds.from_dict(raw)
    if t.config_hash is None:
        t.config_hash = compute_hash(raw)
    return t


def freeze(path: Optional[Path] = None) -> Thresholds:
    """Mark the current thresholds file frozen and stamp its content hash."""
    path = path or config.THRESHOLDS_FILE
    raw = json.loads(path.read_text()) if path.exists() else dict(DEFAULTS)
    raw["frozen"] = True
    raw["hash"] = compute_hash(raw)
    path.write_text(json.dumps(raw, indent=2))
    return Thresholds.from_dict(raw)


def write_example(path: Path) -> None:
    path.write_text(json.dumps(DEFAULTS, indent=2))
