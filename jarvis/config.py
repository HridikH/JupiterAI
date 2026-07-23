"""Central config. Every value here was chosen against a 16GB unified-memory ceiling.

Memory budget (resident, steady state):
    macOS + apps          ~3.0-4.0 GB
    Qwen2.5 7B 4-bit      ~4.5 GB   (weights + growing KV cache)
    Qwen2.5 0.5B 4-bit    ~0.4 GB   (router, NEVER unloaded — it's the latency lever)
    whisper medium (mlx)  ~1.5 GB
    Kokoro 82M            ~0.4 GB
    -----------------------------------
    total                 ~10-11 GB  → OK, with headroom for KV growth + audio buffers

    Qwen2.5-Coder 7B 4-bit (+4.5 GB) does NOT fit on top of that.
    It is loaded on demand and the main model makes room. See llm.py.

NOTE: macOS caps Metal "wired" memory at roughly 2/3 of RAM (~10.7GB on 16GB).
If you see Metal allocation failures during coder swaps, raise it:
    sudo sysctl iogpu.wired_limit_mb=12288
"""
import os
from pathlib import Path

# ---- Models -----------------------------------------------------------------
# All overridable by env var. Defaults are the Qwen3 generation: the 4B
# Instruct-2507 benchmarks above the old Qwen2.5-7B at roughly half the RAM,
# and the 0.6B shares its tokenizer, so one resident small model serves as
# router, speculative-decoding draft, AND memory extractor.
MAIN_MODEL = os.getenv("JARVIS_MAIN_MODEL", "mlx-community/Qwen3-4B-Instruct-2507-4bit")
ROUTER_MODEL = os.getenv("JARVIS_ROUTER_MODEL", "mlx-community/Qwen3-0.6B-4bit")
CODER_MODEL = os.getenv("JARVIS_CODER_MODEL",
                        "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit")  # load-on-demand ONLY

# Speculative decoding: the resident router doubles as the draft model for
# main-model generation (same tokenizer family required — true for Qwen3
# 0.6B + 4B). Zero extra weights resident; typically 1.5-2x decode speed.
SPECULATIVE = os.getenv("JARVIS_SPECULATIVE", "1") == "1"
NUM_DRAFT_TOKENS = int(os.getenv("JARVIS_DRAFT_TOKENS", "3"))

# Cloud escalation: when the router labels a query HARD and this key exists,
# the turn streams from Claude instead of the local 4B. Unset key = fully
# local, HARD falls back to MAIN.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("JARVIS_CLAUDE_MODEL", "claude-sonnet-5")

# Persistent memory (SQLite): facts about the user extracted in the
# background by the router model, recalled by keyword overlap each turn.
MEMORY_DB = Path(os.getenv("JARVIS_MEMORY_DB",
                           str(Path(__file__).resolve().parent.parent / "data" / "memory.db")))
MEMORY_RECALL_K = 6          # max facts injected per turn
MEMORY_MIN_SCORE = 1         # min keyword-overlap score to recall a fact
WHISPER_MODEL = os.getenv("JARVIS_WHISPER_MODEL",
                          "mlx-community/whisper-medium-mlx")  # medium, not large-v3-turbo:
                                                               # turbo's ~1.6GB extra competes
                                                               # with the LLMs at this RAM tier.
                                                               # On 8GB machines set
                                                               # JARVIS_WHISPER_MODEL=mlx-community/whisper-small-mlx
                                                               # (or base) — medium + memory
                                                               # pressure makes partials lag
                                                               # behind real time
KOKORO_MODEL = "prince-canuma/Kokoro-82M"
KOKORO_VOICE = os.getenv("JARVIS_TTS_VOICE", "am_michael")  # deep-ish male but crisp;
                                                            # am_onyx = deeper/slower,
                                                            # bm_george = deep British
KOKORO_SPEED = float(os.getenv("JARVIS_TTS_SPEED", "1.15"))  # energetic delivery

