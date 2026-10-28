# PLAN — `v4-glasses/`
## Smart Glasses for Hands-Free Datasheet Retrieval (ECE 445, Team 6, Fall 2026)

> **STATUS: PLAN ONLY. No implementation code written yet. Awaiting your approval.**

This folder is a new, self-contained version that repurposes the JarvisV3 local-first
assistant stack into the ECE 445 glasses system. **Nothing outside `v4-glasses/` is
modified.** Useful pieces from `jarvis/`, `web/`, and `scripts/` are *copied forward*,
never moved.

---

## 0. Naming decision (please confirm)

Existing convention is `JarvisV2/`, `JarvisV3/` at the `Projects/` level; there are **no
version subfolders inside** the repo, and `stages/` are build stages, not versions. Your
example was `v3-glasses/`, but this repo is already "V3", so I chose **`v4-glasses/`**
(next version number + descriptive suffix) as a subfolder of the JarvisV3 repo. If you'd
rather it be a sibling project (`Projects/JarvisV4-Glasses/`) or keep the literal
`v3-glasses/`, say so and I'll rename before writing code.

---

## 1. What I will REUSE (copy forward)

| Source in repo | Reused as | Why it fits |
|---|---|---|
| `jarvis/server.py` WS architecture (`emit()` → `queue.Queue` → pump thread → `broadcast()`, `lifespan`, `create_app`, wsproto backend) | `server/ws.py` + `server/app.py` | Exactly the state-push model the pipeline needs (received → processing → accepted/candidate/recapture/error). Battle-tested handshake quirks (wsproto, module-level WebSocket import) already solved. |
| `jarvis/config.py` env-overridable central-config idiom | `server/config.py` + `server/thresholds.py` (frozen) | Same documented, override-by-env style. Thresholds get a stricter *frozen + hashed* variant. |
| `web/index.html` shell (WS `connect()`/`handle()`, chips, transcript `log`, `toast()`, Three.js orb, status word) | `ui/web/index.html` | The control-surface skeleton and connection/status plumbing transfer directly; the 5-mode menu is replaced by the three result views. |
| `scripts/desktop.py` (Chromium `--app` launcher, port-wait, process mgmt) | `ui/desktop.py` | Gives a frameless desktop window without shipping an unsigned Electron binary — your existing approach. |
| `scripts/doctor.py` health-check pattern | `tools/doctor.py` | Preflight checks (deps, OCR backend, catalog frozen, PDFs hashed). |
| `jarvis/llm.py` `ModelManager.chat_stream` (MLX Qwen) | `server/datasheet_qa.py` (OPTIONAL) | Only for answering datasheet questions *from retrieved page text with citation*. **Hard-walled off from identification.** |
| `jarvis/tts.py` `KokoroStreamer` | `server/readout.py` (OPTIONAL, off by default) | Short spoken result readout (U-nice-to-have). |
| venv + pinned `requirements.txt` convention | `server/requirements.txt` | Same reproducible-env discipline, newly pinned for this subsystem. |

## 2. What I will DROP (not copied)

Whisper/STT, VAD, barge-in, wake-clap, mic/speaker capture (`stt.py`, `audio.py`,
`bargein.py`, `wake.py`), the router/memory/coder/sandbox/tools/agent/refine/prompts
assistant logic, and **the Anthropic cloud-escalation path** (`claude_stream` — banned:
no cloud in the runtime path). Input is a button-triggered JPEG over Wi-Fi, not voice, so
none of the audio-in or assistant-reasoning stack applies.

## 3. Core safety invariant (drives every design choice)

Identification is **deterministic catalog matching with frozen thresholds**. No LLM ever
produces or guesses a part number. Auto-accept requires **both** a score threshold **and**
a separation (margin) threshold; otherwise candidate-selection; otherwise recapture with a
reason. The resolver, thresholds, and catalog are entirely LLM-free. The optional Qwen path
answers datasheet questions from already-retrieved page text only, with a citation, and
returns "unavailable" when unsupported.

---

