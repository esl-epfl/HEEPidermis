// Copyright 2026 Universidad Politecnica de Madrid
// SPDX-License-Identifier: Apache-2.0
//
// Native raw dLC feature extraction.
//
// The FE input is the compressed dLC event stream: dt_ticks and dlvl. The
// smoother is time-aware: each data sample is weighted by the raw duration it
// represents, and the roughness term uses the actual event spacing.

#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_LINE 8192
#define IDAC_LSB_NA 40.0
#define VDD_UV 800000.0
#define VCO_PHASES 62.0

static const double table_vin_uV[] = {
    330000, 340000, 360000, 380000, 400000,
    420000, 440000, 460000, 480000, 500000,
    520000, 540000, 560000, 580000, 600000,
    620000, 640000, 660000, 680000, 700000,
    720000, 740000, 760000, 780000, 800000,
};

static const double table_fosc_hz[] = {
    24000, 26130, 31330, 37320, 45270,
    55150, 67270, 82680, 99870, 121190,
    146020, 175270, 208990, 247770, 291780,
    341260, 396650, 457900, 525140, 598560,
    677660, 762750, 853760, 950200, 1051710,
};

typedef enum {
    FE_METHOD_LEVEL = 0,
    FE_METHOD_DELTA,
    FE_METHOD_RATE,
} fe_method_t;

typedef struct {
    uint16_t dt_ticks;
    int16_t dlvl;
    int64_t tick;
    double level;
} dlc_event_t;

typedef struct {
    uint8_t *data;
    size_t len;
    size_t cap;
} byte_vec_t;

typedef struct {
    dlc_event_t *data;
    size_t len;
    size_t cap;
} event_vec_t;

typedef struct {
    char ses_cfg[MAX_LINE];
    char dlc_cfg[MAX_LINE];
    double sample_rate_hz;
    double vco_fs_hz;
    int log_level_width;
    int level_width;
    int initial_level;
    int idac_code;
    int dlvl_bits;
    int dt_bits;
    int sign_magnitude;
} dlc_config_t;

static void usage(const char *argv0)
{
    fprintf(stderr,
            "Usage: %s [--input sim.txt] [--method delta|rate|level] "
            "[--lambda-weight W] [--crop-events N|auto] "
            "[--include-post-fe-reconstruction]\n",
            argv0);
}

static int parse_method(const char *text, fe_method_t *method)
{
    if (strcmp(text, "level") == 0) {
        *method = FE_METHOD_LEVEL;
        return 0;
    }
    if (strcmp(text, "delta") == 0) {
        *method = FE_METHOD_DELTA;
        return 0;
    }
    if (strcmp(text, "rate") == 0) {
        *method = FE_METHOD_RATE;
        return 0;
    }
    return -1;
}

static const char *method_name(fe_method_t method)
{
    switch (method) {
    case FE_METHOD_LEVEL:
        return "level";
    case FE_METHOD_DELTA:
        return "delta";
    case FE_METHOD_RATE:
        return "rate";
    default:
        return "unknown";
    }
}

static const char *feature_domain_name(fe_method_t method)
{
    switch (method) {
    case FE_METHOD_LEVEL:
        return "level";
    case FE_METHOD_RATE:
        return "dlvl_per_tick";
    case FE_METHOD_DELTA:
    default:
        return "dlvl";
    }
}

static const char *weight_name(fe_method_t method)
{
    return method == FE_METHOD_LEVEL ? "hold_ticks" : "dt_ticks";
}

static int byte_vec_push(byte_vec_t *vec, uint8_t value)
{
    if (vec->len == vec->cap) {
        size_t new_cap = vec->cap ? vec->cap * 2u : 1024u;
        uint8_t *new_data = (uint8_t *)realloc(vec->data, new_cap * sizeof(*new_data));
        if (!new_data) {
            return -1;
        }
        vec->data = new_data;
        vec->cap = new_cap;
    }
    vec->data[vec->len++] = value;
    return 0;
}

