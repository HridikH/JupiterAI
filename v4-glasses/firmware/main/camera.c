#include "camera.h"
#include "board_config.h"
#include "logging.h"
#include "esp_log.h"

static const char *TAG = "camera";

esp_err_t camera_init(void)
{
    camera_config_t cfg = {
        .pin_d0 = CAM_PIN_D0, .pin_d1 = CAM_PIN_D1, .pin_d2 = CAM_PIN_D2,
        .pin_d3 = CAM_PIN_D3, .pin_d4 = CAM_PIN_D4, .pin_d5 = CAM_PIN_D5,
        .pin_d6 = CAM_PIN_D6, .pin_d7 = CAM_PIN_D7,
        .pin_pclk = CAM_PIN_PCLK, .pin_href = CAM_PIN_HREF, .pin_vsync = CAM_PIN_VSYNC,
        .pin_xclk = CAM_PIN_XCLK,
        .pin_sccb_sda = CAM_PIN_SCCB_SDA, .pin_sccb_scl = CAM_PIN_SCCB_SCL,
        .pin_pwdn = CAM_PIN_PWDN, .pin_reset = CAM_PIN_RESET,
        .xclk_freq_hz = CAM_XCLK_FREQ_HZ,
        .ledc_timer = LEDC_TIMER_0, .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG,
        .frame_size = FRAMESIZE_QSXGA,        /* 2592x1944 */
        .jpeg_quality = 10,                    /* 0..63, lower = better/larger */
        .fb_count = 2,
        .fb_location = CAMERA_FB_IN_PSRAM,     /* full-res JPEG needs PSRAM */
        .grab_mode = CAMERA_GRAB_LATEST,
    };

    esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_camera_init failed: 0x%x (check board_config.h pins)", err);
        return err;
    }

    sensor_t *s = esp_camera_sensor_get();
    if (s) {
        /* Deterministic, fixed sensor setup — no runtime auto-tuning. */
        s->set_framesize(s, FRAMESIZE_QSXGA);
        s->set_gain_ctrl(s, 1);
        s->set_exposure_ctrl(s, 1);
        s->set_whitebal(s, 1);
    }
    ESP_LOGI(TAG, "camera initialized (OV5640 QSXGA JPEG, PSRAM fb)");
    return ESP_OK;
}

camera_fb_t *camera_capture(const char *req_id, bool *rejected)
{
    if (rejected) *rejected = false;
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        ESP_LOGE(TAG, "capture failed");
        return NULL;
    }
    if (fb->len > MAX_JPEG_BYTES) {           /* C1: reject oversized frames */
        EVT("FRAME_REJECT", "req=%s reason=oversize bytes=%u", req_id, (unsigned)fb->len);
        if (rejected) *rejected = true;
        esp_camera_fb_return(fb);
        return NULL;
    }
    EVT("CAPTURE_DONE", "req=%s bytes=%u w=%u h=%u",
        req_id, (unsigned)fb->len, fb->width, fb->height);
    return fb;
}

void camera_return(camera_fb_t *fb)
{
    if (fb) esp_camera_fb_return(fb);
}

sensor_t *camera_sensor(void)
{
    return esp_camera_sensor_get();
}
