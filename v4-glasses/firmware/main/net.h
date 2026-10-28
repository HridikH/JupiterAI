#ifndef GLASSES_NET_H
#define GLASSES_NET_H

#include "esp_err.h"

/* Connect to Wi-Fi STA using credentials from Kconfig (menuconfig), NOT
 * hardcoded. Blocks until connected or returns an error on repeated failure. */
esp_err_t net_connect(void);

#endif /* GLASSES_NET_H */
