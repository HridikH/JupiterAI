#ifndef GLASSES_STREAM_H
#define GLASSES_STREAM_H

#include "esp_err.h"

/* Start an on-device MJPEG server (http://<device>:81/stream) so the laptop can
 * show a LIVE feed (option B). Runs continuous esp32-camera captures and pushes
 * multipart/x-mixed-replace JPEG frames. Independent of the button-triggered
 * full-res capture path — identification still uses POST /capture. Call after
 * camera_init(). Disable via menuconfig if not using the live feed. */
esp_err_t stream_start(void);
void stream_stop(void);

#endif /* GLASSES_STREAM_H */
