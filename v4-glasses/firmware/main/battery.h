#ifndef GLASSES_BATTERY_H
#define GLASSES_BATTERY_H

#include "esp_err.h"
#include <stdint.h>

/* Init ADC (eFuse calibrated) + load two-point calibration constants from NVS. */
esp_err_t battery_init(void);

/* Read battery voltage in millivolts: average BATT_SAMPLES reads after a
 * settling delay, apply the 100k/100k divider and the two-point NVS calibration
 * (P6). Logs BATT mv=<n>. */
int battery_read_mv(void);

/* Store two-point calibration (raw_adc, known_mv) pair index 0 or 1 into NVS. */
esp_err_t battery_set_cal_point(int index, int raw, int mv);

#endif /* GLASSES_BATTERY_H */
