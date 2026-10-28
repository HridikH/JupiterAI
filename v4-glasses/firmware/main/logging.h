/*
 * logging.h — ONE consistent serial log format the eval scripts parse.
 *
 * Every significant event prints exactly one line:
 *
 *     EVT <uptime_ms> <TAG> key=val key=val ...
 *
 * eval/parse_device_logs.py keys off TAG + fields. Keep tags/fields stable; if
 * you change them, update the parser. Tags used (requirement in parens):
 *
 *   BTN_PRESS                                   button accepted
 *   BTN_IGNORED   reason=busy|not_released      (I1, I2)
 *   FOCUS_START   req=<id>                      (C2)
 *   FOCUS_DONE    req=<id> dur_ms=<n>           (C2: dur_ms <= 1500)
 *   CAPTURE_DONE  req=<id> bytes=<n> w=<n> h=<n>
 *   FRAME_REJECT  req=<id> reason=oversize bytes=<n>   (C1)
 *   WORKLIGHT     state=on|off                  (C4)
 *   STATE         led=<name> dur_ms=<n>         (I3: dur_ms <= 250 from trigger)
 *   UPLOAD_TRY    req=<id> attempt=<n>          (E2)
 *   ACK_OK        req=<id>                      (E1)
 *   ACK_IGNORED   req=<id> got=<other_id>       (E3)
 *   UPLOAD_TIMEOUT req=<id> attempt=<n>         (E2)
 *   UPLOAD_FAIL   req=<id> attempts=<n>         (E2: visible error)
 *   BATT          mv=<n>                        (P6)
 */
#ifndef GLASSES_LOGGING_H
#define GLASSES_LOGGING_H

#include <stdio.h>
#include "esp_timer.h"

#define EVT(tag, fmt, ...) \
    printf("EVT %lld " tag " " fmt "\n", \
           (long long)(esp_timer_get_time() / 1000), ##__VA_ARGS__)

#define EVT0(tag) \
    printf("EVT %lld " tag "\n", (long long)(esp_timer_get_time() / 1000))

#endif /* GLASSES_LOGGING_H */
