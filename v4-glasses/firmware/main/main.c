/*
 * main.c — capture transaction state machine.
 *
 * READY -> (button) -> PROCESSING -> focus+expose(worklights) -> capture ->
 *          upload (buffer-until-ACK) -> poll laptop outcome -> ACCEPTED |
 *          RECAPTURE | ERROR -> back to READY.
 *
 * The laptop screen is the authoritative display; the device LEDs are coarse
 * status. candidate_selection is resolved on the laptop, so the device stays in
 * PROCESSING for it (see uploader_poll_outcome). Worklights are on ONLY during
 * focus+exposure (C4). One capture per button press, release-gated (I1), presses
 * ignored while a transaction runs (I2).
 */
#include "board_config.h"
#include "logging.h"
#include "camera.h"
#include "autofocus.h"
#include "worklights.h"
#include "button.h"
#include "status_led.h"
#include "battery.h"
#include "net.h"
#include "uploader.h"

#include "esp_log.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_camera.h"

static const char *TAG = "main";

static void run_transaction(void)
{
    char req_id[17];
    uploader_new_request_id(req_id, sizeof(req_id));

    button_set_busy(true);                 /* I2: ignore presses during transaction */
    status_led_set(LED_STATE_PROCESSING);  /* I3 */

    /* focus + exposure under worklights (C4) */
    worklights_set(true);
    af_focus_once(req_id);                  /* C2 timestamps logged inside */
    bool rejected = false;
    camera_fb_t *fb = camera_capture(req_id, &rejected);   /* C1 inside */
    worklights_set(false);                  /* worklights OFF the instant capture ends (C4) */

    if (!fb) {
        status_led_set(rejected ? LED_STATE_RECAPTURE : LED_STATE_ERROR);
        vTaskDelay(pdMS_TO_TICKS(1200));
        goto done;
    }

    int batt = battery_read_mv();           /* P6 */

    upload_result_t up = uploader_send(req_id, fb->buf, fb->len, fb->width, fb->height, batt);
    camera_return(fb);

    if (up != UP_OK) {
        status_led_set(LED_STATE_ERROR);    /* E2: visible error after retries exhausted */
        vTaskDelay(pdMS_TO_TICKS(1500));
        goto done;
    }

    /* reflect the laptop's deterministic outcome */
    outcome_t out = uploader_poll_outcome(req_id, 25000);
    switch (out) {
        case OUTCOME_ACCEPTED:  status_led_set(LED_STATE_ACCEPTED);  break;
        case OUTCOME_RECAPTURE: status_led_set(LED_STATE_RECAPTURE); break;
        case OUTCOME_ERROR:     status_led_set(LED_STATE_ERROR);     break;
        default:                status_led_set(LED_STATE_PROCESSING); break; /* candidate/unknown */
    }
    vTaskDelay(pdMS_TO_TICKS(1500));

done:
    button_set_busy(false);
    status_led_set(LED_STATE_READY);
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());

    status_led_init();
    worklights_init();
    button_init();
    ESP_ERROR_CHECK(battery_init());

    ESP_ERROR_CHECK(net_connect());
    ESP_ERROR_CHECK(camera_init());
    if (af_load_firmware() != ESP_OK)
        ESP_LOGW(TAG, "autofocus unavailable (add af_firmware.inc) — captures will be fixed-focus");

    status_led_set(LED_STATE_READY);
    ESP_LOGI(TAG, "glasses ready");

    for (;;) {
        if (button_pressed()) {             /* I1: one per press, release-gated */
            run_transaction();
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
