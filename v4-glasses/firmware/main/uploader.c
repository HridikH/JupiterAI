/*
 * uploader.c — multipart POST of JPEG + metadata, with buffer-until-ACK retries.
 *
 * Implements the client side of docs/protocol.md §3.1/§5:
 *   - multipart/form-data with `meta` (JSON) + `image` (jpeg)
 *   - headers X-Glasses-Token (from Kconfig) + X-Request-Id
 *   - 5 s per-attempt timeout, max 2 retries (E2); same request_id across retries
 *   - accept only an ACK whose request_id matches (E1); ignore mismatches (E3)
 */
#include "uploader.h"
#include "board_config.h"
#include "logging.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "mbedtls/sha256.h"
#include "sdkconfig.h"
#include <string.h>
#include <stdio.h>

static const char *TAG = "upload";
#define BOUNDARY "----glassesboundary7a1b2c3d"
#define ATTEMPT_TIMEOUT_MS 5000
#define MAX_RETRIES 2

void uploader_new_request_id(char *buf, size_t buflen)
{
    uint32_t a = esp_random();
    uint64_t t = (uint64_t)esp_timer_get_time();
    snprintf(buf, buflen, "%08lx%08lx", (unsigned long)a, (unsigned long)(t & 0xffffffff));
}

static void sha256_hex(const uint8_t *data, size_t len, char out[65])
{
    uint8_t dig[32];
    mbedtls_sha256(data, len, dig, 0);   /* 0 = SHA-256 (not 224) */
    for (int i = 0; i < 32; i++) snprintf(out + i * 2, 3, "%02x", dig[i]);
    out[64] = 0;
}

/* crude value extractor: finds "key":"value" or "key":value in a small json */
static bool json_find_str(const char *json, const char *key, char *out, size_t outlen)
{
    char pat[48];
    snprintf(pat, sizeof(pat), "\"%s\"", key);
    const char *p = strstr(json, pat);
    if (!p) return false;
    p = strchr(p + strlen(pat), ':');
    if (!p) return false;
    p++;
    while (*p == ' ' || *p == '"') p++;
    size_t i = 0;
    while (*p && *p != '"' && *p != ',' && *p != '}' && i < outlen - 1) out[i++] = *p++;
    out[i] = 0;
    return true;
}

static upload_result_t attempt(const char *request_id, int attempt_no,
                               const uint8_t *jpeg, size_t jpeg_len,
                               int width, int height, int battery_mv,
                               const char *sha_hex)
{
    EVT("UPLOAD_TRY", "req=%s attempt=%d", request_id, attempt_no);

    char meta[384];
    int meta_len = snprintf(meta, sizeof(meta),
        "{\"request_id\":\"%s\",\"proto\":\"glasses-proto/1\",\"width\":%d,"
        "\"height\":%d,\"byte_count\":%u,\"sha256\":\"%s\",\"battery_mv\":%d,"
        "\"firmware\":\"glasses-fw/0.1\",\"attempt\":%d}",
        request_id, width, height, (unsigned)jpeg_len, sha_hex, battery_mv, attempt_no);

    char pre_meta[512], pre_img[256], tail[64];
    int n1 = snprintf(pre_meta, sizeof(pre_meta),
        "--" BOUNDARY "\r\nContent-Disposition: form-data; name=\"meta\"\r\n"
        "Content-Type: application/json\r\n\r\n%s\r\n", meta);
    int n2 = snprintf(pre_img, sizeof(pre_img),
        "--" BOUNDARY "\r\nContent-Disposition: form-data; name=\"image\"; "
        "filename=\"capture.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n");
    int n3 = snprintf(tail, sizeof(tail), "\r\n--" BOUNDARY "--\r\n");
    int total = n1 + n2 + (int)jpeg_len + n3;
    (void)meta_len;

    char url[160];
    snprintf(url, sizeof(url), "%s/capture", CONFIG_GLASSES_SERVER_URL);
    esp_http_client_config_t cfg = {
        .url = url, .method = HTTP_METHOD_POST, .timeout_ms = ATTEMPT_TIMEOUT_MS,
    };
    esp_http_client_handle_t cli = esp_http_client_init(&cfg);
    esp_http_client_set_header(cli, "Content-Type", "multipart/form-data; boundary=" BOUNDARY);
    esp_http_client_set_header(cli, "X-Glasses-Token", CONFIG_GLASSES_SHARED_TOKEN);
    esp_http_client_set_header(cli, "X-Request-Id", request_id);

    upload_result_t res = UP_FAIL;
    if (esp_http_client_open(cli, total) != ESP_OK) {
        EVT("UPLOAD_TIMEOUT", "req=%s attempt=%d", request_id, attempt_no);
        esp_http_client_cleanup(cli);
        return UP_FAIL;
    }
    esp_http_client_write(cli, pre_meta, n1);
    esp_http_client_write(cli, pre_img, n2);
    esp_http_client_write(cli, (const char *)jpeg, jpeg_len);   /* buffered until ack (E1) */
    esp_http_client_write(cli, tail, n3);

    int clen = esp_http_client_fetch_headers(cli);
    int status = esp_http_client_get_status_code(cli);
    char body[320] = {0};
    if (clen > 0) {
        int r = esp_http_client_read_response(cli, body, sizeof(body) - 1);
        if (r >= 0) body[r] = 0;
    }
    esp_http_client_close(cli);
    esp_http_client_cleanup(cli);

    if (status == 202) {
        char ack_id[80];
        if (json_find_str(body, "request_id", ack_id, sizeof(ack_id)) &&
            strcmp(ack_id, request_id) == 0) {
            EVT("ACK_OK", "req=%s", request_id);         /* matching ack (E1) */
            res = UP_OK;
        } else {
            EVT("ACK_IGNORED", "req=%s got=%s", request_id, ack_id);  /* wrong id (E3) */
            res = UP_FAIL;
        }
    } else if (status <= 0) {
        EVT("UPLOAD_TIMEOUT", "req=%s attempt=%d", request_id, attempt_no);
    } else {
        ESP_LOGW(TAG, "server returned %d: %s", status, body);  /* 4xx: visible error upstream */
    }
    return res;
}

