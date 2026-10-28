/*
 * battery.c — battery voltage via a 100k/100k divider on a calibrated ADC (P6).
 *
 * Uses the ESP-IDF ADC oneshot + curve-fitting calibration (eFuse). On top of
 * that it applies a board-level TWO-POINT calibration (raw->mv) stored in NVS so
 * divider tolerance and ADC offset are corrected per unit. A configurable
 * settling delay precedes each sample.
 */
#include "battery.h"
#include "board_config.h"
#include "logging.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_log.h"
#include "rom/ets_sys.h"
#include "nvs_flash.h"
#include "nvs.h"

static const char *TAG = "batt";
static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali;
static bool s_cali_ok = false;
static adc_channel_t s_chan;

/* two-point calibration: mv = p0_mv + (raw - p0_raw) * (p1_mv - p0_mv)/(p1_raw - p0_raw) */
static int p0_raw = BATT_CAL_DEFAULT_P0_RAW, p0_mv = BATT_CAL_DEFAULT_P0_MV;
static int p1_raw = BATT_CAL_DEFAULT_P1_RAW, p1_mv = BATT_CAL_DEFAULT_P1_MV;

static void load_cal_from_nvs(void)
{
    nvs_handle_t h;
    if (nvs_open("batt", NVS_READONLY, &h) != ESP_OK) return;
    int32_t v;
    if (nvs_get_i32(h, "p0_raw", &v) == ESP_OK) p0_raw = v;
    if (nvs_get_i32(h, "p0_mv",  &v) == ESP_OK) p0_mv  = v;
    if (nvs_get_i32(h, "p1_raw", &v) == ESP_OK) p1_raw = v;
    if (nvs_get_i32(h, "p1_mv",  &v) == ESP_OK) p1_mv  = v;
    nvs_close(h);
}

esp_err_t battery_set_cal_point(int index, int raw, int mv)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open("batt", NVS_READWRITE, &h);
    if (err != ESP_OK) return err;
    const char *rk = index == 0 ? "p0_raw" : "p1_raw";
    const char *mk = index == 0 ? "p0_mv"  : "p1_mv";
    nvs_set_i32(h, rk, raw);
    nvs_set_i32(h, mk, mv);
    err = nvs_commit(h);
    nvs_close(h);
    load_cal_from_nvs();
    return err;
}

esp_err_t battery_init(void)
{
    /* NOTE (TODO): map PIN_BATT_ADC to its adc_channel_t for the S3 once the pin
     * is filled in board_config.h. Using ADC_CHANNEL_0 as a placeholder. */
    s_chan = ADC_CHANNEL_0;   /* TODO: derive from PIN_BATT_ADC */

    adc_oneshot_unit_init_cfg_t unit = { .unit_id = ADC_UNIT_1 };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&unit, &s_adc));
    adc_oneshot_chan_cfg_t ch = { .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, s_chan, &ch));

    adc_cali_curve_fitting_config_t cali = {
        .unit_id = ADC_UNIT_1, .chan = s_chan,
        .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    s_cali_ok = (adc_cali_create_scheme_curve_fitting(&cali, &s_cali) == ESP_OK);

    load_cal_from_nvs();
    ESP_LOGI(TAG, "battery init (cali=%d, 2pt p0=%d/%dmV p1=%d/%dmV)",
             s_cali_ok, p0_raw, p0_mv, p1_raw, p1_mv);
    return ESP_OK;
}

int battery_read_mv(void)
{
    ets_delay_us(BATT_ADC_SETTLE_US);        /* configurable settling delay */
    long acc = 0;
    for (int i = 0; i < BATT_SAMPLES; i++) {
        int raw = 0;
        adc_oneshot_read(s_adc, s_chan, &raw);
        acc += raw;
    }
    int raw = (int)(acc / BATT_SAMPLES);

    int mv;
    if (p1_raw != p0_raw) {
        /* two-point board calibration takes precedence (already includes divider) */
        mv = p0_mv + (int)((long)(raw - p0_raw) * (p1_mv - p0_mv) / (p1_raw - p0_raw));
    } else if (s_cali_ok) {
        int adc_mv = 0;
        adc_cali_raw_to_voltage(s_cali, raw, &adc_mv);
        mv = adc_mv * BATT_DIVIDER_NUM / BATT_DIVIDER_DEN;
    } else {
        mv = raw;   /* uncalibrated fallback */
    }
    EVT("BATT", "mv=%d", mv);
    return mv;
}