## 4. Final directory tree

```
v4-glasses/
├── PLAN.md                      # this file
├── README.md                    # runbook (setup→flash→wifi→catalog→calibrate→freeze→trials→wipe→troubleshoot)
├── Makefile                     # setup / run / test / wipe convenience targets
│
├── firmware/                    # ESP-IDF project, ESP32-S3-WROOM-1-N8R8
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults       # PSRAM octal, 240MHz, partition for N8 flash
│   ├── partitions.csv
│   ├── Kconfig.projbuild        # Wi-Fi SSID/pass, server URL, shared token (menuconfig — NOT hardcoded)
│   ├── main/
│   │   ├── board_config.h       # ALL pins, ONE place, every value a TODO (filled from Rev A.2/B schematic)
│   │   ├── camera.c/.h          # esp32-camera OV5640 init, JPEG 2592x1944, PSRAM fb, >2MB reject (C1)
│   │   ├── autofocus.c/.h       # OV5640 AF firmware load over SCCB, trigger, poll, focus timestamps (C2)
│   │   ├── worklights.c/.h      # 2 LED enables, ON only during focus+exposure (C4)
│   │   ├── button.c/.h          # active-low, 250ms debounce, one-per-press, release-gated (I1,I2)
│   │   ├── status_led.c/.h      # ready/processing/accepted/recapture/error ≤250ms + debug GPIO (I3)
│   │   ├── battery.c/.h         # 100k/100k divider, eFuse-cal ADC, settling delay, 2-pt NVS cal (P6)
│   │   ├── uploader.c/.h        # request_id, POST JPEG+metadata, buffer-until-ACK, 5s/2-retry, wrong-ID ignore (E1,E2,E3)
│   │   ├── net.c/.h             # Wi-Fi STA from Kconfig
│   │   ├── logging.h            # fixed serial format the eval parsers consume
│   │   └── main.c               # state machine wiring the above
│   └── README.md                # idf.py set-target/menuconfig/build/flash
│
├── server/                      # FastAPI receiver + deterministic pipeline
│   ├── requirements.txt         # pinned
│   ├── config.py                # paths, host-bind, size limits, OCR backend select
│   ├── thresholds.py            # loads frozen thresholds.json (version+hash), refuses eval if unfrozen
│   ├── app.py                   # FastAPI app, routes, lifespan (from server.py)
│   ├── ws.py                    # emit/queue/pump/broadcast (from server.py)
│   ├── protocol.py              # shared constants/schemas mirroring docs/protocol.md
│   ├── receiver.py              # validate id/sha256/bytes/size/JPEG-decode BEFORE OCR; dedup by id (V1)
│   ├── state.py                 # per-request state machine + per-stage timestamps
│   ├── preprocess.py            # central 60%×60% crop + FIXED versioned enhancement variants
│   ├── ocr/
│   │   ├── base.py              # OCREngine interface (text, boxes, confidences, timing)
│   │   ├── apple_vision.py      # pyobjc Vision backend (default, macOS)
│   │   └── fallback.py          # PaddleOCR/EasyOCR backend
│   ├── resolver.py              # normalized edit distance + OCR confusion map + package/aliases → ranked list, score+margin (V3)
│   ├── confusion.py             # 0/O,1/I/l,5/S,8/B,2/Z … map
│   ├── catalog_api.py           # load parts.yaml/manifest.yaml, open cached PDF at cited page
│   ├── datasheet_qa.py          # OPTIONAL Qwen/MLX QA strictly from retrieved page text + citation (walled off)
│   ├── readout.py               # OPTIONAL Kokoro TTS readout (off by default)
│   ├── privacy.py               # local-only storage + single wipe command
│   └── logging_setup.py         # structured logs, request_id everywhere
│
├── ui/                          # reuse Chromium-app shell
│   ├── desktop.py               # from scripts/desktop.py
│   └── web/
│       ├── index.html           # accepted / candidate / recapture-error views, conn+battery+timings, debug panel
│       └── assets/              # (styles split out if index grows)
│
├── catalog/
│   ├── parts.yaml               # per orderable part: mfr, package, top-markings, aliases, min char height
│   ├── manifest.yaml            # per orderable number: mfr, number, package, URL, retrieval date, sha256, cache path (D1)
│   ├── schema/
│   │   ├── parts.schema.yaml
│   │   └── manifest.schema.yaml
│   ├── validate.py              # fails on unmapped acceptable part or hash mismatch
│   ├── fetch.py                 # one-time online PDF fetch, records hashes, requires reviewed:true per entry (D2)
│   ├── page_index.py            # PyMuPDF per-page text; abs-max vs recommended-op kept separate (D3)
│   └── cache/                   # downloaded PDFs (gitignored); runtime never hits network
│
├── eval/
│   ├── calibrate.py             # run held-out calibration set → propose thresholds
│   ├── freeze.py                # freeze thresholds.json with version+hash (gates eval mode)
│   ├── blinded_trial.py         # randomized ≥20 parts ×≥5 trials (100); operator never sees ground truth (V2)
│   ├── report.py                # auto/assisted/overall accuracy, incorrects, recaptures, errors, confusion matrix
│   ├── latency.py               # press→first-result median + nearest-rank p95, excl. user decision time (U3)
│   ├── parse_device_logs.py     # C1,C2,E1,E2,E3,I1,I3 pass/fail table from serial logs
│   └── datasets/                # calibration/ and trial/ image sets (gitignored)
│
├── tools/
│   ├── fake_device.py           # replays saved JPEGs through the REAL protocol: retries, dup IDs, wrong ACKs, drops
│   ├── fuzzer.py                # payload fuzzer, values just below/above each limit (V1)
│   ├── timing.py                # timing helpers for latency/throughput
│   └── doctor.py                # preflight health check
│
├── docs/
│   ├── protocol.md              # wire protocol — WRITTEN FIRST, both sides implement against it
│   ├── traceability.md          # every req ID → code + test + evidence output
│   └── runbook.md               # deep-dive ops (README links here)
│
└── tests/
    ├── test_receiver.py
    ├── test_resolver.py
    ├── test_manifest_validator.py
    ├── test_state_machine.py
    └── test_e2e_simulator.py    # fake_device → server → accepted/candidate/recapture
```