static int event_vec_push(event_vec_t *vec, dlc_event_t value)
{
    if (vec->len == vec->cap) {
        size_t new_cap = vec->cap ? vec->cap * 2u : 1024u;
        dlc_event_t *new_data = (dlc_event_t *)realloc(vec->data, new_cap * sizeof(*new_data));
        if (!new_data) {
            return -1;
        }
        vec->data = new_data;
        vec->cap = new_cap;
    }
    vec->data[vec->len++] = value;
    return 0;
}

static int hex_value(int ch)
{
    if (ch >= '0' && ch <= '9') {
        return ch - '0';
    }
    if (ch >= 'a' && ch <= 'f') {
        return 10 + ch - 'a';
    }
    if (ch >= 'A' && ch <= 'F') {
        return 10 + ch - 'A';
    }
    return -1;
}

static int append_hex_payload(byte_vec_t *bytes, const char *payload)
{
    int high_nibble = -1;

    for (const char *p = payload; *p; p++) {
        int value = hex_value((unsigned char)*p);

        if (value < 0) {
            if (isspace((unsigned char)*p)) {
                continue;
            }
            fprintf(stderr, "ERROR: invalid hex character '%c'\n", *p);
            return -1;
        }

        if (high_nibble < 0) {
            high_nibble = value;
        } else {
            uint8_t byte = (uint8_t)((high_nibble << 4) | value);
            if (byte_vec_push(bytes, byte) != 0) {
                return -1;
            }
            high_nibble = -1;
        }
    }

    if (high_nibble >= 0) {
        fprintf(stderr, "ERROR: odd number of HEX digits\n");
        return -1;
    }

    return 0;
}

static int get_param_token(const char *line, const char *key, char *out, size_t out_size)
{
    size_t key_len = strlen(key);
    const char *p = line;

    while (*p) {
        while (*p && isspace((unsigned char)*p)) {
            p++;
        }

        const char *token = p;
        while (*p && !isspace((unsigned char)*p)) {
            p++;
        }

        size_t token_len = (size_t)(p - token);
        if (token_len > key_len + 1u &&
            strncmp(token, key, key_len) == 0 &&
            token[key_len] == '=') {
            size_t value_len = token_len - key_len - 1u;
            if (value_len >= out_size) {
                value_len = out_size - 1u;
            }
            memcpy(out, token + key_len + 1u, value_len);
            out[value_len] = '\0';
            return 0;
        }
    }

    return -1;
}

static int param_int(const char *line, const char *key, int default_value, int required)
{
    char value[128];
    char *end = NULL;
    long parsed;

    if (get_param_token(line, key, value, sizeof(value)) != 0) {
        if (required) {
            fprintf(stderr, "ERROR: missing DLC_CFG parameter %s\n", key);
            exit(EXIT_FAILURE);
        }
        return default_value;
    }

    errno = 0;
    parsed = strtol(value, &end, 0);
    if (errno || end == value || *end != '\0') {
        fprintf(stderr, "ERROR: invalid integer for %s: %s\n", key, value);
        exit(EXIT_FAILURE);
    }

    return (int)parsed;
}

static double param_double(const char *line, const char *key, double default_value, int required)
{
    char value[128];
    char *end = NULL;
    double parsed;

    if (get_param_token(line, key, value, sizeof(value)) != 0) {
        if (required) {
            fprintf(stderr, "ERROR: missing DLC_CFG parameter %s\n", key);
            exit(EXIT_FAILURE);
        }
        return default_value;
    }

    errno = 0;
    parsed = strtod(value, &end);
    if (errno || end == value || *end != '\0') {
        fprintf(stderr, "ERROR: invalid float for %s: %s\n", key, value);
        exit(EXIT_FAILURE);
    }

    return parsed;
}

static int param_is_sign_magnitude(const char *line)
{
    char value[128];

    if (get_param_token(line, "FORMAT", value, sizeof(value)) != 0) {
        return 1;
    }

    return strcmp(value, "sign_magnitude") == 0;
}

static void trim_newline(char *line)
{
    size_t len = strlen(line);

    while (len > 0 && (line[len - 1u] == '\n' || line[len - 1u] == '\r')) {
        line[--len] = '\0';
    }
}

