/*
 * autofocus.c — OV5640 contrast autofocus sequence over SCCB.
 *
 * Sequence (per OmniVision OV5640 AF application note):
 *   1. Hold the AF MCU in reset, download the AF firmware blob to 0x8000..,
 *      release reset, poll firmware status reg 0x3029 until 0x70 (S_IDLE / ready).
 *   2. Trigger single focus: write 0x03 to command reg 0x3022.
 *   3. Poll 0x3029 until 0x10 (S_FOCUSED) or timeout.
 *
 * NOTE (TODO): the AF firmware blob `ov5640_af_fw[]` is ~4.8 kB of OmniVision-
 * provided bytes. It is NOT reproduced here (licensing + size). Drop the official
 * array into af_firmware.inc and remove the #error below. Everything else — the
 * load/trigger/poll state machine and the C2 timestamps — is implemented.
 */
#include "autofocus.h"
#include "board_config.h"
#include "camera.h"
#include "logging.h"
#include "esp_camera.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "af";

/* OV5640 AF registers */
#define OV5640_CMD_MAIN   0x3022
#define OV5640_CMD_ACK    0x3023
#define OV5640_FW_STATUS  0x3029
#define OV5640_FW_RST     0x3000  /* MCU reset bit field (see app note) */

/* Firmware blob — supply the official bytes. */
#if __has_include("af_firmware.inc")
#  include "af_firmware.inc"       /* defines: static const uint8_t ov5640_af_fw[]; */
#else
#  warning "af_firmware.inc missing — autofocus disabled. Add the OV5640 AF firmware blob."
static const uint8_t ov5640_af_fw[] = {0};
#  define AF_FW_PLACEHOLDER 1
#endif

static int reg_write(uint16_t reg, uint8_t val)
{
    sensor_t *s = camera_sensor();
    if (!s || !s->set_reg) return -1;
    return s->set_reg(s, reg, 0xFF, val);
}

static int reg_read(uint16_t reg)
{
    sensor_t *s = camera_sensor();
    if (!s || !s->get_reg) return -1;
    return s->get_reg(s, reg, 0xFF);
}

esp_err_t af_load_firmware(void)
{
#ifdef AF_FW_PLACEHOLDER
    ESP_LOGW(TAG, "AF firmware placeholder — focus is a no-op until af_firmware.inc is added");
    return ESP_ERR_NOT_SUPPORTED;
#else
    /* 1. reset MCU, program memory, release */
    reg_write(OV5640_FW_RST, 0x20);
    for (size_t i = 0; i < sizeof(ov5640_af_fw); i++) {
        if (camera_sensor()->set_reg(camera_sensor(), 0x8000 + i, 0xFF, ov5640_af_fw[i]) != 0) {
            ESP_LOGE(TAG, "AF fw write failed at %u", (unsigned)i);
            return ESP_FAIL;
        }
    }
    reg_write(OV5640_CMD_MAIN, 0x00);
    reg_write(OV5640_CMD_ACK, 0x00);
    reg_write(OV5640_FW_STATUS, 0x7F);
    reg_write(OV5640_FW_RST, 0x00);  /* release MCU */

    /* poll until S_IDLE (0x70) */
    for (int i = 0; i < 100; i++) {
        if (reg_read(OV5640_FW_STATUS) == 0x70) {
            ESP_LOGI(TAG, "AF firmware ready");
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
    ESP_LOGE(TAG, "AF firmware did not become ready");
    return ESP_ERR_TIMEOUT;
#endif
}

bool af_focus_once(const char *req_id)
{
    int64_t t0 = esp_timer_get_time();
    EVT("FOCUS_START", "req=%s", req_id);

#ifdef AF_FW_PLACEHOLDER
    /* No firmware: emit a FOCUS_DONE so timing plumbing is exercised, but flag it. */
    EVT("FOCUS_DONE", "req=%s dur_ms=0 note=no_af_fw", req_id);
    return false;
#else
    reg_write(OV5640_CMD_ACK, 0x01);
    reg_write(OV5640_CMD_MAIN, 0x03);   /* single focus */

    while ((esp_timer_get_time() - t0) / 1000 < FOCUS_DEADLINE_MS) {
        if (reg_read(OV5640_CMD_ACK) == 0x00) {   /* command consumed */
            int st = reg_read(OV5640_FW_STATUS);
            if (st == 0x10) {                      /* S_FOCUSED */
                int dur = (int)((esp_timer_get_time() - t0) / 1000);
                EVT("FOCUS_DONE", "req=%s dur_ms=%d", req_id, dur);
                return dur <= FOCUS_DEADLINE_MS;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    int dur = (int)((esp_timer_get_time() - t0) / 1000);
    EVT("FOCUS_DONE", "req=%s dur_ms=%d note=timeout", req_id, dur);
    return false;
#endif
}
