# Glasses firmware (ESP-IDF, ESP32-S3-WROOM-1-N8R8)

Captures an autofocus JPEG of a component on button press and uploads it to the
laptop over 2.4 GHz Wi-Fi, per [`../docs/protocol.md`](../docs/protocol.md).

## Prerequisites
- ESP-IDF **5.1+** installed and exported (`. $IDF_PATH/export.sh`).
- The Waveshare OV5640 Camera Board (C) wired per the Rev A.2/Rev B schematic.

## 1. Fill in the pins (REQUIRED)
Open [`main/board_config.h`](main/board_config.h) and set **every** pin from the
schematic. The build warns until you remove the `BOARD_CONFIG_UNVERIFIED` guard.
Do not guess pins.

## 2. Add the OV5640 autofocus firmware blob
Autofocus needs OmniVision's AF firmware array. Place it in
`main/af_firmware.inc` as:
```c
static const uint8_t ov5640_af_fw[] = { /* official bytes */ };
```
Without it, captures are fixed-focus (a warning is logged).

## 3. Configure Wi-Fi, server, token (no hardcoding)
```bash
idf.py set-target esp32s3
idf.py menuconfig      # → "Glasses configuration": SSID, password, server URL, shared token
```
The shared token MUST match `GLASSES_TOKEN` on the server.

## 4. Build / flash / monitor
```bash
idf.py build
idf.py -p /dev/tty.usbmodemXXXX flash monitor
```

## Serial log format
One line per event: `EVT <uptime_ms> <TAG> key=val ...` — see
[`main/logging.h`](main/logging.h). `../eval/parse_device_logs.py` parses these to
produce the C1/C2/E1/E2/E3/I1/I3 pass–fail table. Capture a run with:
```bash
idf.py -p <port> monitor | tee ../eval/datasets/device_run.log
```

## Requirement coverage
| ID | Where |
|---|---|
| C1 | `camera.c` (reject > 2 MB) |
| C2 | `autofocus.c` (focus timestamps, ≤ 1.5 s) |
| C4 | `worklights.c` (on only during focus+exposure) |
| I1/I2 | `button.c` (debounce, release-gate, busy-ignore) |
| I3 | `status_led.c` (≤ 250 ms + debug transition GPIO) |
| E1/E2/E3 | `uploader.c` (buffer-until-ACK, timeout/retries, ignore wrong ACK) |
| P6 | `battery.c` (divider + eFuse + 2-point NVS cal) |

## Known TODOs
- All `board_config.h` pins (schematic).
- `main/af_firmware.inc` (OV5640 AF blob).
- `battery.c`: map `PIN_BATT_ADC` → `adc_channel_t` for the S3.
