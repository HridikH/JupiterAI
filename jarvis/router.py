"""Query routing — the "smart AND fast" mechanism.

Three tiers, cheapest first:
1. Zero-model fast paths (regex): time/date, "finish that" resume. 0ms.
2. Router model (0.5B, resident): one greedy label, <50ms. Trivial queries get
   answered BY the router itself (with an ESCALATE hatch), so the 7B never
   wakes for "what's 12 times 8".
3. Main 7B only for things that need it; coder 7B only for code.

Failure mode to watch: the 0.5B misclassifying a hard query as TRIVIAL and
giving a shallow answer. Two guards: the label prompt biases to MAIN on doubt,
and TRIVIAL answers pass through ESCALATE — if the router balks, the main
model takes over. Routing decisions are printed so misroutes are visible
during testing.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from . import config, prompts
from .llm import ModelManager

_TIME_RE = re.compile(r"\b(what('| i)?s the time|what time is it|current time)\b", re.I)
_DATE_RE = re.compile(r"\b(what('| i)?s (the date|today's date)|what day is (it|today))\b", re.I)
_RESUME_RE = re.compile(
    r"^(wait,? )?(finish|continue|resume)( (that|it|what you were saying))?\.?$", re.I
)

VALID_LABELS = {"TRIVIAL", "WEATHER", "STOCK", "SEARCH", "CODER", "HARD", "MAIN"}


@dataclass
class Route:
    kind: str            # fast_path | trivial | tool | main | coder | resume
    text: str = ""       # answer text for fast_path/trivial
    tool: str = ""       # tool name for kind == "tool"
    label: str = ""      # raw router label (for logging)


def _fast_path(query: str) -> Route | None:
    if _RESUME_RE.match(query.strip()):
        return Route(kind="resume", label="fastpath")
    if _TIME_RE.search(query):
        return Route(kind="fast_path", label="fastpath",
                     text=f"It's {datetime.datetime.now().strftime('%-I:%M %p')}.")
    if _DATE_RE.search(query):
        return Route(kind="fast_path", label="fastpath",
                     text=f"It's {datetime.datetime.now().strftime('%A, %B %-d')}.")
    return None


def route(models: ModelManager, query: str) -> Route:
    fp = _fast_path(query)
    if fp:
        return fp

    label = models.router_complete(
        prompts.ROUTER_SYSTEM_PROMPT, query, config.ROUTER_MAX_TOKENS
    ).split()[0].upper() if query.strip() else "MAIN"
    if label not in VALID_LABELS:
        label = "MAIN"

    if label == "TRIVIAL":
        answer = models.router_complete(prompts.ROUTER_ANSWER_PROMPT, query, 60)
        if "ESCALATE" in answer or not answer:
            return Route(kind="main", label="TRIVIAL->ESCALATE")
        return Route(kind="trivial", text=answer, label=label)
    if label == "WEATHER":
        return Route(kind="tool", tool="get_weather", label=label)
    if label == "STOCK":
        return Route(kind="tool", tool="get_stock", label=label)
    if label == "SEARCH":
        return Route(kind="tool", tool="web_search", label=label)
    if label == "CODER":
        return Route(kind="coder", label=label)
    if label == "HARD":
        from .llm import claude_available
        if claude_available():
            return Route(kind="cloud", label=label)
        return Route(kind="main", label="HARD->local")  # no API key: best local effort
    return Route(kind="main", label=label)
