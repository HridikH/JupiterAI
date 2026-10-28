# Wire Protocol — Glasses ↔ Laptop

**Version:** `glasses-proto/1`
**Status:** authoritative. Firmware (`firmware/`) and server (`server/`) both implement
against THIS document. `server/protocol.py` mirrors these constants verbatim; if they
disagree, this file wins and the code is a bug.

The protocol is deliberately boring: one upload endpoint, one ACK endpoint, plain HTTP
over the local 2.4 GHz LAN. No TLS (local-only, demo-offline), but every request carries a
**shared token** and the server **binds to the LAN interface only**. There is no cloud and
no internet dependency at runtime.

---

## 1. Transport & addressing

- **Transport:** HTTP/1.1 over TCP, cleartext, on the local Wi-Fi LAN.
- **Server bind:** the configured LAN IP only (never `0.0.0.0` in production config, never a
  public interface). Port from `server/config.py` (`GLASSES_PORT`, default `8777`).
- **Client:** the ESP32-S3 is the HTTP *client*; the laptop is the HTTP *server*. The
  laptop never initiates a connection to the glasses.
- **Auth:** every request MUST include header `X-Glasses-Token: <shared token>`. The token
  is set in firmware via Kconfig/menuconfig and in the server via config/env. Requests with
  a missing or wrong token get `401` and are dropped before any processing.

## 2. Size limits (hard)

| Thing | Limit | Enforced by |
|---|---|---|
| JPEG body | **≤ 2 MB** (`2_097_152` bytes) | firmware pre-upload (C1) **and** server receiver (V1) |
| Metadata JSON | **≤ 16 kB** (`16_384` bytes) | server receiver (V1) |
| `request_id` string | 8–64 chars, `[A-Za-z0-9_-]` | server receiver (V1) |

A body over the JPEG limit is rejected with `413` and error code `E_TOO_LARGE`. The
firmware must also refuse to send such a frame (re-capture), so this is defense in depth.

## 3. Endpoints

### 3.1 `POST /capture` — upload one JPEG + metadata

`multipart/form-data` with exactly two parts:

| Part name | Content-Type | Contents |
|---|---|---|
| `meta` | `application/json` | the metadata object (§4), ≤ 16 kB |
| `image` | `image/jpeg` | the JPEG bytes, ≤ 2 MB |

Required headers:

```
X-Glasses-Token: <shared token>
X-Request-Id:    <request_id>     # MUST equal meta.request_id (cross-check; mismatch → E_BAD_REQUEST)
```

**Server validation order (V1 — all BEFORE any OCR/identification):**

1. Token present & correct → else `401 E_UNAUTHORIZED`.
2. `meta` parses as JSON and ≤ 16 kB → else `400 E_BAD_META`.
3. `X-Request-Id` header present, well-formed, equals `meta.request_id` → else `400 E_BAD_REQUEST`.
4. **Idempotency:** if `request_id` already seen, do NOT start a second identification;
   return the current status for that id (`200`, `duplicate: true`). (E1/E2 retries are safe.)
5. `image` present, ≤ 2 MB → else `413 E_TOO_LARGE`.
6. `byte_count` in meta equals actual image length → else `400 E_SIZE_MISMATCH`.
7. `sha256` in meta equals SHA-256 of the image bytes → else `400 E_HASH_MISMATCH`.
8. Image decodes as a valid JPEG (Pillow) → else `422 E_DECODE_FAILED`.

Only after all 8 pass does the request enter the pipeline (`received → processing`).
A failure at any step returns the error code and **can never reach an accepted state.**

**Success response (`202 Accepted`):**

```json
{
  "ok": true,
  "request_id": "a1b2c3d4...",
  "status": "processing",
  "duplicate": false,
  "ack": { ... see §5 ACK ... }
}
```

The response body embeds the ACK so a single round-trip both accepts the upload and
confirms the `request_id` the firmware must match (buffer-until-ACK, E1).

**Error response (any non-2xx):**

```json
{ "ok": false, "request_id": "...", "error": "E_HASH_MISMATCH",
  "detail": "human-readable reason" }
```

### 3.2 `GET /result/{request_id}` — poll the outcome (optional for firmware)

Returns the current state machine record (§6). The firmware does not need this (it only
needs the ACK); it exists for the simulator, the UI, and debugging.