static int read_event_bytes(const char *path, dlc_config_t *cfg, byte_vec_t *bytes)
{
    FILE *f = fopen(path, "r");
    char line[MAX_LINE];

    if (!f) {
        perror(path);
        return -1;
    }

    if (!fgets(cfg->ses_cfg, sizeof(cfg->ses_cfg), f) ||
        !fgets(cfg->dlc_cfg, sizeof(cfg->dlc_cfg), f)) {
        fprintf(stderr, "ERROR: %s does not contain SES_CFG and DLC_CFG lines\n", path);
        fclose(f);
        return -1;
    }
    trim_newline(cfg->ses_cfg);
    trim_newline(cfg->dlc_cfg);

    cfg->sample_rate_hz = param_double(cfg->dlc_cfg, "SAMPLE_RATE_HZ", 0.0, 1);
    cfg->vco_fs_hz = param_double(cfg->dlc_cfg, "VCO_FS_HZ", 0.0, 1);
    cfg->log_level_width = param_int(cfg->dlc_cfg, "LOG_LEVEL_WIDTH", 0, 1);
    cfg->initial_level = param_int(cfg->dlc_cfg, "INITIAL_LEVEL", 0, 0);
    cfg->idac_code = param_int(cfg->dlc_cfg, "IDAC_CODE", 7, 0);
    cfg->dlvl_bits = param_int(cfg->dlc_cfg, "DLVL_BITS", 2, 0);
    cfg->dt_bits = param_int(cfg->dlc_cfg, "DT_BITS", 8 - cfg->dlvl_bits, 0);
    cfg->sign_magnitude = param_is_sign_magnitude(cfg->dlc_cfg);

    if (cfg->sample_rate_hz <= 0.0 || cfg->vco_fs_hz <= 0.0) {
        fprintf(stderr, "ERROR: SAMPLE_RATE_HZ and VCO_FS_HZ must be positive\n");
        fclose(f);
        return -1;
    }
    if (cfg->log_level_width < 0 || cfg->log_level_width > 30) {
        fprintf(stderr, "ERROR: invalid LOG_LEVEL_WIDTH=%d\n", cfg->log_level_width);
        fclose(f);
        return -1;
    }
    if (cfg->dlvl_bits <= 0 || cfg->dlvl_bits >= 8 || cfg->dt_bits <= 0 ||
        cfg->dlvl_bits + cfg->dt_bits > 8 ||
        (cfg->sign_magnitude && cfg->dlvl_bits < 2)) {
        fprintf(stderr, "ERROR: invalid DLVL_BITS=%d DT_BITS=%d\n",
                cfg->dlvl_bits, cfg->dt_bits);
        fclose(f);
        return -1;
    }
    cfg->level_width = 1 << cfg->log_level_width;

    while (fgets(line, sizeof(line), f)) {
        char second[128];
        char *p = line;

        while (*p && isspace((unsigned char)*p)) {
            p++;
        }
        if (!*p) {
            continue;
        }

        if (strncmp(p, "HEX", 3) == 0 && isspace((unsigned char)p[3])) {
            if (append_hex_payload(bytes, p + 3) != 0) {
                fclose(f);
                return -1;
            }
            continue;
        }

        if (sscanf(p, "%*127s %127s", second) >= 1) {
            char *end = NULL;
            unsigned long value;

            errno = 0;
            value = strtoul(second, &end, 0);
            if (errno || end == second) {
                fprintf(stderr, "ERROR: invalid event word: %s\n", second);
                fclose(f);
                return -1;
            }
            if (byte_vec_push(bytes, (uint8_t)(value & 0xffu)) != 0 ||
                byte_vec_push(bytes, (uint8_t)((value >> 8) & 0xffu)) != 0) {
                fclose(f);
                return -1;
            }
        }
    }

    fclose(f);
    return 0;
}

