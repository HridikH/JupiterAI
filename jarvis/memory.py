"""Persistent memory: SQLite fact store + keyword recall.

Design constraints, in order: (1) must not add latency to the spoken turn,
(2) must not add resident RAM, (3) must survive restarts.

So: facts are extracted AFTER each chat turn on a background thread by the
already-resident 0.6B router model (no new weights), stored in SQLite, and
recalled by plain keyword overlap (no embeddings, no torch, ~0ms). Recall
text is appended to the user turn rather than the system prompt so the
main model's KV prefix cache stays valid across turns.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

from . import config, prompts

_STOP = set(
    "a an the i im i'm you your yours it its is are was were be been being do does "
    "did my his her their our of in on at to for with and or but if so not no yes "
    "what when where who whom why how which tell me about please jarvis can could "
    "would should will just like get got make made this that these those there "
    "have has had say said says go going went want wants".split()
)


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower())
            if w not in _STOP and len(w) > 2}


class Memory:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = str(db_path or config.MEMORY_DB)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS facts ("
                "id INTEGER PRIMARY KEY, text TEXT UNIQUE, "
                "keywords TEXT, created REAL)"
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    # ---- store ---------------------------------------------------------------
    def add(self, fact: str) -> None:
        fact = fact.strip().strip('"')
        if not fact:
            return
        kws = " ".join(sorted(_keywords(fact)))
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO facts (text, keywords, created) VALUES (?,?,?)",
                (fact, kws, time.time()),
            )

    # ---- recall --------------------------------------------------------------
    def recall(self, query: str, k: int = config.MEMORY_RECALL_K) -> list[str]:
        qk = _keywords(query)
        if not qk:
            return []
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT text, keywords, created FROM facts").fetchall()
        scored = [
            (len(qk & set(kw.split())), created, text)
            for text, kw, created in rows
        ]
        scored = [s for s in scored if s[0] >= config.MEMORY_MIN_SCORE]
        scored.sort(key=lambda s: (-s[0], -s[1]))  # overlap, then recency
        return [text for _, _, text in scored[:k]]

    def context(self, query: str) -> str:
        facts = self.recall(query)
        if not facts:
            return ""
        return ("[memory — known facts about the user]\n"
                + "\n".join(f"- {f}" for f in facts))

    # ---- background extraction ----------------------------------------------
    def extract_async(self, models, user_text: str, assistant_text: str) -> None:
        """Fire-and-forget: never blocks the spoken turn."""
        threading.Thread(
            target=self._extract, args=(models, user_text, assistant_text),
            daemon=True,
        ).start()

    def _extract(self, models, user_text: str, assistant_text: str) -> None:
        try:
            out = models.router_complete(
                prompts.MEMORY_EXTRACT_PROMPT,
                f"User said: {user_text}\nAssistant replied: {assistant_text[:300]}",
                60,
            )
            line = out.strip().splitlines()[0].strip() if out.strip() else "NONE"
            if "NONE" in line.upper()[:8] or not (8 < len(line) < 200):
                return
            self.add(line)
        except Exception:
            pass  # memory is best-effort; a failed extraction must never crash a turn
