// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Synchronized P/N GSR acquisition for the desktop monitor.

#include <stdint.h>

#include "syscalls.h"
#include "GSR_controller.h"
#include "REFs_ctrl.h"
#include "iDAC_ctrl.h"
#include "soc_ctrl.h"
#include "timer_sdk.h"
#include "uart.h"
#include "uart_regs.h"

// Build values provide startup defaults; the GUI can update both volatile words over JTAG.
#define SYS_FCLK_HZ 10000000
#define VCO_FS_HZ 10
#define VCO_SAMPLE_RATE_MILLIHZ 10000
#define INJECTED_CURRENT_NA 240

#define IDAC_LSB_NA 40
#define IDAC_MAX_CODE 255
#define IREF_DEFAULT_CAL 255
#define IDAC_DEFAULT_CAL 15
#define VREF_DEFAULT_CAL 1023
#define FEEDBACK_PERIOD_CC ((SYS_FCLK_HZ <= UINT32_MAX / 3U) ? (SYS_FCLK_HZ * 3U) : UINT32_MAX)

// The GUI updates these words over JTAG; firmware rereads them in the acquisition loop.
volatile uint32_t gsr_injected_current_nA = INJECTED_CURRENT_NA;
volatile uint32_t gsr_sample_rate_millihz = VCO_SAMPLE_RATE_MILLIHZ;

#if VCO_FS_HZ < 1 || (SYS_FCLK_HZ / VCO_FS_HZ) < 100
#error "Sampling period must be at least 100 MCU cycles"
#endif
#if VCO_SAMPLE_RATE_MILLIHZ < 100 || VCO_SAMPLE_RATE_MILLIHZ > 10000000
#error "Output sample rate must be between 0.1 Hz and 10000 Hz"
#endif
#if VCO_SAMPLE_RATE_MILLIHZ < 1000 && ((SYS_FCLK_HZ * 1000ULL) / VCO_SAMPLE_RATE_MILLIHZ) > UINT32_MAX
#error "Sub-Hz output period exceeds the 32-bit cycle timer"
#endif
#if INJECTED_CURRENT_NA < IDAC_LSB_NA || INJECTED_CURRENT_NA > (IDAC_LSB_NA * IDAC_MAX_CODE) || (INJECTED_CURRENT_NA % IDAC_LSB_NA) != 0
#error "Injected current must be a nonzero multiple of 40 nA, up to 10200 nA"
#endif

static char *append_u32(char *out, uint32_t value) {
    char digits[10];
    uint8_t count = 0U;
    do {
        digits[count++] = (char)('0' + (value % 10U));
        value /= 10U;
    } while (value != 0U);
    while (count != 0U) *out++ = digits[--count];
    return out;
}

static void print_sample(uint32_t index, uint32_t p_Hz, uint32_t n_Hz, uint32_t current_nA) {
    char line[46];
    char *out = line;
    out = append_u32(out, index); *out++ = ',';
    out = append_u32(out, p_Hz); *out++ = ',';
    out = append_u32(out, n_Hz); *out++ = ',';
    out = append_u32(out, current_nA); *out++ = '\n';
    *out = '\0';
    _writestr(line);
}

static void print_setting(const char *name, uint32_t value, const char *unit) {
    char number[11];
    *append_u32(number, value) = '\0';
    _writestr("[i] ");
    _writestr(name);
    _writestr(number);
    _writestr(unit);
}

static const char *acquisition_feedback(gsr_status_t status, const vco_pair_sample_t *pair) {
    if (status == GSR_STATUS_MISSED_UPDATE)
        return "[i] Missed updates: decrease sample rate (CPU/UART limit).\n";
    if (status == GSR_STATUS_UNDERFLOW || status == GSR_STATUS_OVERFLOW) {
        if (pair->n_Hz < VCO_MIN_FREQUENCY_HZ || pair->n_Hz > VCO_MAX_FREQUENCY_HZ)
            return "[i] N out of range: check reference/supply; lower sample rate if counts are sparse.\n";
        // Front-end model: P voltage = reference - current / conductance.
        if (pair->p_Hz < VCO_MIN_FREQUENCY_HZ)
            return "[i] P below range: decrease current; check resistor/contact; lower sample rate if counts are sparse.\n";
        if (pair->p_Hz > VCO_MAX_FREQUENCY_HZ)
            return "[i] P above range: increase current to lower P voltage.\n";
        return "[i] P-N unresolved: increase current or decrease sample rate.\n";
    }
    return "[i] Acquisition setup error: lower sample rate and rebuild.\n";
}

typedef struct {
    uint32_t value_nA;
    uint8_t state;
} current_command_t;

