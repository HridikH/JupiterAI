"""OPTIONAL datasheet Q&A — answers strictly from retrieved page text, with citation.

HARD RULES (enforced by construction):
  * This module NEVER identifies a part and is never called during identification.
    It is handed an already-accepted part_number and a question about its datasheet.
  * Answers come ONLY from text retrieved out of the cached PDF (offline). Every
    answer carries a {part_number, page} citation (D3).
  * If no page matches, it returns {"available": False} — an explicit "unavailable"
    result, never a guess.

Default backend is EXTRACTIVE (no LLM): it returns the best-matching page snippet.
If GLASSES_DATASHEET_QA=1 AND the parent repo's MLX stack is importable, it will
instead have the local Qwen model phrase an answer *grounded in the retrieved page
text only*, still with the citation and the same "unavailable" fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from . import config
from .logging_setup import get

log = get("datasheet_qa")

_GROUNDED_SYSTEM = (
    "You answer ONLY from the datasheet excerpt provided. If the excerpt does not "
    "contain the answer, reply exactly 'UNAVAILABLE'. Never state or guess a part "
    "number. Be concise and quote values verbatim."
)


def _search(part_number: str, question: str):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from catalog.page_index import index_for_part

    idx = index_for_part(part_number)
    if idx is None:
        return None, []
    return idx, idx.search(question, k=3)


def _qwen_answer(question: str, page_text: str) -> str | None:
    """Best-effort local Qwen answer grounded in page_text. None if unavailable."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from jarvis.llm import ModelManager  # parent repo MLX stack
    except Exception as e:  # MLX not installed in this venv — fall back extractive
        log.info(f"MLX unavailable, using extractive QA: {e}")
        return None
    try:
        mm = ModelManager(load_now=True)
        prompt = f"Datasheet excerpt:\n{page_text[:2000]}\n\nQuestion: {question}"
        chunks = mm.chat_stream(
            [{"role": "system", "content": _GROUNDED_SYSTEM},
             {"role": "user", "content": prompt}])
        text = "".join(chunks) if hasattr(chunks, "__iter__") else str(chunks)
        text = text.strip()
        if not text or text.upper().startswith("UNAVAILABLE"):
            return None
        return text
    except Exception as e:  # pragma: no cover
        log.warning(f"Qwen QA failed, extractive fallback: {e}")
        return None


def answer(part_number: str, question: str) -> dict[str, Any]:
    """Return a grounded, cited answer or an explicit unavailable result."""
    idx, hits = _search(part_number, question)
    if not hits:
        return {"available": False, "part_number": part_number,
                "reason": "no matching datasheet page"}

    page_no, snippet = hits[0]
    citation = {"part_number": part_number, "page": page_no}

    if config.DATASHEET_QA_ENABLED:
        page_text = next((p.text for p in idx.pages if p.number == page_no), snippet)
        llm = _qwen_answer(question, page_text)
        if llm is not None:
            return {"available": True, "answer": llm, "citation": citation,
                    "source": "qwen", "snippet": snippet}

    # extractive default
    return {"available": True, "answer": snippet, "citation": citation,
            "source": "extractive", "snippet": snippet}
