/*
 * stream.c — on-device MJPEG live feed (option B), the standard esp32-camera way.
 *
 * Serves multipart/x-mixed-replace JPEG at http://<device>:81/stream. The laptop
 * UI points an <img> at the server's /stream, which (for real hardware) proxies
 * this; for the hardware-free demo the simulator pushes frames to the laptop's
 * POST /frame instead (see tools/stream_sim.py).
 *
 * This runs on its own HTTP server/port so it never interferes with the
 * button-triggered full-res capture + upload path. Streaming uses a smaller frame
 * size for bandwidth; the identification capture still uses QSXGA (camera.c).
 *
 * NOTE: for a clean live feed you typically drop the sensor to e.g. SVGA while
 * streaming and raise back to QSXGA for the identification capture — left as a
 * TODO hook (stream_set_framesize) since it depends on your bandwidth budget.
 */
#include <string.h>
#include "stream.h"
#include "logging.h"
#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_log.h"

static const char *TAG = "stream";
static httpd_handle_t s_server = NULL;

#define PART_BOUNDARY "123456789000000000000987654321"
static const char *STREAM_CT = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char *STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static esp_err_t stream_handler(httpd_req_t *req)
{
    esp_err_t res = httpd_resp_set_type(req, STREAM_CT);
    if (res != ESP_OK) return res;
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

    char part_buf[64];
    while (true) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (!fb) { res = ESP_FAIL; break; }
        if (httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY)) != ESP_OK) {
            esp_camera_fb_return(fb); break;
        }
        size_t hlen = snprintf(part_buf, sizeof(part_buf), STREAM_PART, (unsigned)fb->len);
        if (httpd_resp_send_chunk(req, part_buf, hlen) != ESP_OK ||
            httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len) != ESP_OK) {
            esp_camera_fb_return(fb); break;
        }
        esp_camera_fb_return(fb);
    }
    return res;
}

esp_err_t stream_start(void)
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 81;
    config.ctrl_port = 32769;
    if (httpd_start(&s_server, &config) != ESP_OK) {
        ESP_LOGE(TAG, "failed to start stream httpd");
        return ESP_FAIL;
    }
    httpd_uri_t uri = { .uri = "/stream", .method = HTTP_GET, .handler = stream_handler };
    httpd_register_uri_handler(s_server, &uri);
    EVT0("STREAM_START");
    ESP_LOGI(TAG, "MJPEG live feed on :81/stream");
    return ESP_OK;
}

void stream_stop(void)
{
    if (s_server) { httpd_stop(s_server); s_server = NULL; }
}
