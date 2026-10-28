#ifndef GLASSES_CAMERA_H
#define GLASSES_CAMERA_H

#include "esp_err.h"
#include "esp_camera.h"

/* Initialize the OV5640 over 8-bit DVP: JPEG, 2592x1944 (QSXGA), frame buffer in
 * PSRAM. Returns ESP_OK on success. */
esp_err_t camera_init(void);

/* Capture one JPEG frame. Caller MUST call camera_return() when done with it.
 * Returns NULL on capture failure. Rejects frames over MAX_JPEG_BYTES (C1):
 * logs FRAME_REJECT, returns NULL with *rejected set true. */
camera_fb_t *camera_capture(const char *req_id, bool *rejected);

void camera_return(camera_fb_t *fb);

/* Access to the SCCB/sensor handle for the autofocus module. */
sensor_t *camera_sensor(void);

#endif /* GLASSES_CAMERA_H */