static int decode_packed_dlvl(uint8_t byte, const dlc_config_t *cfg)
{
    unsigned mask = (1u << cfg->dlvl_bits) - 1u;
    unsigned raw = byte & mask;

    if (cfg->sign_magnitude) {
        unsigned sign_bit = raw >> (cfg->dlvl_bits - 1);
        unsigned mag_mask = (1u << (cfg->dlvl_bits - 1)) - 1u;
        int magnitude = (int)(raw & mag_mask);
        return sign_bit ? -magnitude : magnitude;
    }

    if (raw & (1u << (cfg->dlvl_bits - 1))) {
        raw |= ~mask;
    }

    return (int)(int8_t)raw;
}

static int decode_events(const byte_vec_t *bytes, const dlc_config_t *cfg,
                         event_vec_t *events)
{
    int64_t current_tick = 0;

    for (size_t i = 0; i < bytes->len; i++) {
        uint8_t byte = bytes->data[i];
        unsigned dt_mask = (1u << cfg->dt_bits) - 1u;
        int dlvl = decode_packed_dlvl(byte, cfg);
        uint16_t dt_ticks = (uint16_t)((byte >> cfg->dlvl_bits) & dt_mask);

        if (dt_ticks == 0u) {
            if (events->len > 0) {
                dlc_event_t *last = &events->data[events->len - 1u];
                last->dlvl = (int16_t)(last->dlvl + dlvl);
            }
            continue;
        }

        current_tick += (int64_t)dt_ticks;

        dlc_event_t ev;
        ev.dt_ticks = dt_ticks;
        ev.dlvl = (int16_t)dlvl;
        ev.tick = current_tick;
        ev.level = 0.0;

        if (event_vec_push(events, ev) != 0) {
            return -1;
        }
    }

    if (events->len == 0) {
        fprintf(stderr, "ERROR: no raw dLC events found after decoding\n");
        return -1;
    }

    return 0;
}

static double level_before_event(const dlc_config_t *cfg, const event_vec_t *events,
                                 size_t event_index)
{
    double level = (double)cfg->initial_level;

    for (size_t i = 0; i < event_index; i++) {
        level += (double)events->data[i].dlvl;
    }

    return level;
}

static size_t parse_crop_events(const char *text, size_t event_count)
{
    char *end = NULL;
    long value;

    if (strcmp(text, "auto") == 0) {
        size_t room = event_count > 3u ? (event_count - 3u) / 4u : 0u;
        return room < 50u ? room : 50u;
    }

    errno = 0;
    value = strtol(text, &end, 10);
    if (errno || end == text || *end != '\0' || value < 0) {
        fprintf(stderr, "ERROR: --crop-events must be >= 0 or auto\n");
        exit(EXIT_FAILURE);
    }

    return (size_t)value;
}

static double interp_vin_uV(double freq_hz)
{
    size_t n = sizeof(table_fosc_hz) / sizeof(table_fosc_hz[0]);

    if (freq_hz <= table_fosc_hz[0]) {
        return table_vin_uV[0];
    }
    if (freq_hz >= table_fosc_hz[n - 1u]) {
        return table_vin_uV[n - 1u];
    }

    for (size_t hi = 1; hi < n; hi++) {
        if (freq_hz <= table_fosc_hz[hi]) {
            size_t lo = hi - 1u;
            double x0 = table_fosc_hz[lo];
            double x1 = table_fosc_hz[hi];
            double y0 = table_vin_uV[lo];
            double y1 = table_vin_uV[hi];
            return y0 + ((y1 - y0) * (freq_hz - x0) / (x1 - x0));
        }
    }

    return table_vin_uV[n - 1u];
}

static double level_to_conductance_nS(double level, const dlc_config_t *cfg)
{
    double freq_hz = level * (double)cfg->level_width * cfg->vco_fs_hz / VCO_PHASES;
    double vin_uV = interp_vin_uV(freq_hz);
    double dv_uV = VDD_UV - vin_uV;
    double idac_nA = (double)cfg->idac_code * IDAC_LSB_NA;

    return dv_uV > 0.0 ? (idac_nA * 1e6 / dv_uV) : 0.0;
}

