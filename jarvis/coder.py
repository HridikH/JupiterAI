"""Coder workflow: generate, scaffold, run, iterate.

Generation source, best first:
- Claude (when ANTHROPIC_API_KEY is set): dramatically better code than a
  local 7B-4bit, AND it skips the coder model swap entirely — no RAM spike,
  main model stays resident. This is the default when a key exists.
- Local Qwen2.5-Coder swap (fully offline fallback): swap in, generate,
  swap out, per the policy in llm.py.

Spoken UX contract (from the system prompt): say one short acknowledgment
immediately — generation takes 10s+ and silence feels broken.

Iteration loop (same for both sources): scaffold -> run -> if nonzero exit,
feed stderr back for a fix, up to MAX_FIX_ROUNDS. Never returns an
unverified code dump unless the run command itself is missing.
"""
from __future__ import annotations

import re
import threading
from typing import Generator

from . import config, prompts
from .llm import ModelManager, claude_available, claude_stream
from .sandbox import Sandbox, apply_coder_output

MAX_FIX_ROUNDS = 2
CLOUD_CODER_MAX_TOKENS = 32768  # a polished single-file site is easily 10k+ tokens;
                                # truncation mid-<file> costs the whole build


def _project_name(query: str) -> str:
    words = re.findall(r"[a-z0-9]+", query.lower())[:4]
    return "-".join(words) or "project"


def _open_site(sandbox: Sandbox, written: list[str]) -> str | None:
    """If the project produced a web page, launch it in the default browser.
    Returns the opened filename, or None if there was nothing to open."""
    entry = "index.html" if "index.html" in written else \
        next((w for w in written if w.endswith(".html")), None)
    if entry is None:
        return None
    import webbrowser
    webbrowser.open((sandbox.root / entry).resolve().as_uri())
    return entry


def code_task(
    models: ModelManager,
    query: str,
    interrupt: threading.Event | None = None,
) -> Generator[str, None, None]:
    """Yields short spoken status lines; detailed output goes to stdout."""
    yield "On it. Give me a moment to build that."

    sandbox = Sandbox(_project_name(query))
    messages = [{"role": "system", "content": prompts.CODER_SYSTEM_PROMPT},
                {"role": "user", "content": query}]

    if claude_available():
        try:
            yield from _rounds(models, sandbox, messages, interrupt, cloud=True)
            return
        except Exception as e:
            print(f"[coder] cloud coder failed ({type(e).__name__}: {e}); going local")
            yield "Cloud coder unreachable — using the local one."

    with models.coder():  # <-- main KV dropped here; weights too if RAM is tight
        yield from _rounds(models, sandbox, messages, interrupt, cloud=False)


def _rounds(
    models: ModelManager,
    sandbox: Sandbox,
    messages: list[dict],
    interrupt: threading.Event | None,
    cloud: bool,
) -> Generator[str, None, None]:
    for attempt in range(1 + MAX_FIX_ROUNDS):
        if interrupt is not None and interrupt.is_set():
            return
        out = []
        gen = (claude_stream(messages, max_tokens=CLOUD_CODER_MAX_TOKENS,
                             interrupt=interrupt)
               if cloud else models.coder_stream(messages, interrupt=interrupt))
        for delta in gen:
            out.append(delta)
            print(delta, end="", flush=True)  # full code -> console, not voice
        print()
        text = "".join(out)
        messages.append({"role": "assistant", "content": text})

        written, run_cmd = apply_coder_output(sandbox, text)
        if not written:
            yield "The coder didn't produce files. Check the console output."
            return
        if run_cmd is None:
            opened = _open_site(sandbox, written)
            if opened:
                yield (f"Done — {len(written)} files in {sandbox.root.name}. "
                       f"Opening {opened} in your browser.")
            else:
                yield f"Wrote {len(written)} files to {sandbox.root.name}, no run command given."
            return

        # Web pages are "run" by opening them, not by executing a command the
        # model made up — skip shell verification for pure-HTML projects.
        if all(w.endswith((".html", ".css", ".js")) for w in written):
            opened = _open_site(sandbox, written)
            if opened:
                yield (f"Done — {len(written)} files in {sandbox.root.name}. "
                       f"Opening {opened} in your browser.")
                print(f"[sandbox] {sandbox.root}")
                return

        result = sandbox.run(run_cmd)
        if result["exit_code"] == 0:
            stack = text.splitlines()[0][:80] if text else ""
            opened = _open_site(sandbox, written)
            yield (f"Done. {stack} {len(written)} files in {sandbox.root.name}, "
                   f"and it runs clean."
                   + (f" Opening {opened} in your browser." if opened else ""))
            print(f"[sandbox] {sandbox.root}\n[stdout]\n{result['stdout']}")
            return
        if attempt < MAX_FIX_ROUNDS:
            yield "First pass failed, fixing it."
            messages.append({"role": "user", "content":
                             f"`{run_cmd}` failed (exit {result['exit_code']}).\n"
                             f"stderr:\n{result['stderr']}\n"
                             "Fix it. Re-emit ONLY changed files and the run command."})
        else:
            yield (f"It's written to {sandbox.root.name} but still failing: "
                   f"{result['stderr'].splitlines()[-1][:120] if result['stderr'] else 'unknown error'}")
            return