outcome_t uploader_poll_outcome(const char *request_id, int timeout_ms)
{
    char url[192];
    snprintf(url, sizeof(url), "%s/result/%s", CONFIG_GLASSES_SERVER_URL, request_id);
    int64_t t0 = esp_timer_get_time();
    while ((esp_timer_get_time() - t0) / 1000 < timeout_ms) {
        esp_http_client_config_t cfg = {.url = url, .method = HTTP_METHOD_GET, .timeout_ms = 2000};
        esp_http_client_handle_t cli = esp_http_client_init(&cfg);
        esp_http_client_set_header(cli, "X-Glasses-Token", CONFIG_GLASSES_SHARED_TOKEN);
        outcome_t out = OUTCOME_UNKNOWN;
        if (esp_http_client_open(cli, 0) == ESP_OK) {
            int clen = esp_http_client_fetch_headers(cli);
            char body[256] = {0};
            if (clen != 0) {
                int r = esp_http_client_read_response(cli, body, sizeof(body) - 1);
                if (r >= 0) body[r] = 0;
            }
            char st[32];
            if (json_find_str(body, "status", st, sizeof(st))) {
                if (!strcmp(st, "accepted")) out = OUTCOME_ACCEPTED;
                else if (!strcmp(st, "recapture")) out = OUTCOME_RECAPTURE;
                else if (!strcmp(st, "error")) out = OUTCOME_ERROR;
                else if (!strcmp(st, "candidate_selection")) out = OUTCOME_CANDIDATE;
                else out = OUTCOME_PROCESSING;
            }
        }
        esp_http_client_close(cli);
        esp_http_client_cleanup(cli);
        if (out == OUTCOME_ACCEPTED || out == OUTCOME_RECAPTURE || out == OUTCOME_ERROR)
            return out;
        vTaskDelay(pdMS_TO_TICKS(300));
    }
    return OUTCOME_UNKNOWN;
}

upload_result_t uploader_send(const char *request_id,
                              const uint8_t *jpeg, size_t jpeg_len,
                              int width, int height, int battery_mv)
{
    char sha_hex[65];
    sha256_hex(jpeg, jpeg_len, sha_hex);

    for (int a = 0; a <= MAX_RETRIES; a++) {
        if (attempt(request_id, a, jpeg, jpeg_len, width, height, battery_mv, sha_hex) == UP_OK)
            return UP_OK;
    }
    EVT("UPLOAD_FAIL", "req=%s attempts=%d", request_id, MAX_RETRIES + 1);  /* E2 */
    return UP_FAIL;
}
