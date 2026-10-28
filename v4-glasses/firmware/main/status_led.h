#ifndef GLASSES_STATUS_LED_H
#define GLASSES_STATUS_LED_H

typedef enum {
    LED_STATE_READY = 0,
    LED_STATE_PROCESSING,
    LED_STATE_ACCEPTED,
    LED_STATE_RECAPTURE,
    LED_STATE_ERROR,
} led_state_t;

void status_led_init(void);

/* Set the device state LED. Reflects within LED_UPDATE_DEADLINE_MS (I3), pulses
 * the debug transition GPIO, and logs STATE with dur_ms measured from the call
 * so a logic analyzer / the eval parser can verify the <=250 ms budget. */
void status_led_set(led_state_t st);

#endif /* GLASSES_STATUS_LED_H */