static void add_symmetric_band(size_t n, double *d0, double *d1, double *d2,
                               size_t row, size_t col, double value)
{
    size_t tmp;
    size_t delta;

    if (row > col) {
        tmp = row;
        row = col;
        col = tmp;
    }

    delta = col - row;
    if (delta == 0u) {
        d0[row] += value;
    } else if (delta == 1u && row + 1u < n) {
        d1[row] += value;
    } else if (delta == 2u && row + 2u < n) {
        d2[row] += value;
    }
}

static int solve_pentadiagonal(size_t n, double *d0, double *d1, double *d2,
                               double *b, double *x)
{
    double *l1 = NULL;
    double *l2 = NULL;

    if (n == 0u) {
        return -1;
    }
    if (n == 1u) {
        if (fabs(d0[0]) < 1e-18) {
            return -1;
        }
        x[0] = b[0] / d0[0];
        return 0;
    }

    l1 = (double *)calloc(n - 1u, sizeof(*l1));
    l2 = n > 2u ? (double *)calloc(n - 2u, sizeof(*l2)) : NULL;
    if (!l1 || (n > 2u && !l2)) {
        free(l1);
        free(l2);
        return -1;
    }

    for (size_t i = 0; i + 2u < n; i++) {
        double u1;
        double u2;

        if (fabs(d0[i]) < 1e-18) {
            free(l1);
            free(l2);
            return -1;
        }

        u1 = d1[i];
        u2 = d2[i];
        l1[i] = u1 / d0[i];
        l2[i] = u2 / d0[i];

        d0[i + 1u] -= l1[i] * u1;
        d1[i + 1u] -= l1[i] * u2;
        d0[i + 2u] -= l2[i] * u2;
    }

    {
        size_t i = n - 2u;
        if (fabs(d0[i]) < 1e-18) {
            free(l1);
            free(l2);
            return -1;
        }
        l1[i] = d1[i] / d0[i];
        d0[i + 1u] -= l1[i] * d1[i];
    }

    for (size_t i = 0; i + 2u < n; i++) {
        b[i + 1u] -= l1[i] * b[i];
        b[i + 2u] -= l2[i] * b[i];
    }
    b[n - 1u] -= l1[n - 2u] * b[n - 2u];

    if (fabs(d0[n - 1u]) < 1e-18) {
        free(l1);
        free(l2);
        return -1;
    }
    x[n - 1u] = b[n - 1u] / d0[n - 1u];

    if (fabs(d0[n - 2u]) < 1e-18) {
        free(l1);
        free(l2);
        return -1;
    }
    x[n - 2u] = (b[n - 2u] - d1[n - 2u] * x[n - 1u]) / d0[n - 2u];

    for (long i = (long)n - 3; i >= 0; i--) {
        double sum = d1[i] * x[i + 1] + d2[i] * x[i + 2];

        if (fabs(d0[i]) < 1e-18) {
            free(l1);
            free(l2);
            return -1;
        }
        x[i] = (b[i] - sum) / d0[i];
    }

    free(l1);
    free(l2);
    return 0;
}

