#ifndef GLASSES_WORKLIGHTS_H
#define GLASSES_WORKLIGHTS_H

void worklights_init(void);
/* Drive the two LED enables. MUST be on ONLY during focus + exposure (C4). */
void worklights_set(bool on);

#endif /* GLASSES_WORKLIGHTS_H */