// Accept one short ASCII command: I=<current in nA>\n.
static void poll_current_command(const uart_t *uart, current_command_t *command) {
    while (!(mmio_region_read32(uart->base_addr, UART_STATUS_REG_OFFSET) &
             (1U << UART_STATUS_RXEMPTY_BIT))) {
        uint8_t byte = (uint8_t)(mmio_region_read32(uart->base_addr, UART_RDATA_REG_OFFSET) &
                                 UART_RDATA_RDATA_MASK);
        if (byte == 'I') {
            command->state = 1U;
            command->value_nA = 0U;
        } else if (command->state == 1U && byte == '=') {
            command->state = 2U;
        } else if (command->state == 2U && byte >= '0' && byte <= '9' &&
                   command->value_nA <= (IDAC_LSB_NA * IDAC_MAX_CODE) / 10U) {
            command->value_nA = command->value_nA * 10U + (uint32_t)(byte - '0');
        } else if (command->state == 2U && (byte == '\n' || byte == '\r')) {
            if (command->value_nA >= IDAC_LSB_NA &&
                command->value_nA <= IDAC_LSB_NA * IDAC_MAX_CODE &&
                command->value_nA % IDAC_LSB_NA == 0U) {
                gsr_injected_current_nA = command->value_nA;
            }
            command->state = 0U;
        } else {
            command->state = 0U;
        }
    }
}