static int smooth_time_weighted(size_t n, const double *ticks, const double *input,
                                const double *weights, double lambda_weight,
                                double *output)
{
    double *d0 = (double *)calloc(n, sizeof(*d0));
    double *d1 = n > 1u ? (double *)calloc(n - 1u, sizeof(*d1)) : NULL;
    double *d2 = n > 2u ? (double *)calloc(n - 2u, sizeof(*d2)) : NULL;
    double *rhs = (double *)calloc(n, sizeof(*rhs));
    double lambda_sq = lambda_weight * lambda_weight;

    if (!d0 || (n > 1u && !d1) || (n > 2u && !d2) || !rhs) {
        free(d0);
        free(d1);
        free(d2);
        free(rhs);
        return -1;
    }

    for (size_t i = 0; i < n; i++) {
        double w = weights[i] > 0.0 ? weights[i] : 1.0;
        d0[i] += w;
        rhs[i] = w * input[i];
    }

    if (lambda_sq > 0.0 && n >= 3u) {
        for (size_t i = 1; i + 1u < n; i++) {
            double h_prev = ticks[i] - ticks[i - 1u];
            double h_next = ticks[i + 1u] - ticks[i];
            double scale;
            double c0;
            double c1;
            double c2;
            double alpha;
            size_t idx[3] = {i - 1u, i, i + 1u};
            double c[3];

            if (h_prev <= 0.0) {
                h_prev = 1.0;
            }
            if (h_next <= 0.0) {
                h_next = 1.0;
            }

            scale = h_prev + h_next;
            c0 = 2.0 / (h_prev * scale);
            c1 = -2.0 / (h_prev * h_next);
            c2 = 2.0 / (h_next * scale);
            alpha = lambda_sq * 0.5 * scale;

            c[0] = c0;
            c[1] = c1;
            c[2] = c2;

            for (size_t r = 0; r < 3u; r++) {
                for (size_t col = r; col < 3u; col++) {
                    add_symmetric_band(n, d0, d1, d2, idx[r], idx[col],
                                       alpha * c[r] * c[col]);
                }
            }
        }
    }

    if (solve_pentadiagonal(n, d0, d1, d2, rhs, output) != 0) {
        free(d0);
        free(d1);
        free(d2);
        free(rhs);
        return -1;
    }

    free(d0);
    free(d1);
    free(d2);
    free(rhs);
    return 0;
}

