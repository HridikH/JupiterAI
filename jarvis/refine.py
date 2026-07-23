"""Blabber refinement — smart transcription of thoughts flowing.

Raw whisper output of someone thinking out loud is full of fillers ("um",
"you know"), stutters ("I- I want"), immediate repetitions ("the the"), and
abandoned fragments. Two refinement layers, cheapest first:

1. Regex pass (~0ms, always on): strips pure fillers and collapses repeated
   words/bigrams. Deliberately conservative — words like "like" and "well"
   can carry meaning, so only unambiguous verbal tics are removed.
2. LLM pass (resident 0.6B, only for RAMBLY utterances): rewrites the
   transcript into the single clear request the user meant. Guarded by a
   word-overlap sanity check — if the small model wanders off-script, we
   fall back to the regex-cleaned text.
"""
from __future__ import annotations

import re

from . import config, prompts

_FILLER_RE = re.compile(r"\b(?:um+|uh+|uhm+|erm+|ah{2,}|hmm+|mmm+)\b[,.]?\s*", re.I)
_PHRASE_FILLER_RE = re.compile(
    r"\b(?:you know what i mean|you know|i mean|sort of like|kind of like)\b[,.]?\s*", re.I)
_WORD_REP_RE = re.compile(r"\b([A-Za-z']+)(?:[,\s]+\1\b)+", re.I)
_BIGRAM_REP_RE = re.compile(r"\b([A-Za-z']+\s+[A-Za-z']+)(?:[,\s]+\1\b)+", re.I)
_SPACE_RE = re.compile(r"\s{2,}")
_PUNCT_SPACE_RE = re.compile(r"\s+([,.!?;])")

# a transcript ending like this suggests the thought isn't finished
CONTINUE_RE = re.compile(
    r"(?:\b(?:and|but|so|because|then|also|plus|or|like|um+|uh+)|,)\s*[.…]?\s*$", re.I)


def clean_transcript(text: str) -> str:
    t = _PHRASE_FILLER_RE.sub("", text)
    t = _FILLER_RE.sub("", t)
    t = _BIGRAM_REP_RE.sub(r"\1", t)
    t = _WORD_REP_RE.sub(r"\1", t)
    t = _SPACE_RE.sub(" ", t)
    t = _PUNCT_SPACE_RE.sub(r"\1", t)
    t = re.sub(r"^[,.;\s]+", "", t)
    return t.strip()


def is_rambly(raw: str) -> bool:
    if len(raw.split()) >= config.BLABBER_MIN_WORDS:
        return True
    fillers = len(_FILLER_RE.findall(raw)) + len(_PHRASE_FILLER_RE.findall(raw))
    reps = len(_WORD_REP_RE.findall(raw)) + len(_BIGRAM_REP_RE.findall(raw))
    return fillers >= 2 or reps >= 2


def refine(models, raw: str) -> str:
    """Full refinement. `models` may be None (or not resident) — then the
    regex pass is all you get, which is still a strict improvement."""
    cleaned = clean_transcript(raw)
    if not cleaned:
        return raw.strip()
    if models is None or not is_rambly(raw):
        return cleaned
    try:
        out = models.router_complete(prompts.REFINE_PROMPT, cleaned, 90)
        out = out.strip().strip('"').splitlines()[0].strip() if out.strip() else ""
    except Exception:
        return cleaned
    if not out:
        return cleaned
    ow = set(re.findall(r"[a-z']+", out.lower()))
    cw = set(re.findall(r"[a-z']+", cleaned.lower()))
    if not ow or len(ow & cw) / len(ow) < 0.5 or len(out) > 2 * len(cleaned) + 20:
        return cleaned  # the 0.6B wandered — trust the regex pass
    return out


class AnyEvent:
    """Duck-typed Event: is_set() when any member is. Lets listen_once honor
    'mode switched OR continuation window expired' as one stop signal."""

    def __init__(self, *events) -> None:
        self.events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self.events)
