# JARVIS — Fully Local Voice Assistant (Apple Silicon, 16GB, $0/token)

Mic → VAD → mlx-whisper(medium) → router(Qwen2.5 0.5B) → Qwen2.5 7B → tools → Kokoro TTS → speaker, with barge-in. No cloud LLM anywhere.

## Setup

```bash
cd jarvis
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_models.py          # ~7GB download (add --all for coder, +4.3GB)
```

Grant Terminal mic access on first run (System Settings → Privacy → Microphone).

## Memory budget — why every model pick looks the way it does

| Component | Resident | Size |
|---|---|---|
| Qwen2.5 7B Instruct 4-bit (main) | always | ~4.5GB + KV |
| Qwen2.5 0.5B Instruct 4-bit (router) | **always — never cold-starts** | ~0.4GB |
| whisper **medium** (not large-v3-turbo) | always | ~1.5GB |
| Kokoro 82M | always | ~0.4GB |
| Qwen2.5-Coder 7B 4-bit | **on-demand only** | ~4.5GB |
| macOS + apps | — | ~3-4GB |

Steady state ≈ 10-11GB of 16. The two calls the ceiling forced:

1. **Whisper medium over large-v3-turbo.** Turbo costs ~1.6GB more — exactly the headroom the 7B's KV cache needs on long turns. Medium's accuracy is fine for near-field voice.
2. **Coder swaps in/out; main model never gets evicted permanently.** Main + coder simultaneously = ~9GB of weights before KV, over the Metal wired ceiling (~10.7GB default on 16GB — raise with `sudo sysctl iogpu.wired_limit_mb=12288` if you see Metal alloc failures). Policy in `jarvis/llm.py`:
   - **"cache" mode (default):** entering a coder task drops the main model's KV/prompt cache + MLX buffer pool; weights stay mmap'd. Return to chat is instant.
   - **"full" mode (auto-escalates when free RAM < 3GB):** main weights released too. Reload after the coder finishes is ~1-3s because the safetensors are still in the OS page cache — a re-map, not a cold disk read.
   - Cost lands on coding turns (already slow); chat turns never pay it. Coder is always unloaded when done.

## The "smart AND fast" mechanism (jarvis/router.py)

Three tiers, cheapest first: (1) regex fast-paths — time/date/"finish that", 0ms, no model; (2) the resident 0.5B emits one greedy label in <50ms and *answers trivial queries itself* with an ESCALATE hatch back to the 7B; (3) the 7B only wakes for real reasoning, search synthesis, or native tool-calling. Weather/stock answers are verbalized by **template**, not by any LLM — a 7B model reading a number aloud is pure wasted latency. Routing decisions print every turn so 0.5B misroutes are visible; the router prompt biases to MAIN on doubt because a shallow TRIVIAL answer to a hard question is the failure that makes the assistant feel dumb.

## Build stages — each runs standalone

**Stage 1 — streaming STT.** `python -m stages.stage1_stt`
Speak; partials update live, final prints on 700ms silence. Partials are rolling re-transcription every 0.5s (mlx-whisper has no incremental API); medium re-transcribes a few seconds of audio in ~200-400ms so the cadence holds.

**Stage 2 — router + main resident.** `python -m stages.stage2_llm_router`
Text REPL showing route + latency per turn. Try `what time is it` (fast-path, 0ms), `hi` (0.5B answers, 7B untouched), `explain the Monty Hall problem` (7B, watch first-token time — expect 0.3-0.6s warm). Both models load once and stay resident; the main model keeps a persistent prompt cache so the ~350-token system prompt isn't re-prefilled every turn (saves ~0.5-1s/turn).

**Stage 3 — streaming TTS.** `python -m stages.stage3_tts` (or `--say "test"` for TTS alone)
LLM deltas are chunked at sentence boundaries and synthesized while generation continues — audio should start before the printed reply finishes. Kokoro runs inline on the generation thread on purpose: it's 82M params (tens of ms/chunk), and a second Metal-issuing thread complicates the tight memory pool for no win.

**Stage 4 — tools.** `python -m stages.stage4_tools --test-tools` (no models, pure HTTP) then `python -m stages.stage4_tools`
web_search = SearXNG (`docker run -d -p 8080:8080 searxng/searxng`) with automatic DuckDuckGo-HTML fallback if it's not running; weather = Open-Meteo; stock = Stooq CSV (no key, no pandas — yfinance's pandas dependency is dead weight at this RAM tier). Try `weather in Tokyo` (no 7B involved), `nvidia stock`, `who won the last champions league` (SEARCH → 7B summarizes), `compare AAPL and MSFT` (7B emits native `<tool_call>`s; the holdback filter keeps tool JSON out of the spoken stream).

**Stage 5 — coder + sandbox.** `python -m stages.stage5_coder`
Try `build> a python script that prints the first 20 fibonacci numbers`, then something bigger. Watch the `[llm]` lines for the swap sequence and keep Activity Monitor's Memory Pressure open — this stage is the 16GB stress test. Coder output is parsed as `<file>`/`<run>` blocks, written under `~/jarvis_projects/<name>/`, executed, and stderr is fed back for up to 2 fix rounds — never an unverified dump. Sandbox = scoped subprocess (cwd-pinned, cleaned env, timeouts, rlimits): contains accidents, not hostile code — set `USE_DOCKER=True` in config for real isolation.