int main(int argc, char **argv)
{
    const char *input_path = "sim.txt";
    const char *crop_arg = "auto";
    double lambda_weight = 1.0;
    fe_method_t method = FE_METHOD_DELTA;
    int include_post_fe_reconstruction = 0;
    byte_vec_t bytes = {0};
    event_vec_t events = {0};
    dlc_config_t cfg;
    size_t crop_n;
    size_t start;
    size_t n;
    double *ticks = NULL;
    double *dt_ticks = NULL;
    double *hold_ticks = NULL;
    double *weight_ticks = NULL;
    double *dlvl = NULL;
    double *level = NULL;
    double *fe_input = NULL;
    double *tonic_fe_input = NULL;
    double *tonic_dlvl = NULL;
    double *phasic_dlvl = NULL;
    double *tonic_level = NULL;
    double baseline_level = 0.0;
    int level_fe_input;
    int need_level_domain;

    memset(&cfg, 0, sizeof(cfg));

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--input") == 0 && i + 1 < argc) {
            input_path = argv[++i];
        } else if (strcmp(argv[i], "--method") == 0 && i + 1 < argc) {
            if (parse_method(argv[++i], &method) != 0) {
                fprintf(stderr, "ERROR: unsupported method: %s\n", argv[i]);
                usage(argv[0]);
                return EXIT_FAILURE;
            }
        } else if ((strcmp(argv[i], "--lambda") == 0 ||
                    strcmp(argv[i], "--lambda-weight") == 0) && i + 1 < argc) {
            char *end = NULL;
            errno = 0;
            lambda_weight = strtod(argv[++i], &end);
            if (errno || end == argv[i] || *end != '\0' || lambda_weight < 0.0) {
                fprintf(stderr, "ERROR: --lambda-weight must be a nonnegative number\n");
                return EXIT_FAILURE;
            }
        } else if (strcmp(argv[i], "--crop-events") == 0 && i + 1 < argc) {
            crop_arg = argv[++i];
        } else if (strcmp(argv[i], "--include-post-fe-reconstruction") == 0) {
            include_post_fe_reconstruction = 1;
        } else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            usage(argv[0]);
            return EXIT_SUCCESS;
        } else {
            fprintf(stderr, "ERROR: unknown or incomplete argument: %s\n", argv[i]);
            usage(argv[0]);
            return EXIT_FAILURE;
        }
    }

    level_fe_input = method == FE_METHOD_LEVEL;
    need_level_domain = level_fe_input || include_post_fe_reconstruction;

    if (read_event_bytes(input_path, &cfg, &bytes) != 0 ||
        decode_events(&bytes, &cfg, &events) != 0) {
        free(bytes.data);
        free(events.data);
        return EXIT_FAILURE;
    }

    crop_n = parse_crop_events(crop_arg, events.len);
    if (2u * crop_n >= events.len) {
        fprintf(stderr, "ERROR: crop of %zu events removes all %zu events\n",
                crop_n, events.len);
        free(bytes.data);
        free(events.data);
        return EXIT_FAILURE;
    }

    start = crop_n;
    n = events.len - (2u * crop_n);

    ticks = (double *)calloc(n, sizeof(*ticks));
    dt_ticks = (double *)calloc(n, sizeof(*dt_ticks));
    hold_ticks = (double *)calloc(n, sizeof(*hold_ticks));
    weight_ticks = (double *)calloc(n, sizeof(*weight_ticks));
    dlvl = (double *)calloc(n, sizeof(*dlvl));
    level = need_level_domain ? (double *)calloc(n, sizeof(*level)) : NULL;
    fe_input = (double *)calloc(n, sizeof(*fe_input));
    tonic_fe_input = (double *)calloc(n, sizeof(*tonic_fe_input));
    tonic_dlvl = (double *)calloc(n, sizeof(*tonic_dlvl));
    phasic_dlvl = (double *)calloc(n, sizeof(*phasic_dlvl));
    tonic_level = need_level_domain ? (double *)calloc(n, sizeof(*tonic_level)) : NULL;

    if (!ticks || !dt_ticks || !hold_ticks || !weight_ticks || !dlvl ||
        (need_level_domain && (!level || !tonic_level)) ||
        !fe_input || !tonic_fe_input || !tonic_dlvl || !phasic_dlvl) {
        fprintf(stderr, "ERROR: out of memory\n");
        free(bytes.data);
        free(events.data);
        free(ticks);
        free(dt_ticks);
        free(hold_ticks);
        free(weight_ticks);
        free(dlvl);
        free(level);
        free(fe_input);
        free(tonic_fe_input);
        free(tonic_dlvl);
        free(phasic_dlvl);
        free(tonic_level);
        return EXIT_FAILURE;
    }

    if (level_fe_input) {
        baseline_level = level_before_event(&cfg, &events, start);
    }

    for (size_t i = 0; i < n; i++) {
        const dlc_event_t *ev = &events.data[start + i];
        double future_hold;

        ticks[i] = (double)ev->tick;
        dt_ticks[i] = (double)ev->dt_ticks;
        dlvl[i] = (double)ev->dlvl;
        if (level_fe_input) {
            level[i] = (i == 0u ? baseline_level : level[i - 1u]) + dlvl[i];
        }

        if (i + 1u < n) {
            future_hold = (double)(events.data[start + i + 1u].tick - ev->tick);
        } else {
            future_hold = dt_ticks[i];
        }
        hold_ticks[i] = future_hold > 0.0 ? future_hold : 1.0;

        if (method == FE_METHOD_LEVEL) {
            fe_input[i] = level[i];
            weight_ticks[i] = hold_ticks[i];
        } else if (method == FE_METHOD_RATE) {
            fe_input[i] = dt_ticks[i] > 0.0 ? dlvl[i] / dt_ticks[i] : 0.0;
            weight_ticks[i] = dt_ticks[i] > 0.0 ? dt_ticks[i] : 1.0;
        } else {
            fe_input[i] = dlvl[i];
            weight_ticks[i] = dt_ticks[i] > 0.0 ? dt_ticks[i] : 1.0;
        }
    }

    if (smooth_time_weighted(n, ticks, fe_input, weight_ticks, lambda_weight,
                             tonic_fe_input) != 0) {
        fprintf(stderr, "ERROR: time-aware FE solve failed\n");
        free(bytes.data);
        free(events.data);
        free(ticks);
        free(dt_ticks);
        free(hold_ticks);
        free(weight_ticks);
        free(dlvl);
        free(level);
        free(fe_input);
        free(tonic_fe_input);
        free(tonic_dlvl);
        free(phasic_dlvl);
        free(tonic_level);
        return EXIT_FAILURE;
    }

    if (need_level_domain && !level_fe_input) {
        baseline_level = level_before_event(&cfg, &events, start);
    }

    for (size_t i = 0; i < n; i++) {
        if (need_level_domain && !level_fe_input) {
            level[i] = (i == 0u ? baseline_level : level[i - 1u]) + dlvl[i];
        }

        if (method == FE_METHOD_LEVEL) {
            tonic_level[i] = tonic_fe_input[i];
            tonic_dlvl[i] = i == 0u ? (tonic_level[i] - baseline_level)
                                    : (tonic_level[i] - tonic_level[i - 1u]);
        } else if (method == FE_METHOD_RATE) {
            tonic_dlvl[i] = tonic_fe_input[i] * dt_ticks[i];
            if (need_level_domain) {
                tonic_level[i] = (i == 0u ? baseline_level : tonic_level[i - 1u]) +
                                 tonic_dlvl[i];
            }
        } else {
            tonic_dlvl[i] = tonic_fe_input[i];
            if (need_level_domain) {
                tonic_level[i] = (i == 0u ? baseline_level : tonic_level[i - 1u]) +
                                 tonic_dlvl[i];
            }
        }
        phasic_dlvl[i] = dlvl[i] - tonic_dlvl[i];
    }

    printf("# RAW_DLC_FE=1 FEA_IMPL=C TIME_AWARE=1 METHOD=%s FEATURE_DOMAIN=%s "
           "PRE_FE_RECONSTRUCTION=0 POST_FE_RECONSTRUCTION=%d LAMBDA=%.10g "
           "INPUT=%s SAMPLE_RATE_HZ=%.10g LOG_LEVEL_WIDTH=%d INITIAL_LEVEL=%d "
           "IDAC_CODE=%d CROP_EVENTS_EACH_END=%zu EVENTS=%zu TIME_WEIGHT=%s "
           "ROUGHNESS=irregular_second_difference\n",
           method_name(method), feature_domain_name(method),
           include_post_fe_reconstruction, lambda_weight, input_path,
           cfg.sample_rate_hz, cfg.log_level_width, cfg.initial_level,
           cfg.idac_code, crop_n, n, weight_name(method));
    printf("k,time_s,ticks,dt_ticks,dt_s,hold_ticks,weight_ticks,dlvl,"
           "fe_input,tonic_fe_input,phasic_fe_input,tonic_dlvl,phasic_dlvl");
    if (include_post_fe_reconstruction) {
        printf(",level,tonic_level,phasic_level,g_nS,tonic,phasic");
    }
    printf("\n");

    for (size_t i = 0; i < n; i++) {
        double time_s = ticks[i] / cfg.sample_rate_hz;
        double dt_s = dt_ticks[i] / cfg.sample_rate_hz;
        double phasic_fe_input = fe_input[i] - tonic_fe_input[i];

        printf("%zu,%.10g,%.0f,%.0f,%.10g,%.10g,%.10g,%.10g,"
               "%.10g,%.10g,%.10g,%.10g,%.10g",
               i, time_s, ticks[i], dt_ticks[i], dt_s, hold_ticks[i],
               weight_ticks[i], dlvl[i], fe_input[i], tonic_fe_input[i],
               phasic_fe_input, tonic_dlvl[i], phasic_dlvl[i]);

        if (include_post_fe_reconstruction) {
            double phasic_level = level[i] - tonic_level[i];
            double g_nS = level_to_conductance_nS(level[i], &cfg);
            double tonic_nS = level_to_conductance_nS(tonic_level[i], &cfg);
            double phasic_nS = g_nS - tonic_nS;

            printf(",%.10g,%.10g,%.10g,%.10g,%.10g,%.10g",
                   level[i], tonic_level[i], phasic_level, g_nS, tonic_nS,
                   phasic_nS);
        }
        printf("\n");
    }

    free(bytes.data);
    free(events.data);
    free(ticks);
    free(dt_ticks);
    free(hold_ticks);
    free(weight_ticks);
    free(dlvl);
    free(level);
    free(fe_input);
    free(tonic_fe_input);
    free(tonic_dlvl);
    free(phasic_dlvl);
    free(tonic_level);

    return EXIT_SUCCESS;
}