---

## 5. Requirement → module traceability (full map goes in `docs/traceability.md`)

| ID | Requirement (short) | Primary module(s) | Test / evidence |
|---|---|---|---|
| **C1** | Reject JPEG > 2 MB | `firmware/main/camera.c`; `server/receiver.py` | `tools/fuzzer.py`, `eval/parse_device_logs.py` |
| **C2** | Autofocus ≤ 1.5 s @ 100–110 mm | `firmware/main/autofocus.c` | focus timestamps → `eval/parse_device_logs.py` |
| **C3** | *TODO: exact C3 definition not in brief* (assumed capture resolution/quality 2592×1944) | `firmware/main/camera.c` | TODO — confirm from design doc |
| **C4** | Worklights only during focus+exposure | `firmware/main/worklights.c` | logic-analyzer via debug GPIO |
| **I1** | One capture per press, release required | `firmware/main/button.c` | `eval/parse_device_logs.py` |
| **I2** | Presses ignored during active transaction | `firmware/main/button.c`, `main.c` | device-log parse |
| **I3** | Status LED ≤ 250 ms + debug transition GPIO | `firmware/main/status_led.c` | logic-analyzer timing |
| **E1** | Unique request_id; buffer until matching ACK | `firmware/main/uploader.c`; `server/receiver.py` | `tools/fake_device.py` |
| **E2** | 5 s timeout, max 2 retries, then visible error | `firmware/main/uploader.c` | `fake_device.py` (dropped conn) |
| **E3** | Ignore ACKs with wrong request_id | `firmware/main/uploader.c` | `fake_device.py` (wrong ACK) |
| **V1** | Validate id/sha256/bytes/size/decode before OCR; dedup | `server/receiver.py` | `tests/test_receiver.py`, `tools/fuzzer.py` |
| **V2** | Blinded trial ≥80 correct, ≤2 incorrect | `eval/blinded_trial.py`, `report.py` | trial report |
| **V3** | Auto-accept iff score AND margin thresholds | `server/resolver.py`, `thresholds.py` | `tests/test_resolver.py` |
| **D1** | Each orderable number its own manifest entry | `catalog/manifest.yaml`, `validate.py` | `tests/test_manifest_validator.py` |
| **D2** | Runtime offline; reviewed:true gate | `catalog/fetch.py`, `catalog_api.py`, `privacy.py` | validator + runtime no-net |
| **D3** | Page citations; unsupported → "unavailable" | `catalog/page_index.py`, `datasheet_qa.py` | `tests/` + manual |
| **U1** | Accepted/candidate views w/ full details + open PDF | `ui/web/index.html`, `catalog_api.py` | manual + e2e |
| **U2** | Recapture reason (blur/glare/off-center/too far/unreadable) | `server/resolver.py`, `ui/web/index.html` | `tests/test_resolver.py` |
| **U3** | Latency report; user decision time logged separately | `eval/latency.py`, `server/state.py` | latency report |
| **P6** | Battery ADC, 2-pt NVS calibration | `firmware/main/battery.c` | bench measurement |