int main(void) {
    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);
    soc_ctrl_set_frequency(&soc_ctrl, SYS_FCLK_HZ);

    uart_t uart = {
        .base_addr = mmio_region_from_addr((uintptr_t)UART_START_ADDRESS),
        .baudrate = SYS_FCLK_HZ / 20U,
        .clk_freq_hz = SYS_FCLK_HZ,
        .nco = ((uint64_t)(SYS_FCLK_HZ / 20U) << (NCO_WIDTH + 4)) / SYS_FCLK_HZ,
    };
    if (uart_init(&uart) != kErrorOk) return 1;

    REFs_calibrate(IREF_DEFAULT_CAL, IREF1);
    REFs_calibrate(VREF_DEFAULT_CAL, VREF);
    iDAC1_calibrate(IDAC_DEFAULT_CAL);
    iDACs_enable(true, false);

    timer_cycles_init();
    timer_start();
    if (vco_set_clock_config(SYS_FCLK_HZ, 1U) != VCO_STATUS_OK) {
        _writestr("[i] VCO clock setup failed: check MCU frequency and rebuild.\n");
        return 1;
    }

    gsr_controller_t controller = {0};
    gsr_status_t status = gsr_set_default_settings(&controller);
    if (status != GSR_STATUS_OK) {
        return 1;
    }
    uint32_t current_nA = gsr_injected_current_nA;
    controller.config.channel = VCO_CHANNEL_DIFFERENTIAL;
    controller.config.baseline_refresh_rate_Hz = VCO_FS_HZ;
    controller.config.phasic_refresh_rate_Hz = VCO_FS_HZ;
    controller.config.recovery_refresh_rate_Hz = VCO_FS_HZ;
    controller.config.current_refresh_rate_Hz = VCO_FS_HZ;
    controller.config.duty_cycle_code = 1U;
    controller.config.idac_code = current_nA / IDAC_LSB_NA;
    controller.config.M = 1U;
    status = gsr_controller_init(&controller);
    if (status != GSR_STATUS_OK) {
        _writestr("[i] GSR init failed: lower sample rate and rebuild.\n");
        return 1;
    }

    uint32_t sample_rate_millihz = gsr_sample_rate_millihz;
    uint32_t index = 0U;
    current_command_t command = {0};
    uint32_t last_output_cycle = timer_get_cycles();
    uint32_t output_period_cycles =
        (uint32_t)(((uint64_t)SYS_FCLK_HZ * 1000U) / sample_rate_millihz);
    uint32_t last_valid_cycle = last_output_cycle;
    uint32_t last_feedback_cycle = last_output_cycle;
    bool signal_warning = false;
    const char *no_signal = "[i] No signal: check resistor/electrodes; lower sample rate if VCO counts are sparse.\n";
    const char *feedback = no_signal;
    print_setting("Current set: ", current_nA, " nA\n");
    print_setting("Sampling set: ", sample_rate_millihz, " mHz\n");
    _writestr("[i] GSR demo ready.\n");
    while (1) {
        poll_current_command(&uart, &command);
        uint32_t requested_rate_millihz = gsr_sample_rate_millihz;
        if (requested_rate_millihz != sample_rate_millihz) {
            uint32_t refresh_rate_Hz = requested_rate_millihz < 1000U ? 1U : requested_rate_millihz / 1000U;
            uint64_t period_cycles = requested_rate_millihz == 0U ? UINT64_MAX :
                ((uint64_t)SYS_FCLK_HZ * 1000U) / requested_rate_millihz;
            if (requested_rate_millihz < 100U || requested_rate_millihz > 10000000U ||
                (requested_rate_millihz >= 1000U && requested_rate_millihz % 1000U != 0U) ||
                refresh_rate_Hz > SYS_FCLK_HZ / 100U ||
                (requested_rate_millihz < 1000U && period_cycles > UINT32_MAX)) {
                gsr_sample_rate_millihz = sample_rate_millihz;
                _writestr("[i] Sampling change failed: outside timer/rate limits.\n");
            } else {
                controller.config.baseline_refresh_rate_Hz = refresh_rate_Hz;
                controller.config.phasic_refresh_rate_Hz = refresh_rate_Hz;
                controller.config.recovery_refresh_rate_Hz = refresh_rate_Hz;
                status = gsr_controller_set_config(&controller);
                if (status == GSR_STATUS_OK) {
                    sample_rate_millihz = requested_rate_millihz;
                    output_period_cycles = (uint32_t)period_cycles;
                    last_output_cycle = timer_get_cycles();
                    print_setting("Sampling set: ", sample_rate_millihz, " mHz\n");
                } else {
                    uint32_t old_refresh_rate = sample_rate_millihz < 1000U ? 1U : sample_rate_millihz / 1000U;
                    controller.config.baseline_refresh_rate_Hz = old_refresh_rate;
                    controller.config.phasic_refresh_rate_Hz = old_refresh_rate;
                    controller.config.recovery_refresh_rate_Hz = old_refresh_rate;
                    gsr_controller_set_config(&controller);
                    gsr_sample_rate_millihz = sample_rate_millihz;
                    _writestr("[i] Sampling change failed: VCO setup rejected.\n");
                }
            }
        }
        uint32_t requested_nA = gsr_injected_current_nA;
        if (requested_nA != current_nA) {
            if (requested_nA < IDAC_LSB_NA || requested_nA > IDAC_LSB_NA * IDAC_MAX_CODE ||
                requested_nA % IDAC_LSB_NA != 0U) {
                gsr_injected_current_nA = current_nA;
                _writestr("[i] Current change failed: use 40 nA steps (40..10200).\n");
                continue;
            }
            controller.config.idac_code = requested_nA / IDAC_LSB_NA;
            status = gsr_controller_set_config(&controller);
            uint32_t dac_code = mmio_region_read32(
                mmio_region_from_addr((uintptr_t)IDAC_CTRL_START_ADDRESS),
                IDAC_CTRL_CURRENT_REG_OFFSET) & IDAC_CTRL_CURRENT_CURRENT_1_MASK;
            if (status == GSR_STATUS_OK && dac_code == controller.config.idac_code) {
                current_nA = requested_nA;
                print_setting("Current set: ", current_nA, " nA\n");
            } else {
                controller.config.idac_code = current_nA / IDAC_LSB_NA;
                gsr_controller_set_config(&controller);
                gsr_injected_current_nA = current_nA;
                _writestr("[i] Current change failed: iDAC readback mismatch.\n");
            }
        }
        vco_pair_sample_t pair = {0};
        status = gsr_controller_read_pair(&controller, &pair);
        uint32_t now = timer_get_cycles();
        if (status != GSR_STATUS_OK) {
            if (status != GSR_STATUS_NO_NEW_SAMPLE)
                feedback = acquisition_feedback(status, &pair);
            // Startup/stale reads are normal. Warn only after sustained failure,
            // then repeat at most once per three seconds, even in a tight loop.
            if ((uint32_t)(now - last_valid_cycle) >= FEEDBACK_PERIOD_CC &&
                (uint32_t)(now - last_feedback_cycle) >= FEEDBACK_PERIOD_CC) {
                _writestr(feedback);
                last_feedback_cycle = now;
                signal_warning = true;
            }
            continue;
        }
        last_valid_cycle = now;
        feedback = no_signal;
        if (signal_warning) {
            _writestr("[i] Signal recovered.\n");
            signal_warning = false;
        }

        if (sample_rate_millihz < 1000U) {
            if ((uint32_t)(now - last_output_cycle) < output_period_cycles) continue;
            last_output_cycle += output_period_cycles;
        }

        print_sample(index, pair.p_Hz, pair.n_Hz, controller.sample.current_nA);
        index++;
    }
}
