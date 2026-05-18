// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// File: test_GSR_op_controller/main.c
// Author: Ismail Essaidi
// Date: 13/04/2026
// Description: Linear request-level test for the GSR operating-point controller.

#include <stdint.h>
#include <stdio.h>

#include "GSR_op_controller.h"
#include "REFs_ctrl.h"
#include "iDAC_ctrl.h"
#include "soc_ctrl.h"
#include "timer_sdk.h"
#include "x-heep.h"

#define PRINTF_IN_SIM   0
#define PRINTF_IN_FPGA  0
#define TARGET_SIM      1

#if TARGET_SIM && PRINTF_IN_SIM
#define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
#define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#else
#define PRINTF(...)
#endif

#define SYS_FCLK_HZ          10000000U
#define IREF_DEFAULT_CAL     255U
#define VREF_DEFAULT_CAL     0b1111111111U
#define IDAC_DEFAULT_CAL     15U
#define VCO_ACCEL_RATIO      100U
#define SAMPLE_ATTEMPT_LIMIT 16U
#define N_READ_STEPS         2U

volatile uint32_t debug __attribute__((section(".xheep_debug_mem")));

void __attribute__((aligned(4), interrupt)) handler_irq_timer(void) {
    vco_handle_timer_irq();
    debug = 'wake';
}

static void debug_mark(uint8_t tag, uint32_t value) {
    debug = ((uint32_t)tag << 24) | (value & 0x00FFFFFFUL);
}

static void hw_init(void) {
    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);
    soc_ctrl_set_frequency(&soc_ctrl, SYS_FCLK_HZ);

    REFs_calibrate(IREF_DEFAULT_CAL, IREF1);
    REFs_calibrate(VREF_DEFAULT_CAL, VREF);

    iDACs_enable(true, false);
    iDAC1_calibrate(IDAC_DEFAULT_CAL);

    enable_timer_interrupt();
    timer_irq_enable();
    timer_cycles_init();
    timer_start();
}

static int init_stack(gsr_controller_t *controller, gsr_op_controller_t *opctrl) {
    gsr_status_t st;

    st = gsr_set_default_settings(controller);
    if (st != GSR_STATUS_OK) {
        debug_mark(0xE1U, (uint32_t)st);
        return -1;
    }

    st = gsr_controller_init(controller);
    if (st != GSR_STATUS_OK) {
        debug_mark(0xE2U, (uint32_t)st);
        return -1;
    }

    if (gsr_opctrl_init(opctrl, controller) != GSR_OPCTRL_OK) {
        debug_mark(0xE3U, 0U);
        return -1;
    }

    return 0;
}

static uint32_t refresh_wait_cycles(uint32_t refresh_rate_Hz) {
#if TARGET_SIM
    return SYS_FCLK_HZ / (VCO_ACCEL_RATIO * refresh_rate_Hz);
#else
    return SYS_FCLK_HZ / refresh_rate_Hz;
#endif
}

static void wait_cycles_busy(uint32_t cycles) {
    uint32_t start = timer_get_cycles();
    while ((uint32_t)(timer_get_cycles() - start) < cycles) {
    }
}

