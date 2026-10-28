#ifndef GLASSES_BUTTON_H
#define GLASSES_BUTTON_H

#include <stdbool.h>

void button_init(void);

/* Returns true exactly once per clean press (active-low, debounced).
 * Rules enforced here:
 *   - one capture per press; the button must be RELEASED before another press
 *     counts (I1);
 *   - while a transaction is active, presses are ignored (I2) — call
 *     button_set_busy(true) around a transaction.
 * Logs BTN_PRESS / BTN_IGNORED. */
bool button_pressed(void);

void button_set_busy(bool busy);

#endif /* GLASSES_BUTTON_H */