# ---- First-boot wake (clap/snap) --------------------------------------------
WAKE_ON_CLAP = os.getenv("JARVIS_WAKE_ON_CLAP", "1") == "1"
WAKE_SOUND = Path(os.getenv("JARVIS_WAKE_SOUND",
                            str(Path(__file__).resolve().parent.parent / "assets" / "wake.wav")))
WAKE_LINE = os.getenv("JARVIS_WAKE_LINE", "At your service, sir.")
CLAP_THRESHOLD = float(os.getenv("JARVIS_CLAP_THRESHOLD", "0.12"))  # lower = snappier snaps

# ---- Blabber refinement (smart transcription) -------------------------------
BLABBER_REFINE = os.getenv("JARVIS_BLABBER_REFINE", "1") == "1"
BLABBER_MIN_WORDS = int(os.getenv("JARVIS_BLABBER_MIN_WORDS", "14"))  # >= this = "rambly",
                                                                      # gets the 0.6B rewrite
CONTINUE_WINDOW_S = float(os.getenv("JARVIS_CONTINUE_WINDOW_S", "2.2"))  # extra patience when
                                                                      # an utterance trails off
                                                                      # mid-thought ("and, um…")

# ---- Coder swap policy (the real 16GB design choice) ------------------------
# "cache": free the main model's KV/prompt cache only; weights stay mapped.
#          Fast return to chat (no disk re-read), but peak RAM is higher.
# "full":  also drop main weights before loading coder. Max headroom; reload
#          after coder finishes is still quick (~1-3s) because the safetensors
#          stay in the OS page cache.
# "auto":  try "cache"; escalate to "full" when available RAM is below threshold.
CODER_SWAP_MODE = "auto"
CODER_SWAP_FULL_THRESHOLD_GB = 3.0

# ---- Audio ------------------------------------------------------------------
SAMPLE_RATE = 16000          # whisper native
FRAME_MS = 30                # webrtcvad requires 10/20/30ms frames
VAD_AGGRESSIVENESS = 2       # 0..3
VAD_TRIGGER_MS = 240         # this much voiced audio => utterance started
VAD_SILENCE_MS = 700         # this much silence => utterance ended
VAD_BARGE_IN_MS = int(os.getenv("JARVIS_BARGE_IN_MS", "450"))  # stricter trigger while
                             # TTS is playing (echo rejection on open speakers)
BARGE_IN_MIN_RMS = float(os.getenv("JARVIS_BARGE_IN_MIN_RMS", "0.08"))  # speaker echo is
                             # usually quieter at the mic than direct speech; gate on level
                             # too, not just VAD. Raise if it still self-interrupts.
POST_SPEECH_COOLDOWN_S = 0.3 # after speaking, discard this much mic audio (room tail)
MAX_UTTERANCE_S = 30
PARTIAL_INTERVAL_S = 0.5     # re-transcription cadence for streaming partials

# ---- TTS streaming ----------------------------------------------------------
TTS_SAMPLE_RATE = 24000
TTS_MIN_CHUNK_CHARS = 40     # flush to Kokoro at sentence boundary or this length

# ---- Generation -------------------------------------------------------------
MAX_TOKENS = 512
CODER_MAX_TOKENS = 2048
ROUTER_MAX_TOKENS = 8
TEMPERATURE = 0.4

# ---- Tools ------------------------------------------------------------------
SEARXNG_URL = "http://localhost:8080"   # self-hosted SearXNG; DDG HTML fallback if down
DEFAULT_LOCATION = "San Francisco"
TOOL_TIMEOUT_S = 8

# ---- Sandbox ----------------------------------------------------------------
PROJECT_DIR = Path.home() / "jarvis_projects"
SANDBOX_TIMEOUT_S = 60
SANDBOX_MAX_MEM_MB = 1024
USE_DOCKER = False           # subprocess sandbox default; set True if Docker is running

# ---- Barge-in ---------------------------------------------------------------
RESUME_WINDOW_S = 30         # keep interrupted reply text around for "wait, finish that"