**Stage 6 — barge-in / full loop.** `python -m jarvis.main` (alias: `python -m stages.stage6_bargein`)
Continuous VAD during playback; sustained speech (300ms, aggressiveness 3) kills audio instantly, halts token generation at the next-token check, keeps the generated-but-unspoken remainder for 30s. `wait, finish that` speaks it. The frames that tripped the detector are recycled as pre-roll so your first word isn't clipped. **Honest limitation:** with open speakers, webrtcvad can hear Kokoro and self-trigger. Headphones solve it outright; macOS Voice Isolation mic mode nearly so; the stricter trigger window helps on speakers. Proper AEC (speexdsp) is a contained upgrade inside `bargein.py`. (Chose webrtcvad over silero deliberately — silero rejects echo better but drags in torch, 1GB+ of libs.)

## Desktop app / web dashboard — all 5 capabilities, one command

```
python scripts/fetch_models.py   # once, first — downloads with visible progress
python scripts/desktop.py        # app window; spawns the Python server itself
```

`python -m jarvis.server` still works standalone (opens a browser tab instead).
The desktop shell is a thin launcher: it starts the server, waits for port 7777,
then opens Chrome (or Brave/Edge/Chromium) in `--app` mode against it — a
frameless window with its own profile, no tabs or URL bar. No Node, no Electron:
the unsigned Electron binary is blocked outright by macOS Gatekeeper on current
releases, and a notarized browser you already have does the same job for 0 MB.
Prefetching first matters: model downloads inside the app look like a hang —
the progress bars go to the terminal, and unauthenticated HuggingFace downloads
are throttled (set HF_TOKEN to speed them up).

Glowing-orb control surface over the same modules the stages use. Pick a task
by clicking a card **or saying it** ("chat", "transcribe", "speak", "tools",
"coder") — the menu listens once whisper is warm. The orb color/motion tracks
state: cyan idle, green listening, amber thinking, blue speaking, purple coding.

Audio stays in Python (your Mac's mic/speakers via sounddevice); the browser is
control + transcript only. Models load lazily per mode, so the Speak tab costs
only Kokoro (~0.4GB) — nothing else loads until a mode needs it. Esc returns to
the menu; ■ stops speech. Typed input works everywhere typing makes sense
(chat, speak, tools, coder).

## The smarts stack (v2 brain)

Four upgrades, all stacked, all env-overridable in `config.py`:

1. **Qwen3 models.** Main is now Qwen3-4B-Instruct-2507 (benchmarks above the
   old Qwen2.5-7B at ~half the RAM); router is Qwen3-0.6B. Thinking mode is
   suppressed (`enable_thinking=False`) — no `<think>` spoken aloud.
   Re-run `python scripts/fetch_models.py` once to pull them.
2. **Speculative decoding.** The resident 0.6B doubles as the draft model for
   main generation — zero extra weights, typically 1.5-2x decode speed.
   `JARVIS_SPECULATIVE=0` disables.
3. **Persistent memory.** After each chat turn the 0.6B extracts lasting user
   facts (background thread) into `data/memory.db`; recall is keyword-overlap,
   injected onto the user turn so the KV prefix cache survives. Say "my name
   is X", restart, ask "what's my name".
4. **Cloud escalation.** New router label HARD: genuinely difficult queries
   stream from Claude when `ANTHROPIC_API_KEY` is set (model:
   `JARVIS_CLAUDE_MODEL`, default claude-sonnet-5). No key = HARD runs locally.
   Network failure mid-answer falls back to the local model if nothing was
   spoken yet.

## Latency expectations (M-series, warm)

Fast-path 0ms · trivial ~0.3s · weather/stock ~0.5-0.8s · main-model first spoken word ~0.8-1.2s · +0.5-2s per tool round · coder swap 2-8s before acknowledgment already spoken.

## Layout

```
jarvis/config.py     all knobs; memory budget documented at top
jarvis/prompts.py    system prompt (verbatim from spec), router + coder prompts
jarvis/audio.py      mic, VAD, interruptible player
jarvis/stt.py        whisper medium, rolling partials
jarvis/llm.py        ModelManager — residency + coder swap policy
jarvis/router.py     fast-paths + 0.5B classification
jarvis/tools.py      search/weather/stock + Qwen tool-call parsing
jarvis/agent.py      turn handling, tool rounds, spoken-text holdback
jarvis/sandbox.py    scoped subprocess / Docker exec
jarvis/coder.py      coder workflow (scaffold → run → fix loop)
jarvis/bargein.py    barge-in monitor + resume buffer
jarvis/main.py       assembled loop
jarvis/server.py     web dashboard server (FastAPI + WebSocket)
web/index.html       glowing-orb dashboard UI (single file, zero build step)
stages/              one standalone runner per build stage
```

## Known rough edges

`mlx-audio`'s Kokoro API has drifted between versions — `tts.py` targets `load_model(...).generate(...)`; adjust `_synthesize` if yours differs. The main-model prompt-cache reuse validates by token-prefix match and resets on any mismatch (correct, worst case just re-prefills). Stock-name→ticker mapping is a small dict; unknown names fall through to the 7B's tool-calling.
