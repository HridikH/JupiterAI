#ifndef GLASSES_AUTOFOCUS_H
#define GLASSES_AUTOFOCUS_H

#include "esp_err.h"
#include <stdbool.h>

/* Load the OV5640 autofocus firmware over SCCB. Call once after camera_init().
 * Returns ESP_OK if the AF MCU reports ready. */
esp_err_t af_load_firmware(void);

/* Trigger a single autofocus and poll for completion.
 * Logs FOCUS_START / FOCUS_DONE with dur_ms (C2 target: <= 1500 ms @ 100-110 mm).
 * Returns true if focus completed before FOCUS_DEADLINE_MS. */
bool af_focus_once(const char *req_id);

#endif /* GLASSES_AUTOFOCUS_H */
