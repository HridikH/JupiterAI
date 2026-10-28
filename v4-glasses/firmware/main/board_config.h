/*
 * board_config.h — THE single place for every pin + board constant.
 *
 * ========================= READ ME BEFORE BUILDING =========================
 * Every pin below is a PLACEHOLDER marked TODO. Fill each one from the Rev A.2 /
 * Rev B schematic. DO NOT guess — a wrong camera data pin silently corrupts
 * frames, a wrong PWDN strap can brown-out the sensor. The build intentionally
 * #errors until you remove the BOARD_CONFIG_UNVERIFIED guard at the bottom.
 * ===========================================================================
 *
 * Target: ESP32-S3-WROOM-1-N8R8 (8 MB flash, 8 MB octal PSRAM).
 * Camera: Waveshare OV5640 Camera Board (C), 8-bit DVP.
 */
#ifndef BOARD_CONFIG_H
#define BOARD_CONFIG_H

/* ---- Camera: 8-bit DVP data bus D0..D7 (TODO: fill from schematic) -------- */
#define CAM_PIN_D0      (-1)   /* TODO */
#define CAM_PIN_D1      (-1)   /* TODO */
#define CAM_PIN_D2      (-1)   /* TODO */
#define CAM_PIN_D3      (-1)   /* TODO */
#define CAM_PIN_D4      (-1)   /* TODO */
#define CAM_PIN_D5      (-1)   /* TODO */
#define CAM_PIN_D6      (-1)   /* TODO */
#define CAM_PIN_D7      (-1)   /* TODO */

/* ---- Camera: sync + clock ------------------------------------------------- */
#define CAM_PIN_PCLK    (-1)   /* TODO pixel clock */
#define CAM_PIN_HREF    (-1)   /* TODO horizontal ref */
#define CAM_PIN_VSYNC   (-1)   /* TODO vertical sync */
#define CAM_PIN_XCLK    (-1)   /* TODO master clock out to sensor */
#define CAM_XCLK_FREQ_HZ (20000000)  /* 20 MHz typical for OV5640 DVP */

/* ---- Camera: SCCB (I2C to sensor) ---------------------------------------- */
#define CAM_PIN_SCCB_SDA (-1)  /* TODO */
#define CAM_PIN_SCCB_SCL (-1)  /* TODO */

/* ---- Camera: control ------------------------------------------------------ */
#define CAM_PIN_RESET   (-1)   /* TODO (set -1 if tied to board reset) */
#define CAM_PIN_PWDN    (-1)   /* TODO power-down (active high on OV5640) */

/* ---- Worklights: two LED enables, driven ONLY during focus+exposure (C4) -- */
#define PIN_WORKLIGHT_A (-1)   /* TODO */
#define PIN_WORKLIGHT_B (-1)   /* TODO */
#define WORKLIGHT_ACTIVE_HIGH (1)

/* ---- Capture button: active-low with external/internal pull-up (I1,I2) ---- */
#define PIN_BUTTON      (-1)   /* TODO */
#define BUTTON_ACTIVE_LEVEL (0)      /* active-low */
#define BUTTON_DEBOUNCE_MS  (250)    /* debounce / lockout window (I1) */

/* ---- Status LEDs: ready/processing/accepted/recapture/error (I3) ---------- */
#define PIN_LED_READY     (-1) /* TODO */
#define PIN_LED_PROCESSING (-1)/* TODO */
#define PIN_LED_ACCEPTED  (-1) /* TODO */
#define PIN_LED_RECAPTURE (-1) /* TODO */
#define PIN_LED_ERROR     (-1) /* TODO */
#define LED_ACTIVE_HIGH   (1)
#define LED_UPDATE_DEADLINE_MS (250) /* state change must reflect within this (I3) */

/* ---- Debug GPIOs for logic-analyzer timing -------------------------------- */
#define PIN_DBG_CAPTURE_STATE (-1)   /* TODO toggles around capture (C-group) */
#define PIN_DBG_STATE_TRANSITION (-1)/* TODO pulses on every LED state change (I3) */

/* ---- Battery sense: 100k/100k divider on an ADC pin (P6) ------------------ */
#define PIN_BATT_ADC    (-1)   /* TODO ADC1 channel GPIO */
#define BATT_DIVIDER_NUM (2)   /* 100k/100k -> Vbatt = Vadc * 2 */
#define BATT_DIVIDER_DEN (1)
#define BATT_ADC_SETTLE_US (200) /* configurable settling delay before sampling */
#define BATT_SAMPLES    (16)   /* average this many reads */
/* Two-point calibration constants live in NVS (namespace "batt", keys
 * "p0_raw","p0_mv","p1_raw","p1_mv"); see battery.c. Defaults until calibrated: */
#define BATT_CAL_DEFAULT_P0_RAW (0)
#define BATT_CAL_DEFAULT_P0_MV  (0)
#define BATT_CAL_DEFAULT_P1_RAW (4095)
#define BATT_CAL_DEFAULT_P1_MV  (6600) /* full-scale * divider, placeholder */

/* ---- Image / capture constraints (mirror docs/protocol.md) ---------------- */
#define CAPTURE_WIDTH   (2592)
#define CAPTURE_HEIGHT  (1944)
#define MAX_JPEG_BYTES  (2097152)   /* 2 MiB hard reject before upload (C1) */
#define FOCUS_DEADLINE_MS (1500)    /* autofocus must complete within this (C2) */

/*
 * Remove this guard ONLY after you have filled EVERY pin above from the
 * schematic. It exists so an unconfigured board can't flash and silently
 * misbehave. (Delete the #define and the #error block.)
 */
#define BOARD_CONFIG_UNVERIFIED 1
#if BOARD_CONFIG_UNVERIFIED
#warning "board_config.h pins are placeholders — fill from the Rev A.2/B schematic, then remove BOARD_CONFIG_UNVERIFIED."
#endif

#endif /* BOARD_CONFIG_H */