int main(void) {
    gsr_controller_t controller;
    gsr_op_controller_t opctrl;
    gsr_controller_t planned;
    gsr_sample_t sample;
    gsr_opctrl_status_t opst;
    uint32_t attempts;
    uint32_t reads_done;
    uint32_t wait_cycles;

    gsr_op_request_t request_range_low = { .range = LOW, .resolution = LOW, .power = HIGH };
    gsr_op_request_t request_range_mid = { .range = MEDIUM, .resolution = LOW, .power = HIGH };
    gsr_op_request_t request_range_high = { .range = HIGH, .resolution = LOW, .power = HIGH };
    gsr_op_request_t request_resolution_mid = { .range = LOW, .resolution = MEDIUM, .power = HIGH };
    gsr_op_request_t request_resolution_high = { .range = LOW, .resolution = HIGH, .power = HIGH };
    gsr_op_request_t request_power_low = { .range = LOW, .resolution = LOW, .power = LOW };
    gsr_op_request_t request_power_mid = { .range = LOW, .resolution = LOW, .power = MEDIUM };

    debug_mark(0x01U, 0U);
    hw_init();

    if (init_stack(&controller, &opctrl) != 0) {
        return -1;
    }

    timer_wait_us(5700);

    /* Request 1: low range, low resolution, high power */
    debug_mark(0x10U, ((uint32_t)request_range_low.range << 16) |
                      ((uint32_t)request_range_low.resolution << 8) |
                      (uint32_t)request_range_low.power);
    opst = gsr_opctrl_request(&opctrl, &request_range_low, &planned);
    if (opst != GSR_OPCTRL_OK) {
        debug_mark(0xE4U, (uint32_t)opst);
        return -1;
    }
    wait_cycles = refresh_wait_cycles(controller.config.current_refresh_rate_Hz);
    reads_done = 0U;
    attempts = 0U;
    while (reads_done < N_READ_STEPS && attempts < SAMPLE_ATTEMPT_LIMIT) {
        opst = gsr_opctrl_read_sample(&opctrl, &sample);
        debug_mark(0x30U, (uint32_t)opst);
        if (opst == GSR_OPCTRL_OK) {
            debug_mark(0x40U, sample.G_nS);
            reads_done++;
        } else if (opst == GSR_OPCTRL_NOT_INITIALIZED ||
                   opst == GSR_OPCTRL_MEASUREMENT_ERROR) {
            wait_cycles_busy(wait_cycles);
        } else {
            debug_mark(0xE5U, (uint32_t)opst);
            return -1;
        }
        attempts++;
    }
    if (reads_done != N_READ_STEPS) {
        debug_mark(0xE6U, reads_done);
        return -1;
    }

    /* Request 2: medium range */
    debug_mark(0x11U, ((uint32_t)request_range_mid.range << 16) |
                      ((uint32_t)request_range_mid.resolution << 8) |
                      (uint32_t)request_range_mid.power);
    opst = gsr_opctrl_request(&opctrl, &request_range_mid, &planned);
    if (opst != GSR_OPCTRL_OK) {
        debug_mark(0xE7U, (uint32_t)opst);
        return -1;
    }
    wait_cycles = refresh_wait_cycles(controller.config.current_refresh_rate_Hz);
    reads_done = 0U;
    attempts = 0U;
    while (reads_done < N_READ_STEPS && attempts < SAMPLE_ATTEMPT_LIMIT) {
        opst = gsr_opctrl_read_sample(&opctrl, &sample);
        debug_mark(0x30U, (uint32_t)opst);
        if (opst == GSR_OPCTRL_OK) {
            debug_mark(0x40U, sample.G_nS);
            reads_done++;
        } else if (opst == GSR_OPCTRL_NOT_INITIALIZED ||
                   opst == GSR_OPCTRL_MEASUREMENT_ERROR) {
            wait_cycles_busy(wait_cycles);
        } else {
            debug_mark(0xE8U, (uint32_t)opst);
            return -1;
        }
        attempts++;
    }
    if (reads_done != N_READ_STEPS) {
        debug_mark(0xE9U, reads_done);
        return -1;
    }

    /* Request 3: high range */
    debug_mark(0x12U, ((uint32_t)request_range_high.range << 16) |
                      ((uint32_t)request_range_high.resolution << 8) |
                      (uint32_t)request_range_high.power);
    opst = gsr_opctrl_request(&opctrl, &request_range_high, &planned);
    if (opst != GSR_OPCTRL_OK) {
        debug_mark(0xEAU, (uint32_t)opst);
        return -1;
    }
    wait_cycles = refresh_wait_cycles(controller.config.current_refresh_rate_Hz);
    reads_done = 0U;
    attempts = 0U;
    while (reads_done < N_READ_STEPS && attempts < SAMPLE_ATTEMPT_LIMIT) {
        opst = gsr_opctrl_read_sample(&opctrl, &sample);
        debug_mark(0x30U, (uint32_t)opst);
        if (opst == GSR_OPCTRL_OK) {
            debug_mark(0x40U, sample.G_nS);
            reads_done++;
        } else if (opst == GSR_OPCTRL_NOT_INITIALIZED ||
                   opst == GSR_OPCTRL_MEASUREMENT_ERROR) {
            wait_cycles_busy(wait_cycles);
        } else {
            debug_mark(0xEBU, (uint32_t)opst);
            return -1;
        }
        attempts++;
    }
    if (reads_done != N_READ_STEPS) {
        debug_mark(0xECU, reads_done);
        return -1;
    }

    /* Request 4: medium resolution */
    debug_mark(0x13U, ((uint32_t)request_resolution_mid.range << 16) |
                      ((uint32_t)request_resolution_mid.resolution << 8) |
                      (uint32_t)request_resolution_mid.power);
    opst = gsr_opctrl_request(&opctrl, &request_resolution_mid, &planned);
    if (opst != GSR_OPCTRL_OK) {
        debug_mark(0xEDU, (uint32_t)opst);
        return -1;
    }
    wait_cycles = refresh_wait_cycles(controller.config.current_refresh_rate_Hz);
    reads_done = 0U;
    attempts = 0U;
    while (reads_done < N_READ_STEPS && attempts < SAMPLE_ATTEMPT_LIMIT) {
        opst = gsr_opctrl_read_sample(&opctrl, &sample);
        debug_mark(0x30U, (uint32_t)opst);
        if (opst == GSR_OPCTRL_OK) {
            debug_mark(0x40U, sample.G_nS);
            reads_done++;
        } else if (opst == GSR_OPCTRL_NOT_INITIALIZED ||
                   opst == GSR_OPCTRL_MEASUREMENT_ERROR) {
            wait_cycles_busy(wait_cycles);
        } else {
            debug_mark(0xEEU, (uint32_t)opst);
            return -1;
        }
        attempts++;
    }
    if (reads_done != N_READ_STEPS) {
        debug_mark(0xEFU, reads_done);
        return -1;
    }

    /* Request 5: high resolution */
    debug_mark(0x14U, ((uint32_t)request_resolution_high.range << 16) |
                      ((uint32_t)request_resolution_high.resolution << 8) |
                      (uint32_t)request_resolution_high.power);
    opst = gsr_opctrl_request(&opctrl, &request_resolution_high, &planned);
    if (opst != GSR_OPCTRL_OK) {
        debug_mark(0xF0U, (uint32_t)opst);
        return -1;
    }
    wait_cycles = refresh_wait_cycles(controller.config.current_refresh_rate_Hz);
    reads_done = 0U;
    attempts = 0U;
    while (reads_done < N_READ_STEPS && attempts < SAMPLE_ATTEMPT_LIMIT) {
        opst = gsr_opctrl_read_sample(&opctrl, &sample);
        debug_mark(0x30U, (uint32_t)opst);
        if (opst == GSR_OPCTRL_OK) {
            debug_mark(0x40U, sample.G_nS);
            reads_done++;
        } else if (opst == GSR_OPCTRL_NOT_INITIALIZED ||
                   opst == GSR_OPCTRL_MEASUREMENT_ERROR) {
            wait_cycles_busy(wait_cycles);
        } else {
            debug_mark(0xF1U, (uint32_t)opst);
            return -1;
        }
        attempts++;
    }
    if (reads_done != N_READ_STEPS) {
        debug_mark(0xF2U, reads_done);
        return -1;
    }

    /* Request 6: low power */
    debug_mark(0x15U, ((uint32_t)request_power_low.range << 16) |
                      ((uint32_t)request_power_low.resolution << 8) |
                      (uint32_t)request_power_low.power);
    opst = gsr_opctrl_request(&opctrl, &request_power_low, &planned);
    if (opst != GSR_OPCTRL_OK) {
        debug_mark(0xF3U, (uint32_t)opst);
        return -1;
    }
    reads_done = 0U;
    attempts = 0U;
    while (reads_done < N_READ_STEPS && attempts < SAMPLE_ATTEMPT_LIMIT) {
        opst = gsr_opctrl_read_sample(&opctrl, &sample);
        debug_mark(0x30U, (uint32_t)opst);

        if (opst == GSR_OPCTRL_OK) {
            debug_mark(0x40U, sample.G_nS);
            if (!sample.valid) {
                debug_mark(0xF5U, 0U);
                return -1;
            }
            reads_done++;
        } else if (opst != GSR_OPCTRL_MEASUREMENT_ERROR &&
                   opst != GSR_OPCTRL_NOT_INITIALIZED) {
            debug_mark(0xF4U, (uint32_t)opst);
            return -1;
        }
        attempts++;
    }
    if (reads_done != N_READ_STEPS) {
        debug_mark(0xF5U, reads_done);
        return -1;
    }

    /* Request 7: medium power */
    debug_mark(0x16U, ((uint32_t)request_power_mid.range << 16) |
                      ((uint32_t)request_power_mid.resolution << 8) |
                      (uint32_t)request_power_mid.power);
    opst = gsr_opctrl_request(&opctrl, &request_power_mid, &planned);
    if (opst != GSR_OPCTRL_OK) {
        debug_mark(0xF6U, (uint32_t)opst);
        return -1;
    }
    reads_done = 0U;
    attempts = 0U;
    while (reads_done < N_READ_STEPS && attempts < SAMPLE_ATTEMPT_LIMIT) {
        opst = gsr_opctrl_read_sample(&opctrl, &sample);
        debug_mark(0x30U, (uint32_t)opst);

        if (opst == GSR_OPCTRL_OK) {
            debug_mark(0x40U, sample.G_nS);
            if (!sample.valid) {
                debug_mark(0xF8U, 0U);
                return -1;
            }
            reads_done++;
        } else if (opst != GSR_OPCTRL_MEASUREMENT_ERROR &&
                   opst != GSR_OPCTRL_NOT_INITIALIZED) {
            debug_mark(0xF7U, (uint32_t)opst);
            return -1;
        }
        attempts++;
    }
    if (reads_done != N_READ_STEPS) {
        debug_mark(0xF8U, reads_done);
        return -1;
    }
    return 0;
}
