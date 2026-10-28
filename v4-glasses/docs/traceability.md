# Requirements Traceability

Every requirement ID → the code that implements it, the test/tool that checks it,
and the evidence output you can show. IDs: C1–C4, I1–I3, E1–E3, V1–V3, D1–D3,
U1–U3, plus P6 (battery).

| ID | Requirement | Code | Test / Tool | Evidence |
|----|-------------|------|-------------|----------|
| **C1** | Reject JPEG > 2 MB | `firmware/main/camera.c` (`camera_capture`); `server/receiver.py` (`validate_capture` step 5) | `tools/fuzzer.py` (`jpeg_over_limit`); `tests/test_receiver.py::test_oversize_jpeg`; `eval/parse_device_logs.py` (C1) | fuzzer table row `jpeg_over_limit → 413 E_TOO_LARGE`; device-log C1 PASS |
| **C2** | Autofocus ≤ 1.5 s @ 100–110 mm | `firmware/main/autofocus.c` (`af_focus_once`) | `eval/parse_device_logs.py` (C2) | `FOCUS_DONE dur_ms` ≤ 1500 → C2 PASS |
| **C3** | ⚠️ *definition not in the brief* — assumed capture resolution/quality (QSXGA 2592×1944 JPEG) | `firmware/main/camera.c` (`FRAMESIZE_QSXGA`), `board_config.h` | — | **TODO: confirm C3 from design doc** |
| **C4** | Worklights on ONLY during focus + exposure | `firmware/main/worklights.c`; driven in `main.c` around focus/capture | logic analyzer on `PIN_DBG_CAPTURE_STATE`; `WORKLIGHT state=on/off` log | worklight on/off brackets focus+capture only |
| **I1** | One capture per press; release required; 250 ms debounce | `firmware/main/button.c` (`button_pressed`) | `eval/parse_device_logs.py` (I1) | I1 PASS (no press within debounce) |
| **I2** | Presses ignored while a transaction is active | `firmware/main/button.c` (`s_busy`); `main.c` (`button_set_busy`) | device log `BTN_IGNORED reason=busy` | ignored-press log lines |
| **I3** | Status LED reflects within 250 ms + debug transition GPIO | `firmware/main/status_led.c` | `eval/parse_device_logs.py` (I3); logic analyzer on `PIN_DBG_STATE_TRANSITION` | `STATE dur_ms` ≤ 250 → I3 PASS |
| **E1** | Unique request_id; buffer until matching ACK | `firmware/main/uploader.c`; `server/receiver.py` (dedup via `StateStore.start`) | `tools/fake_device.py`; `tests/test_e2e_simulator.py::test_e2e_normal_accept`; `tests/test_receiver.py::test_duplicate_request_id_not_recreated` | e2e accept with `acked=True`; parser E1 PASS |
| **E2** | 5 s timeout, max 2 retries, then visible error | `firmware/main/uploader.c` (`uploader_send`) | `tools/fake_device.py --scenario retry`; `tests/test_e2e_simulator.py::test_e2e_retry_same_id_deduped`; parser (E2) | `attempts=3` then `UPLOAD_FAIL`; e2e retry deduped |
| **E3** | Ignore ACKs with wrong request_id | `firmware/main/uploader.c` (`_ack_matches`/`attempt`) | `tests/test_e2e_simulator.py::test_e2e_wrong_header_id_rejected`; parser (E3) | `ACK_IGNORED got=…`; E3 PASS |
| **V1** | Validate id/sha256/bytes/size/decode before OCR; dedup | `server/receiver.py` | `tests/test_receiver.py` (21 cases); `tools/fuzzer.py` | fuzzer 14/14; `test_malformed_never_reaches_store` |
| **V2** | Blinded trial ≥ 80 correct, ≤ 2 incorrect | `eval/blinded_trial.py`, `eval/report.py` | `tests/test_eval.py::test_report_*`; `eval/blinded_trial.py` | `eval/trial_report.json` + VERDICT line |
| **V3** | Auto-accept iff score AND margin thresholds | `server/resolver.py` (`resolve`), `server/thresholds.py` | `tests/test_resolver.py` (accept/candidate/near-miss) | `test_high_score_but_low_margin_is_candidate_not_accept` |
| **D1** | Each orderable number its own manifest entry; all mapped | `catalog/manifest.yaml`, `catalog/validate.py` | `tests/test_manifest_validator.py` | `catalog.validate` OK; unmapped→FAIL |
| **D2** | Runtime offline; reviewed:true gate; hashes recorded | `catalog/fetch.py` (only net component), `catalog/validate.py --strict`, `server/privacy.py` | `tests/test_manifest_validator.py::test_correct_hash_passes_strict` | strict validate; no net imports in `server/` runtime |
| **D3** | Page citations; abs-max vs recommended-op separate; unsupported → unavailable | `catalog/page_index.py`; `server/datasheet_qa.py` | `tests/test_page_index.py` | `search` returns cited page; `answer` → `available:false` |
| **U1** | Accepted/candidate views w/ full details + open PDF at cited page | `ui/web/index.html`; `server/catalog_api.py`; `server/app.py` (`/pdf`) | manual; `tests/test_e2e_simulator.py` (accepted record shape) | accepted view screenshot; `/pdf?...#page=N` |
| **U2** | Recapture reason (blur/glare/off_center/too_far/unreadable) | `server/resolver.py` (`pick_recapture_reason`); `ui/web/index.html` | `tests/test_resolver.py` (blur/glare/too_far/off_center/unreadable) | recapture view with reason + message |
| **U3** | Latency report; user decision time logged separately | `eval/latency.py`; `server/state.py` (`user_decision_ms`); `ui/web/index.html` | `tests/test_eval.py::test_latency_*`; `tests/test_state_machine.py::test_user_decision_recorded_separately` | latency report (median/p95); decision ms in record |
| **P6** | Battery ADC, 2-point NVS calibration | `firmware/main/battery.c` | bench measurement; `BATT mv=…` log | measured vs multimeter; `battery_mv` in result |

## Open TODOs tracked here
1. **C3** — confirm the exact definition from the ECE 445 design doc; update the row + `camera.c`.
2. **All firmware pins** — `firmware/main/board_config.h` (schematic Rev A.2/B).
3. **OV5640 AF firmware blob** — `firmware/main/af_firmware.inc`.
4. **Battery ADC channel map** — `battery.c` `PIN_BATT_ADC` → `adc_channel_t` for the S3.
5. **Frozen threshold values** — produced by `eval/calibrate.py` on your calibration set.
6. **Real catalog + datasheets** — ≥20 parts in `catalog/parts.yaml` + fetched/reviewed PDFs.
