// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Synchronized P/N GSR acquisition for the desktop monitor.

#include <stdint.h>

#include "syscalls.h"
#include "VCO_sdk.h"
#include "REFs_ctrl.h"
#include "iDAC_ctrl.h"
#include "soc_ctrl.h"
#include "timer_sdk.h"
#include "uart.h"
#include "uart_regs.h"

// Build values provide startup defaults; the GUI can update the volatile runtime words over JTAG.
#define SYS_FCLK_HZ 10000000
#define VCO_FS_HZ 10
#define VCO_SAMPLE_RATE_MILLIHZ 10000
#define INJECTED_CURRENT_NA 360

#define IDAC_LSB_NA 40
#define IDAC_MAX_CODE 255
#define IREF_DEFAULT_CAL 255
#define IDAC_DEFAULT_CAL 15
#define VREF_DEFAULT_CAL 1023
#ifndef GSR_DLC_ENABLED
#define GSR_DLC_ENABLED 0
#endif
#ifndef GSR_DLC_LOG_WIDTH
#define GSR_DLC_LOG_WIDTH 11
#endif
#ifndef GSR_DLC_TIME_BITS
#define GSR_DLC_TIME_BITS 5
#endif

// 0: hardware register; 1: chip software; 2: host GUI.
#ifndef GSR_DIFFERENTIAL_MODE
#define GSR_DIFFERENTIAL_MODE 0
#endif
#ifndef VCO_SUPPLY_RATE_MILLIHZ
#define VCO_SUPPLY_RATE_MILLIHZ 0
#endif
#if GSR_DIFFERENTIAL_MODE < 0 || GSR_DIFFERENTIAL_MODE > 2
#error "Differential mode must be hardware (0), software (1), or GUI (2)"
#endif
#if GSR_DLC_ENABLED && GSR_DIFFERENTIAL_MODE == 2
#error "GUI differential mode does not support dLC"
#endif
#if GSR_DLC_ENABLED
#define GSR_USE_DLC
#endif
#define FEEDBACK_PERIOD_CC ((SYS_FCLK_HZ <= UINT32_MAX / 3U) ? (SYS_FCLK_HZ * 3U) : UINT32_MAX)

// The GUI updates these words over JTAG; firmware rereads them in the acquisition loop.
volatile uint32_t gsr_injected_current_nA = INJECTED_CURRENT_NA;
volatile uint32_t gsr_sample_rate_millihz = VCO_SAMPLE_RATE_MILLIHZ;
// Zero follows the signal sampling rate; nonzero selects independent P integration.
volatile uint32_t gsr_supply_rate_millihz = VCO_SUPPLY_RATE_MILLIHZ;

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

static void number(uint32_t value) {
    char text[11]; *append_u32(text, value) = '\0'; _writestr(text);
}
static void field(uint32_t value) { _writestr(","); number(value); }
static void signed_field(int32_t value) {
    _writestr(",");
    if (value < 0) _writestr("-");
    number(value < 0 ? (uint32_t)(-(int64_t)value) : (uint32_t)value);
}

static void print_setting(const char *name, uint32_t value, const char *unit) {
    char number[11];
    *append_u32(number, value) = '\0';
    _writestr("[i] ");
    _writestr(name);
    _writestr(number);
    _writestr(unit);
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

#include "acquisition_demo.h"

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
    _writestr("\n"); // terminate any partial UART line left by the previous image

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

    run_acquisition(&uart);
    _writestr("[i] Acquisition stopped: reset board and start recording again.\n");
    // Returning from main would reenter startup with live peripheral state.
    while (1) __asm__ volatile ("wfi");
}
