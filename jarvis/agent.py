"""Turn handling: route -> answer-delta stream.

Everything yields text deltas so the TTS layer upstream can start speaking
immediately regardless of which path produced the words.

Tool paths by latency tier:
- weather/stock routed by the 0.5B: argument extracted by regex/dict; result
  verbalized by template. No 7B involvement at all (~0.5s end to end).
  If extraction fails, falls through to the main model's native tool-calling.
- web_search routed by the 0.5B: HTTP first, then the MAIN model synthesizes
  a spoken answer from snippets (summarization is a real language task).
- main-model turns run with tools enabled; Qwen2.5 emits
  <tool_call>{...}</tool_call> natively. Deltas are held back the moment a
  tool call might be starting so tool JSON is never spoken aloud.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Generator

from . import tools
from .llm import ModelManager, claude_stream

_LOCATION_RE = re.compile(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]{1,40})\s*\??$", re.I)
_TICKERS = {
    "apple": "AAPL", "microsoft": "MSFT", "google": "GOOGL", "alphabet": "GOOGL",
    "amazon": "AMZN", "nvidia": "NVDA", "tesla": "TSLA", "meta": "META",
    "netflix": "NFLX", "amd": "AMD", "intel": "INTC", "disney": "DIS",
    "boeing": "BA", "coinbase": "COIN", "palantir": "PLTR", "salesforce": "CRM",
}
_TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")
_TC_OPEN = "<tool_call>"


def _extract_location(query: str) -> str:
    m = _LOCATION_RE.search(query.strip())
    return m.group(1).strip(" ?.") if m else ""


def _extract_ticker(query: str) -> str | None:
    low = query.lower()
    for name, sym in _TICKERS.items():
        if name in low:
            return sym
    m = _TICKER_RE.search(query)
    return m.group(1) if m else None


def stream_turn(
    models: ModelManager,
    history: list[dict],
    query: str,
    kind: str,
    tool: str = "",
    interrupt: threading.Event | None = None,
) -> Generator[str, None, None]:
    """kind is Route.kind from router.route(); history is mutated in place."""
    if kind == "tool" and tool == "get_weather":
        loc = _extract_location(query)
        result = tools.get_weather(loc)
        answer = tools.speak_weather(result)
        history += [{"role": "user", "content": query},
                    {"role": "assistant", "content": answer}]
        yield answer
        return

    if kind == "tool" and tool == "get_stock":
        sym = _extract_ticker(query)
        if sym:
            answer = tools.speak_stock(tools.get_stock(sym))
            history += [{"role": "user", "content": query},
                        {"role": "assistant", "content": answer}]
            yield answer
            return
        kind = "main"  # couldn't extract -> let the 7B tool-call properly

    if kind == "tool" and tool == "web_search":
        results = tools.web_search(query)
        if not results:
            yield "Search came up empty. Try rephrasing."
            return
        ctx = "\n".join(f"- {r['title']}: {r['snippet']}" for r in results)
        history.append({"role": "user", "content":
                        f"{query}\n\n[web_search results]\n{ctx}\n"
                        "Answer from these results in one or two spoken sentences."})
        yield from _main_stream(models, history, interrupt, use_tools=False)
        return

    # ---- cloud escalation (router said HARD, API key present) ----------------
    if kind == "cloud":
        history.append({"role": "user", "content": query})
        parts: list[str] = []
        try:
            for d in claude_stream(history, interrupt=interrupt):
                parts.append(d)
                yield d
        except Exception:
            if not parts:  # API/network died before a word was spoken -> local
                yield from _main_stream(models, history, interrupt, use_tools=True)
                return
            yield " — connection dropped there."
        history.append({"role": "assistant", "content": "".join(parts)})
        return

    # ---- main model, native tool-calling, up to 2 tool rounds ----------------
    history.append({"role": "user", "content": query})
    yield from _main_stream(models, history, interrupt, use_tools=True)


class _Holdback:
    """Splits a delta stream into speakable text vs. withheld tool-call text.

    Anything after a '<' that could still become '<tool_call>' is withheld;
    once '<tool_call>' is confirmed we go permanently silent for this
    generation (the tool JSON and anything after it is machine traffic).
    """

    def __init__(self) -> None:
        self.held = ""
        self.silent = False

    def feed(self, delta: str) -> str:
        if self.silent:
            return ""
        buf = self.held + delta
        out = []
        while buf:
            i = buf.find("<")
            if i == -1:
                out.append(buf)
                buf = ""
                break
            out.append(buf[:i])
            rest = buf[i:]
            if rest.startswith(_TC_OPEN):
                self.silent = True
                buf = ""
                break
            if len(rest) < len(_TC_OPEN) and _TC_OPEN.startswith(rest):
                buf = ""
                self.held = rest  # ambiguous prefix — wait for more
                return "".join(out)
            # a '<' that is definitely not a tool call — speakable
            out.append(rest[0])
            buf = rest[1:]
        self.held = ""
        return "".join(out)

    def flush(self) -> str:
        held, self.held = self.held, ""
        return "" if self.silent else held


def _main_stream(
    models: ModelManager,
    history: list[dict],
    interrupt: threading.Event | None,
    use_tools: bool,
    max_tool_rounds: int = 2,
) -> Generator[str, None, None]:
    schemas = tools.TOOL_SCHEMAS if use_tools else None
    for _round in range(max_tool_rounds + 1):
        full: list[str] = []
        hb = _Holdback()
        for delta in models.chat_stream(history, tools=schemas, interrupt=interrupt):
            full.append(delta)
            speak = hb.feed(delta)
            if speak:
                yield speak
        tail = hb.flush()
        if tail:
            yield tail
        if interrupt is not None and interrupt.is_set():
            return
        text = "".join(full)
        history.append({"role": "assistant", "content": text})
        calls = tools.parse_tool_calls(text)
        if not calls:
            return
        for call in calls:
            result = tools.execute(call.get("name", ""), call.get("arguments", {}) or {})
            history.append({"role": "tool",
                            "content": json.dumps(result, ensure_ascii=False)})
    yield "I'm going in circles with tools — stopping here."
