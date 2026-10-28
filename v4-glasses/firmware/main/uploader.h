#ifndef GLASSES_UPLOADER_H
#define GLASSES_UPLOADER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum { UP_OK = 0, UP_FAIL } upload_result_t;

/* Terminal outcome as reported by the laptop (GET /result/{id}). candidate_selection
 * is resolved on the laptop screen, so the device treats it as "still processing". */
typedef enum {
    OUTCOME_UNKNOWN = 0, OUTCOME_PROCESSING, OUTCOME_ACCEPTED,
    OUTCOME_CANDIDATE, OUTCOME_RECAPTURE, OUTCOME_ERROR
} outcome_t;

/* Poll GET /result/{id} until a terminal status or timeout. Best-effort; the
 * laptop screen is the authoritative display. */
outcome_t uploader_poll_outcome(const char *request_id, int timeout_ms);

/* Generate a unique request id (hex) into buf (>= 17 bytes). */
void uploader_new_request_id(char *buf, size_t buflen);

/* Upload one JPEG + metadata to the server (docs/protocol.md §3.1).
 *   - sends meta{request_id,width,height,byte_count,battery_mv,sha256,...} + image
 *   - keeps the caller's buffer alive until a MATCHING ack arrives (E1)
 *   - ignores an ack whose request_id differs (E3)
 *   - per-attempt 5 s timeout, max 2 retries (E2); logs UPLOAD_* and ACK_*.
 * The image buffer is owned by the caller (camera fb) and must stay valid for the
 * whole call. Returns UP_OK only on a matching ack. */
upload_result_t uploader_send(const char *request_id,
                              const uint8_t *jpeg, size_t jpeg_len,
                              int width, int height, int battery_mv);

#endif /* GLASSES_UPLOADER_H */
