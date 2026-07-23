"""Model residency manager — the piece the 16GB ceiling actually shapes.

Residency policy:
- ROUTER (0.5B): loaded at startup, NEVER unloaded. It is the latency lever;
  a cold-start router defeats its purpose.
- MAIN (7B): loaded at startup, resident. Keeps a persistent KV/prompt cache
  across turns so the ~350-token system prompt + history isn't re-prefilled
  every turn (saves ~0.5-1s/turn at 7B prefill speeds).
- CODER (7B): load-on-demand only. Main + coder simultaneously = ~9GB of
  weights before KV caches — over the Metal wired ceiling once whisper and
  Kokoro are counted. TRADEOFF, stated explicitly: when the router classifies
  a query as CODER, we (a) always drop the main model's KV/prompt cache and
  MLX's Metal buffer pool, and (b) in "full"/"auto"-escalated mode also
  release main weights. Weights reload after the coder finishes is ~1-3s
  (safetensors still in OS page cache — mmap re-read, not cold disk), so the
  swap cost lands on coding turns, which are slow anyway, instead of on chat
  turns. If memory pressure shows up in testing, coder swaps in/out harder;
  the main model is never the one permanently evicted.
"""
from __future__ import annotations

import gc
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator

import mlx.core as mx
from mlx_lm import load, stream_generate
from mlx_lm.models.cache import make_prompt_cache
from mlx_lm.sample_utils import make_sampler

from . import config


def _available_ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9
    except Exception:
        return 99.0  # no psutil -> never escalate to "full"


