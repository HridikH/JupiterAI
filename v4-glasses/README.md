# Smart Glasses for Hands-Free Datasheet Retrieval

ECE 445 · Team 6 · Fall 2026. A head-worn ESP32-S3 captures an autofocus JPEG of
an electronic component on a button press and uploads it over local Wi-Fi to a
laptop, which validates the image, runs OCR, and **deterministically** matches the
marking against a curated catalog to produce exactly one of three outcomes:

1. **one accepted part** (opens its reviewed datasheet PDF at the cited page),
2. a **candidate-selection** screen when several parts are plausible, or
3. a **recapture / error** result with an actionable reason.

**Core principle:** the system never presents an uncertain match as a definite
answer. Identification is deterministic catalog matching with *frozen* thresholds.
**No LLM ever produces or guesses a part number.** Everything runs **offline** at
demo time; images and logs stay on the laptop.

This is a new version folder built from the JarvisV3 local-assistant stack — it
reuses that repo's FastAPI + WebSocket architecture, config idiom, and Chromium
`--app` UI shell, and drops all the voice/assistant pieces. See
[`PLAN.md`](PLAN.md) for what was reused vs dropped.

```
firmware/  ESP-IDF project (ESP32-S3-WROOM-1-N8R8 + Waveshare OV5640)
server/    FastAPI receiver + deterministic pipeline (preprocess→OCR→resolver)
ui/        Chromium --app console (three result views)
catalog/   parts + datasheet manifest + validator + offline fetch + page index
eval/      calibration, blinded trial, latency, device-log parsers
tools/     fake-device simulator, payload fuzzer, timing, doctor
docs/      protocol spec, requirements traceability, runbook
tests/     receiver, resolver, manifest, state machine, eval, e2e simulator
```

---

## Project status (2026-11-10)

**Hardware**
- The PCB is assembled and **powered up**.
- A few components burned out during bring-up. Replacements have been ordered.
- We are now in the **final integration stage**: PCB, ESP32-S3 and OV5640 camera, then upload to the laptop pipeline.
- **Expected completion: this weekend (Nov 14–15, 2026).**

**Software (laptop side): feature-complete for the demo, 157 tests passing**
- **Recognition pipeline:** preprocess, Apple Vision OCR, then a deterministic, confusion-aware catalog resolver with accept, candidate and recapture outcomes.
- **Live camera feed:** the AR pinout is overlaid on the chip, with pin 1 orientation and DIP layout.
- **Phone camera:** works as a stand-in for the glasses. The HTTPS link is printed at startup.
- **Web datasheet fallback:** parts that aren't in the catalog are looked up and cached automatically.
- **Voice Q&A:** hands-free questions are answered from the accepted part's datasheet.
- **Choosing a candidate:** when the operator picks a part on the candidate screen, that part is accepted and the live pinout switches to it.

**Recent fixes**
- **No more endless "blurry":** a sharp photo of a chip on a plain background was flagged as blurry on every recapture. A clean part-number read now takes priority over the blur metric.
- **No more look-alike matches:** a clear read of a part that isn't in the catalog (e.g. 74HC27N vs 74HC04N) is no longer accepted as a similar catalog part. It goes to the web lookup instead. OCR slips like O/0 or a dropped first letter still match.
- **New catalog parts:** SN74HC153N and SN74HC27N, with pin maps entered by hand and reviewed. Their TI datasheets have no text pin table.

**Open items**
- **Firmware:** GPIO pins in `firmware/main/board_config.h` still need to be filled in from the final schematic.
- **Thresholds:** not yet calibrated and frozen (`eval/` → `server/thresholds.json`).
- **Web-found parts:** if a datasheet has no text pin table, its pin map has to be entered by hand.

---

## Quick start (laptop side)

```bash
cd v4-glasses
make setup          # create .venv (py3.11) + install pinned server/requirements.txt
make test           # run the full test suite (no hardware needed)
make run            # start server + open the console window (Chromium --app)
```

`make run` is the single "launch server + UI together" command. To run the server
alone (e.g. for the simulator): `make server`.

### Jupiter console + hands-free datasheet Q&A
The console uses the JarvisV3 **Jupiter orb** visual (vendored `ui/web/assets/three.min.js`,
so it renders offline); the orb's colour tracks pipeline state (amber idle, blue
processing, green accepted, purple candidate, yellow recapture, red error, cyan
listening). Once a part is **accepted**, a Q&A dock appears:

- **🎙 Ask** — speak a question; **Whisper** (mlx-whisper, mic via Python) transcribes
  it and the answer comes back **grounded in the cached datasheet with a page citation**.
- or type the question.

