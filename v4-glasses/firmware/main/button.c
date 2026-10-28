#include "button.h"
#include "board_config.h"
#include "logging.h"
#include "driver/gpio.h"
#include "esp_timer.h"

static volatile bool s_busy = false;
static bool s_was_down = false;         /* tracks release-before-next-press (I1) */
static int64_t s_last_edge_ms = 0;

static inline bool raw_down(void)
{
    return gpio_get_level(PIN_BUTTON) == BUTTON_ACTIVE_LEVEL;
}

void button_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << PIN_BUTTON),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,       /* active-low with pull-up */
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
    };
    gpio_config(&io);
}

void button_set_busy(bool busy) { s_busy = busy; }

bool button_pressed(void)
{
    int64_t now = esp_timer_get_time() / 1000;
    bool down = raw_down();

    /* debounce lockout window (I1) */
    if ((now - s_last_edge_ms) < BUTTON_DEBOUNCE_MS) {
        s_was_down = down;
        return false;
    }

    if (down && !s_was_down) {               /* fresh falling edge */
        s_last_edge_ms = now;
        s_was_down = true;
        if (s_busy) {                        /* ignore while transaction active (I2) */
            EVT("BTN_IGNORED", "reason=busy");
            return false;
        }
        EVT0("BTN_PRESS");
        return true;
    }

    if (!down && s_was_down) {                /* released — arm next press (I1) */
        s_last_edge_ms = now;
        s_was_down = false;
    }
    return false;
}