---

## 6. Build order & commits (your required staging)

1. **Protocol + simulator + receiver** — `docs/protocol.md`, `server/` skeleton (app/ws/receiver/state/config), `tools/fake_device.py`, `tools/fuzzer.py`, `tests/test_receiver.py`. → commit.
2. **OCR + resolver** — `preprocess.py`, `ocr/*`, `resolver.py`, `confusion.py`, `thresholds.py`, `tests/test_resolver.py`. → commit.
3. **Catalog + datasheets** — `parts.yaml`/`manifest.yaml` + schemas, `validate.py`, `fetch.py`, `page_index.py`, `catalog_api.py`, optional `datasheet_qa.py`, `tests/test_manifest_validator.py`. → commit.
4. **UI** — `ui/desktop.py`, `ui/web/index.html` (3 views + debug panel), optional `readout.py`. → commit.
5. **Firmware** — full ESP-IDF project with `board_config.h` TODOs. → commit.
6. **Eval** — `calibrate.py`, `freeze.py`, `blinded_trial.py`, `report.py`, `latency.py`, `parse_device_logs.py`, `tests/test_state_machine.py`, `tests/test_e2e_simulator.py`, `docs/traceability.md`, `README.md`. → commit.

All Python tests must pass (via the simulator — no hardware needed) before I call it done.

---

## 7. Open TODOs / ambiguities (I will NOT guess these)

1. **Folder name** — `v4-glasses/` vs `v3-glasses/` vs sibling `JarvisV4-Glasses/` (see §0).
2. **C3 definition** — not spelled out in the brief; placeholder assumption above. Need the design-doc text.
3. **All firmware pin numbers** — left as TODOs in `board_config.h`; you fill from Rev A.2/Rev B schematic. I will not invent pins.
4. **Shared-token value & Wi-Fi creds** — via Kconfig/menuconfig + server config file, never committed. I'll ship `.env.example` / `sdkconfig.defaults` placeholders only.
5. **Exact threshold numbers** — produced by `eval/calibrate.py` on YOUR calibration set, then frozen. I ship the mechanism + sane placeholder defaults clearly marked unfrozen.
6. **OCR default backend** — Apple Vision via pyobjc assumed primary on macOS. Confirm pyobjc install is acceptable (pip, in-venv; no system-wide install without asking).
7. **Catalog contents** — I'll seed `parts.yaml`/`manifest.yaml` with a small *illustrative* example + validator; the real ≥20-part catalog is your data.
8. **Install policy** — PaddleOCR/EasyOCR/PyMuPDF/pyobjc are sizable. All go in the project venv via pinned `requirements.txt`; I will **ask before anything system-wide** (e.g. Homebrew).
```
