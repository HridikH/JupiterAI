#include "status_led.h"
#include "board_config.h"
#include "logging.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "rom/ets_sys.h"

static const int s_pins[] = {
    PIN_LED_READY, PIN_LED_PROCESSING, PIN_LED_ACCEPTED, PIN_LED_RECAPTURE, PIN_LED_ERROR,
};
static const char *s_names[] = {"ready", "processing", "accepted", "recapture", "error"};

static inline int on_level(void)  { return LED_ACTIVE_HIGH ? 1 : 0; }
static inline int off_level(void) { return LED_ACTIVE_HIGH ? 0 : 1; }

void status_led_init(void)
{
    uint64_t mask = 0;
    for (size_t i = 0; i < sizeof(s_pins) / sizeof(s_pins[0]); i++)
        mask |= (1ULL << s_pins[i]);
    if (PIN_DBG_STATE_TRANSITION >= 0) mask |= (1ULL << PIN_DBG_STATE_TRANSITION);
    gpio_config_t io = { .pin_bit_mask = mask, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&io);
    for (size_t i = 0; i < sizeof(s_pins) / sizeof(s_pins[0]); i++)
        gpio_set_level(s_pins[i], off_level());
    status_led_set(LED_STATE_READY);
}

void status_led_set(led_state_t st)
{
    int64_t t0 = esp_timer_get_time();

    /* pulse the debug transition GPIO so a logic analyzer timestamps the change (I3) */
    if (PIN_DBG_STATE_TRANSITION >= 0) {
        gpio_set_level(PIN_DBG_STATE_TRANSITION, 1);
        ets_delay_us(5);
        gpio_set_level(PIN_DBG_STATE_TRANSITION, 0);
    }

    for (size_t i = 0; i < sizeof(s_pins) / sizeof(s_pins[0]); i++)
        gpio_set_level(s_pins[i], (i == (size_t)st) ? on_level() : off_level());

    int dur = (int)((esp_timer_get_time() - t0) / 1000);
    EVT("STATE", "led=%s dur_ms=%d", s_names[st], dur);   /* dur_ms <= 250 (I3) */
}