### Live camera feed + pinout overlay + always-listening voice (option B)
The console shows a **live MJPEG feed** (`<img src="/stream">`). For real hardware the
firmware runs an on-device MJPEG server (`firmware/main/stream.c`, `:81/stream`); for a
hardware-free demo push frames with the simulator:

```bash
# terminal 2, alongside a fake accepted part:
python -m tools.make_sample_part --marking NE555P --line2 "TI 7M4"
python -m tools.fake_device  --token dev --image eval/datasets/samples/ne555p.jpg   # accept
python -m tools.stream_sim   --token dev --image eval/datasets/samples/ne555p.jpg   # live feed
```

A **live AR pinout overlay** labels every pin *on the chip itself* in the feed and
follows it as it moves or rotates. Each live frame, the server finds the accepted
part's top marking (Apple Vision, rotated text boxes), grows the dark package body
outward from it, and lays the pins along the body edges. Pin 1 comes from the
marking's reading direction: with the text upright, pin 1 is bottom-left, so no
notch detection is needed and it still works when the chip is upside-down. Power pins
are red, ground blue, NC grey; a `*` marks a pin number that was inferred rather
than read (see below). If the marking leaves the view, the overlay falls back to
the static pin diagram. Tracking runs about 10 Hz on an M-series Mac and only while
the overlay is on.

Pin maps come from (first that works): a reviewed `pins:` list in
`catalog/parts.yaml` (for scanned datasheets like CD4011B) → the datasheet's
*Pin Functions* table (PyMuPDF table extraction; picks the right package column
and the right device's table in family sheets like TL07x) → the *TOP VIEW*
pin-diagram text → the legacy text parser. See `server/pinout.py`.

```bash
# hardware-free AR demo: a synthetic chip that drifts and rotates
python -m tools.make_sample_part --marking LM358P --line2 "TI 7M4" --scene --pins 8
python -m tools.fake_device --token dev --image eval/datasets/samples/lm358p_scene.jpg   # accept
python -m tools.stream_sim  --token dev --chip LM358P --pins 8                          # live feed
```

**Where the live frames come from:**
- **Glasses:** set `GLASSES_DEVICE_STREAM_URL=http://<glasses-ip>:81/stream` and the
  server pulls the firmware's MJPEG feed into the laptop (reconnects on its own).
- **Phone:** `/mobile` → **● Live** (about 10 fps at 1280 px, no frame backlog).
- **Simulator:** `tools.stream_sim` (above), or anything that POSTs JPEGs to `/frame`.

Toggle the overlay:

- **Voice:** say *"show pinout"* / *"hide pinout"* (always-listening).
- **Button:** the ▢ Pinout pill, or the UI **Voice** pill to start/stop listening.

Enable always-listening at launch with `GLASSES_VOICE_ALWAYS=1` (off by default so tests /
headless runs never grab the mic). Voice commands are matched **deterministically** (regex,
no LLM); unmatched speech becomes a datasheet question about the last accepted part.

> **WALL:** voice/LLM are used ONLY to answer questions about an *already-accepted*
> part's datasheet — never to identify a part. Identification stays deterministic
> catalog matching. Default answers are **extractive** (no model); set
> `GLASSES_DATASHEET_QA=1` for grounded Qwen phrasing, `GLASSES_TTS_READOUT=1` for
> spoken answers. First voice use triggers a **macOS mic permission** prompt for the
> Python process, and downloads the Whisper model (~1.5 GB) once.

> **macOS OCR:** the default OCR backend is Apple Vision via `pyobjc`. If the
> doctor warns it's unavailable, `pip install pyobjc-framework-Vision
> pyobjc-framework-Quartz` inside `.venv`, or set `GLASSES_OCR_BACKEND=easy`
> (installs EasyOCR) / `mock` (tests). There is **no npm** — the UI is plain HTML
> served by FastAPI.

### Use your phone as the camera (no ESP32 needed)
The phone feeds the real pipeline over HTTPS (mobile browsers require a secure
context for the camera *and* the SHA-256 the protocol needs). One-time setup:

```bash
make cert            # self-signed cert incl. your LAN IP -> data/cert.pem + key.pem
make server          # now listens http://127.0.0.1:8777 (desktop) + https://<lan-ip>:8443 (phone)
```

On the phone (same Wi-Fi): open **`https://<lan-ip>:8443/mobile`** (the `make cert`
output prints the exact URL), accept the one-time self-signed warning, enter the
shared token in Settings, then:
- **📷 Capture part** — snaps a photo straight into `/capture`; the result appears on
  the phone *and* on the laptop console (shared state). This is the primary path.
- **● Live** — streams frames to the laptop's live feed + pinout overlay.

