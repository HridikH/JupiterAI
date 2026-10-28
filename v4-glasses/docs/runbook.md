# Runbook (operations + troubleshooting)

Deep-dive companion to the top-level [`README.md`](../README.md). The README has
the happy-path commands; this covers selection, gotchas, and the demo checklist.

## OCR backend selection
- **`apple`** (default, macOS): Apple Vision via `pyobjc`. Fastest, no model
  download, best on printed markings. Needs `pyobjc-framework-Vision` +
  `pyobjc-framework-Quartz` in `.venv`.
- **`easy`**: EasyOCR (cross-platform, pulls torch — large). `pip install easyocr`.
- **`paddle`**: PaddleOCR alternative. `pip install paddleocr`.
- **`mock`**: deterministic canned reads — for tests/CI only.

Set via `GLASSES_OCR_BACKEND`. The resolver/thresholds are backend-agnostic; if you
switch backends, **re-calibrate and re-freeze** (score distributions differ).

## WebSocket / server notes (inherited from JarvisV3)
- The server forces `ws="wsproto"` — uvicorn's default websockets backend returned
  403 on macOS in the parent repo. Keep it.
- The UI is a *control surface*: it fetches the canonical `/result/{id}` on every
  WS nudge rather than trusting the event payload, so a missed event self-heals.

## Thresholds / freezing
- `server/thresholds.json` is gitignored (it's generated). An example lives in
  `server/thresholds.py::DEFAULTS`; `eval.freeze` writes/freezes the file.
- Every result logs `config_version` + `config_hash`. If a trial's numbers look
  off, check the hash in `eval/trial_report.json` against `eval.freeze` output.
- `eval.blinded_trial` refuses an unfrozen or hash-mismatched file. This is
  intentional — do not bypass it for a graded run.

## Firmware gotchas
- **PSRAM**: full-res QSXGA JPEG frame buffers require octal PSRAM enabled
  (`sdkconfig.defaults` sets it). Metal/driver alloc failures → check PSRAM init.
- **board_config.h**: the `BOARD_CONFIG_UNVERIFIED` guard warns until every pin is
  filled. A wrong DVP data pin corrupts frames silently — verify against the
  schematic, not guesswork.
- **Autofocus**: without `af_firmware.inc` the device runs fixed-focus and logs a
  warning; C2 will show `note=no_af_fw` focuses (excluded from the C2 check).
- **Flashing**: use the correct `/dev/tty.usbmodem*`; hold BOOT if the S3 won't
  enter download mode.

## Demo checklist (offline)
1. `python -m tools.doctor` → all OK (or only expected WARNs).
2. `python -m catalog.validate --strict` → OK (PDFs cached + reviewed).
3. Thresholds frozen (`doctor` shows `frozen …`).
4. Laptop on the glasses' 2.4 GHz LAN; `GLASSES_HOST` set to the LAN IP; firmware
   `GLASSES_SERVER_URL` points at it; tokens match.
5. `make run` → console window shows **live**.
6. Button press on the device → accepted / candidate / recapture on screen.
7. Verify no network egress (pull the laptop off the internet — it still works).

## Resetting state
```bash
python -m server.privacy wipe     # captures + logs
rm -f server/thresholds.json      # force re-calibrate/freeze
```

## Known TODOs
Tracked in [`PLAN.md`](../PLAN.md) §7 and [`traceability.md`](traceability.md):
C3 definition, firmware pins, OV5640 AF blob, battery ADC channel map, real
catalog data, frozen threshold values.
