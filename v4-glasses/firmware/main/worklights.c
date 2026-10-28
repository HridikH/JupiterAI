#include <stdbool.h>
#include "worklights.h"
#include "board_config.h"
#include "logging.h"
#include "driver/gpio.h"

static inline int level_for(bool on) { return WORKLIGHT_ACTIVE_HIGH ? on : !on; }

void worklights_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << PIN_WORKLIGHT_A) | (1ULL << PIN_WORKLIGHT_B),
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&io);
    worklights_set(false);
}

void worklights_set(bool on)
{
    gpio_set_level(PIN_WORKLIGHT_A, level_for(on));
    gpio_set_level(PIN_WORKLIGHT_B, level_for(on));
    EVT("WORKLIGHT", "state=%s", on ? "on" : "off");
}