The laptop desktop console stays on plain `http://127.0.0.1:8777`. The ESP32 path is
unchanged. Identification is identical regardless of camera source.

### Configuration (no secrets in git)
Set these in the environment (or a local, gitignored `.env`):

| var | meaning | default |
|---|---|---|
| `GLASSES_TOKEN` | shared auth token (must match firmware) | `CHANGE_ME_dev_token` |
| `GLASSES_HOST` | LAN bind address (never `0.0.0.0` in prod) | `127.0.0.1` |
| `GLASSES_PORT` | server port | `8777` |
| `GLASSES_OCR_BACKEND` | `apple` / `easy` / `paddle` / `mock` | `apple` |
| `GLASSES_TTS_READOUT` | optional Kokoro readout | `0` |
| `GLASSES_DATASHEET_QA` | optional grounded Qwen Q&A | `0` |
| `GLASSES_HTTPS_PORT` | phone HTTPS listener (needs `make cert`) | `8443` |
| `GLASSES_HTTPS_HOST` | HTTPS bind (LAN so the phone can reach it) | `0.0.0.0` |
| `GLASSES_DEVICE_STREAM_URL` | glasses MJPEG feed to pull (e.g. `http://192.168.4.20:81/stream`) | unset |

---

## Flashing the glasses (firmware)

Full detail in [`firmware/README.md`](firmware/README.md). Short version:

```bash
cd firmware
#   1. fill EVERY pin in main/board_config.h from the Rev A.2/B schematic
#   2. add the OV5640 AF firmware blob to main/af_firmware.inc
idf.py set-target esp32s3
idf.py menuconfig          # Glasses configuration: WiFi SSID/pass, server URL, token
idf.py build
idf.py -p /dev/tty.usbmodemXXXX flash monitor | tee ../eval/datasets/device_run.log
```

Wi-Fi credentials, server URL, and the shared token come from `menuconfig`
(Kconfig) — never hardcoded. The serial log format is parsed by the eval scripts.

---

## Building the catalog

```bash
# 1. edit catalog/parts.yaml  — one entry per orderable number, with top_markings
# 2. edit catalog/manifest.yaml — one entry per number: url + cache_path + reviewed
python -m catalog.fetch                 # ONE-TIME online download; records sha256
#    then open each PDF, confirm the revision, and set reviewed: true
python -m catalog.validate --strict     # fails on unmapped parts / hash / unreviewed
```

Runtime never touches the network. The validator guarantees every acceptable part
is mapped and every cached PDF hash matches.

---

## Calibrate → freeze → trial

```bash
# calibration set is DISJOINT from the trial set
python -m eval.calibrate --dataset eval/datasets/calibration.yaml
python -m eval.freeze                    # stamps version + content hash
python -m eval.blinded_trial --dataset eval/datasets/trials.yaml --seed 7
```

`eval.blinded_trial` **refuses to run on an unfrozen config**. It randomizes a
≥20-part ×≥5-trial (100) set, never shows the operator the ground truth during
candidate selection, and reports automatic-only / user-assisted / overall
accuracy, incorrect finals, recaptures, errors, and a confusion matrix. Pass =
≥80 correct and ≤2 incorrect (V2).

Dataset YAML (`eval/datasets/*.yaml`):
```yaml
trials:
  - {image: datasets/trial/ne555_01.jpg, truth: NE555P}
```

### Verifying device requirements + latency
```bash
python -m eval.parse_device_logs eval/datasets/device_run.log   # C1/C2/E1/E2/E3/I1/I3 table
python -m eval.latency           eval/datasets/device_run.log   # U3: median + p95
```

### Testing the laptop side without hardware
```bash
make server                                   # terminal 1
python -m tools.fake_device --token $GLASSES_TOKEN \
    --image eval/datasets/samples/x.jpg --scenario normal        # or duplicate|retry|…
python -m tools.fuzzer --token $GLASSES_TOKEN --image …          # V1 boundary probe
```

---

## Privacy / wipe
```bash
python -m server.privacy status
python -m server.privacy wipe     # delete all captured images + logs
```

## Preflight
```bash
python -m tools.doctor            # deps / OCR / catalog / thresholds / datasheets / UI
```

## Troubleshooting
See [`docs/runbook.md`](docs/runbook.md) for the deep-dive (OCR backend selection,
WS handshake notes, Metal/PSRAM gotchas, flashing issues, demo checklist).

## Requirement coverage
[`docs/traceability.md`](docs/traceability.md) maps every ID (C1–C4, I1–I3, E1–E3,
V1–V3, D1–D3, U1–U3, P6) to code + test + evidence. The wire contract is
[`docs/protocol.md`](docs/protocol.md).