```json
{ "request_id": "...", "status": "accepted|candidate_selection|recapture|error|processing|received",
  "timestamps": { "received": 0.0, "processing": 0.012, "accepted": 1.84 },
  "config_version": "thresholds/2026-10-28", "config_hash": "sha256:...",
  "result": { ... outcome-specific, see §7 ... } }
```

### 3.3 `GET /healthz` — liveness (no token required)

`{"ok": true, "service": "glasses-server", "proto": "glasses-proto/1"}`

## 4. Metadata object (`meta` part)

```json
{
  "request_id": "a1b2c3d4e5f6",   // 8–64 chars [A-Za-z0-9_-], unique per capture
  "proto": "glasses-proto/1",
  "width": 2592,
  "height": 1944,
  "byte_count": 1048576,          // MUST equal actual image length
  "sha256": "hex...",             // lowercase hex SHA-256 of the image bytes
  "battery_mv": 3920,             // battery voltage in millivolts (P6)
  "firmware": "glasses-fw/0.1",
  "capture_ms": 1430,             // optional: focus-start→focus-complete (C2 evidence)
  "attempt": 0                    // 0 = first try, 1/2 = retries (E2)
}
```

Unknown extra fields are ignored (forward-compatible). Missing required fields →
`400 E_BAD_META`.

## 5. ACK format (idempotency anchor, E1/E3)

The ACK is the object returned in the `/capture` response under `ack`:

```json
{ "request_id": "a1b2c3d4e5f6", "received": true, "proto": "glasses-proto/1", "server_time": 1730000000.0 }
```

Firmware rules:
- Keep the JPEG buffered until it receives an ACK whose `request_id` **exactly matches** the
  one it sent (E1).
- **Ignore any ACK whose `request_id` differs** from the in-flight request (E3). A mismatched
  ACK is treated as if no ACK arrived.
- Per attempt: **5 s timeout**; on timeout or non-2xx, retry; **max 2 retries** (3 attempts
  total), then surface a visible error LED and give up (E2).
- `attempt` increments on each retry; `request_id` stays the **same** across retries of one
  capture so the server dedupes (idempotency, §3.1 step 4).

## 6. Server state machine (per `request_id`)

```
received ──▶ processing ──▶ accepted
                        ├──▶ candidate_selection
                        ├──▶ recapture        (reason: blur|glare|off_center|too_far|unreadable)
                        └──▶ error            (code: E_*)
```

Every transition stamps a monotonic timestamp. The record also logs `config_version` and
`config_hash` of the frozen thresholds used (so every result is reproducible). States are
terminal except `received`/`processing`; a duplicate upload returns the existing record.

## 7. Outcome payloads (`result` field)

- **accepted** — see §U1: `{ part_number, manufacturer, package, score, margin,
  ocr_text, crop_ref, image_ref, datasheet: { path, cite_page } }`
- **candidate_selection** — `{ candidates: [ {part_number, manufacturer, package, score,
  datasheet:{path, cite_page}}, ... ] }` (nothing accepted until the user picks).
- **recapture** — `{ reason: "blur|glare|off_center|too_far|unreadable", message: "..." }`
- **error** — `{ code: "E_*", message: "..." }`

## 8. Error code registry

| Code | HTTP | Meaning |
|---|---|---|
| `E_UNAUTHORIZED` | 401 | Missing/wrong `X-Glasses-Token` |
| `E_BAD_META` | 400 | `meta` missing, unparseable, too large, or missing required field |
| `E_BAD_REQUEST` | 400 | `X-Request-Id` missing/malformed or ≠ `meta.request_id` |
| `E_SIZE_MISMATCH` | 400 | `byte_count` ≠ actual image length |
| `E_HASH_MISMATCH` | 400 | `sha256` ≠ hash of image bytes |
| `E_TOO_LARGE` | 413 | JPEG > 2 MB or metadata > 16 kB |
| `E_DECODE_FAILED` | 422 | Image is not a decodable JPEG |
| `E_INTERNAL` | 500 | Unexpected server fault (never reaches "accepted") |

## 9. Idempotency rules (summary)

1. `request_id` is the single idempotency key.
2. First valid `/capture` for an id starts exactly one identification task.
3. Any later `/capture` with the same id returns the existing record, `duplicate: true`,
   and never starts a second task.
4. The firmware matches ACKs by `request_id` and ignores all others.
5. Retries reuse the same `request_id`; only `attempt` changes.