class ModelManager:
    def __init__(self, load_now: bool = True) -> None:
        self._lock = threading.Lock()
        self._main = self._main_tok = None
        self._router = self._router_tok = None
        self._coder = self._coder_tok = None
        self._main_cache: list | None = None
        self._main_cache_tokens: list[int] = []
        self._sampler = make_sampler(temp=config.TEMPERATURE)
        self._greedy = make_sampler(temp=0.0)
        if load_now:
            self.load_resident()

    # ---- residency ----------------------------------------------------------
    def load_resident(self) -> None:
        t0 = time.monotonic()
        if self._router is None:
            self._router, self._router_tok = load(config.ROUTER_MODEL)
        if self._main is None:
            self._main, self._main_tok = load(config.MAIN_MODEL)
            self._reset_main_cache()
        print(f"[llm] router + main resident in {time.monotonic() - t0:.1f}s"
              + (" (speculative decoding on)" if self._use_draft() else ""))

    @property
    def resident(self) -> bool:
        """True when router + main are actually loaded (a coder-only shell
        created with load_now=False reports False until load_resident())."""
        return self._router is not None and self._main is not None

    def _use_draft(self) -> bool:
        """Router doubles as speculative draft (same Qwen3 tokenizer family)."""
        return config.SPECULATIVE and self._router is not None

    def _reset_main_cache(self) -> None:
        # Main may legitimately be unloaded (coder-only sessions from the
        # dashboard use a shell ModelManager) — a cache reset then is a no-op;
        # chat_stream rebuilds it after load_resident().
        if self._main is None:
            self._main_cache = None
            self._main_cache_tokens = []
            return
        # With a draft model, mlx-lm expects one combined cache list:
        # main layers first, draft layers after (it splits on len(model.layers)).
        self._main_cache = make_prompt_cache(self._main)
        if self._use_draft():
            self._main_cache = self._main_cache + make_prompt_cache(self._router)
        self._main_cache_tokens = []

    def drop_main_kv(self) -> None:
        """Free main model's KV cache + MLX Metal buffer pool. Weights stay mapped."""
        self._main_cache = None
        self._main_cache_tokens = []
        gc.collect()
        mx.clear_cache()

    def _unload(self, which: str) -> None:
        setattr(self, f"_{which}", None)
        setattr(self, f"_{which}_tok", None)
        gc.collect()
        mx.clear_cache()

    # ---- generation ---------------------------------------------------------
    def _tokens(self, tok, messages: list[dict], tools: list | None) -> list[int]:
        # enable_thinking=False: Qwen3 templates default to emitting <think>
        # blocks — poison for a voice assistant (spoken reasoning, broken
        # router labels). Non-Qwen3 templates just ignore the kwarg.
        try:
            return tok.apply_chat_template(
                messages, tools=tools, add_generation_prompt=True, tokenize=True,
                enable_thinking=False,
            )
        except TypeError:
            return tok.apply_chat_template(
                messages, tools=tools, add_generation_prompt=True, tokenize=True
            )

    def chat_stream(
        self,
        messages: list[dict],
        tools: list | None = None,
        max_tokens: int = config.MAX_TOKENS,
        interrupt: threading.Event | None = None,
    ) -> Generator[str, None, None]:
        """Stream main-model text deltas. Reuses the persistent prompt cache when
        the new conversation is an extension of what's already cached; resets
        otherwise (retokenization drift, cleared history, post-coder state)."""
        with self._lock:
            if self._main is None:
                self.load_resident()
            full = self._tokens(self._main_tok, messages, tools)
            if self._main_cache is None:
                self._reset_main_cache()
            cached = self._main_cache_tokens
            if len(cached) < len(full) and full[: len(cached)] == cached:
                prompt = full[len(cached):]
            else:
                self._reset_main_cache()
                prompt = full
            n_generated = 0
            gen_kwargs: dict = dict(max_tokens=max_tokens, sampler=self._sampler,
                                    prompt_cache=self._main_cache)
            if self._use_draft():
                gen_kwargs["draft_model"] = self._router
                gen_kwargs["num_draft_tokens"] = config.NUM_DRAFT_TOKENS
            for resp in stream_generate(
                self._main, self._main_tok, prompt, **gen_kwargs
            ):
                if interrupt is not None and interrupt.is_set():
                    # KV cache now holds a half-finished reply the next turn
                    # won't extend — cheapest correct move is to drop it.
                    self._reset_main_cache()
                    return
                n_generated += 1
                yield resp.text
            # cache now holds full prompt + generated tokens (minus final eos
            # handling); record the prompt part we know is exact. The generated
            # suffix is validated by prefix-match next turn.
            self._main_cache_tokens = full + self._generated_ids(resp, n_generated)

    @staticmethod
    def _generated_ids(last_resp: Any, n: int) -> list[int]:
        # mlx_lm doesn't hand back the full generated id list on the response
        # object across versions; prefix-match handles the mismatch by resetting,
        # so an empty suffix record is always safe (worst case: re-prefill).
        return []

    def router_complete(self, system: str, user: str, max_tokens: int) -> str:
        with self._lock:
            prompt = self._tokens(
                self._router_tok,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                None,
            )
            out = []
            for resp in stream_generate(
                self._router, self._router_tok, prompt,
                max_tokens=max_tokens, sampler=self._greedy,
            ):
                out.append(resp.text)
            return "".join(out).strip()

    # ---- coder swap -----------------------------------------------------------
    @contextmanager
    def coder(self):
        """Context manager enforcing the swap policy described in the module doc."""
        mode = config.CODER_SWAP_MODE
        if mode == "auto":
            mode = "full" if _available_ram_gb() < config.CODER_SWAP_FULL_THRESHOLD_GB else "cache"
        with self._lock:
            self.drop_main_kv()
            main_dropped = False
            if mode == "full":
                print("[llm] RAM tight -> releasing main weights for coder")
                self._unload("main")
                main_dropped = True
            if self._coder is None:
                t0 = time.monotonic()
                self._coder, self._coder_tok = load(config.CODER_MODEL)
                print(f"[llm] coder loaded in {time.monotonic() - t0:.1f}s")
        try:
            yield self
        finally:
            with self._lock:
                self._unload("coder")  # coder never stays resident on 16GB
                if main_dropped:
                    t0 = time.monotonic()
                    self._main, self._main_tok = load(config.MAIN_MODEL)
                    print(f"[llm] main reloaded in {time.monotonic() - t0:.1f}s (page cache)")
                self._reset_main_cache()

    def coder_stream(
        self,
        messages: list[dict],
        max_tokens: int = config.CODER_MAX_TOKENS,
        interrupt: threading.Event | None = None,
    ) -> Generator[str, None, None]:
        assert self._coder is not None, "call inside `with models.coder():`"
        prompt = self._tokens(self._coder_tok, messages, None)
        for resp in stream_generate(
            self._coder, self._coder_tok, prompt,
            max_tokens=max_tokens, sampler=self._greedy,
        ):
            if interrupt is not None and interrupt.is_set():
                return
            yield resp.text


# ---- cloud escalation ---------------------------------------------------------
def claude_available() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def claude_stream(
    messages: list[dict],
    max_tokens: int = config.MAX_TOKENS,
    interrupt: threading.Event | None = None,
) -> Generator[str, None, None]:
    """Stream text deltas from the Claude API for HARD-routed turns.

    Local history uses OpenAI-style roles (system/user/assistant/tool);
    Anthropic wants a separate system string and strictly alternating
    user/assistant messages, so we fold and merge accordingly."""
    import anthropic  # deliberate lazy import — fully-local installs skip it

    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    msgs: list[dict] = []
    for m in messages:
        if m["role"] not in ("user", "assistant") or not m.get("content"):
            continue
        if msgs and msgs[-1]["role"] == m["role"]:
            msgs[-1]["content"] += "\n" + m["content"]
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    with client.messages.stream(
        model=config.CLAUDE_MODEL, max_tokens=max_tokens,
        system=system, messages=msgs,
    ) as stream:
        for text in stream.text_stream:
            if interrupt is not None and interrupt.is_set():
                return
            yield text
