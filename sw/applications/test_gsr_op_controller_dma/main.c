// Copyright 2024 EPFL
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: test_gsr_controller_dlc/main.c
// Author: Ismail Essaidi
// Description: 

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dma.h"
#include "core_v_mini_mcu.h"
#include "x-heep.h"
#include "cheep.h"
#include "csr.h"
#include "hart.h"
#include "timer_sdk.h"
#include "soc_ctrl.h"
#include "REFs_ctrl.h"
#include "iDAC_ctrl.h"
#include "GSR_sdk.h"
#include "GSR_op_controller.h"

#define TARGET_SIM 1
#define PRINTF_IN_SIM  0
#define PRINTF_IN_FPGA 0


#if TARGET_SIM && PRINTF_IN_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#else
    #define PRINTF(...)
#endif


#define SYS_FCLK_HZ        10000000
#define IREF_DEFAULT_CAL   255
#define IDAC_DEFAULT_CAL   15
#define VREF_DEFAULT_CAL   0b1111111111U

// #define TEST_DUTY // uncomment if you want to test duty cycling

#ifndef TEST_DUTY
    #define WINDOWS_TO_PROCESS 10
    #define RAW_INPUT_SAMPLES  3U
#else
    #define WINDOWS_TO_PROCESS 6
    #define RAW_INPUT_SAMPLES  5U
#endif

#define INTR_DMA_TRANS_DONE  (1 << 19)
#define INTR_DMA_WINDOW_DONE (1 << 30)

volatile uint32_t debug __attribute__((section(".xheep_debug_mem")));

volatile int32_t g_window_flag  = 0;

#define RAW_BUF_SIZE       RAW_INPUT_SAMPLES

static uint32_t buf_a[RAW_BUF_SIZE];
static uint32_t buf_b[RAW_BUF_SIZE];
static gsr_dma_acq_t gsr_dma;


static dma_target_t dma_src;
static dma_target_t dma_dst;
static dma_trans_t  dma_trans;

void dma_intr_handler_trans_done(uint8_t channel) {
    gsr_dma_intr_handler_trans_done(channel);
}

void dma_intr_handler_window_done(uint8_t channel) {
    if (channel == 0) g_window_flag++;
}

// Suppress the DMA window-ratio warning
uint8_t dma_window_ratio_warning_threshold(void) { return 0; }

void __attribute__((aligned(4), interrupt)) handler_irq_timer(void) {
    vco_handle_timer_irq();
}

static void debug_mark(uint8_t tag, uint32_t value) {
    debug = ((uint32_t)tag << 24) | (value & 0x00FFFFFFU);
}

// Hardware init
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

// Load a default controller configuration for standard GSR operation.
static gsr_status_t set_default_settings(gsr_controller_t *ctrl, gsr_dma_acq_t *dma) {

    if (ctrl == 0) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    ctrl->config.channel = VCO_CHANNEL_P;
    ctrl->config.duty_cycle_code = 1; // 100% duty cycle
    ctrl->config.M = 1; // no oversampling by default, just take one measurement per sample. This can be increased for more noisy environments at the cost of temporal resolution and power consumption.
    ctrl->config.baseline_refresh_rate_Hz = 20;
    ctrl->config.phasic_refresh_rate_Hz = 40;
    ctrl->config.recovery_refresh_rate_Hz = 5;
    ctrl->config.idac_code = 40;
    ctrl->config.current_refresh_rate_Hz = ctrl->config.baseline_refresh_rate_Hz; // initialize the current refresh rate to the baseline rate
    ctrl->amplitude_threshold_nS = 80;
    ctrl->slope_threshold_nS = 40;
    ctrl->settle_threshold_nS = 25;
    ctrl->recovery_count_required = 8;

    ctrl->dlc_used = false;

    ctrl->dma_used = true;
    ctrl->dma = dma;

    return GSR_STATUS_OK;
}

static int init_stack(gsr_controller_t *controller, gsr_op_controller_t *opctrl,  gsr_dma_acq_t *dma) {
    gsr_status_t st;

    st = set_default_settings(controller, dma);
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

static int process_window(gsr_op_controller_t *opctrl, gsr_sample_t *sample) {
    gsr_opctrl_status_t opst;

    opst = gsr_opctrl_read(opctrl, sample);
    if (opst == GSR_OPCTRL_OK) {
        if (sample == NULL || !sample->valid) {
            debug = (0xF7 << 24);
            return -1;
        }
        debug_mark(0xA1 , get_valid_samples(opctrl->operating_point));
        debug_mark(0 ,sample->G_nS);
    } else if (opst == GSR_OPCTRL_NOT_INITIALIZED ||
                   opst == GSR_OPCTRL_MEASUREMENT_ERROR ||
                   opst == GSR_OPCTRL_MEASUREMENT_UNDERFLOW ||
                   opst == GSR_OPCTRL_MEASUREMENT_OVERFLOW) {
        debug = (0xDEAD << 16) | ((uint8_t)opst & 0x000FU);
    }  else {
        debug_mark(0xEBU, (uint32_t)opst);
        return -1;
    }
    return 0;
}

int main(void) {
    gsr_controller_t controller;
    gsr_op_controller_t opctrl;
    gsr_controller_t planned;
    gsr_sample_t sample;
    gsr_opctrl_status_t opst;
    uint32_t attempts;

    gsr_op_request_t request_range_low = { .range = LOW, .resolution = LOW, .power = HIGH };
    gsr_op_request_t request_range_high = { .range = HIGH, .resolution = LOW, .power = HIGH };
    gsr_op_request_t request_power_low = { .range = HIGH, .resolution = LOW, .power = LOW };

    gsr_controller_t ctrl;
    gsr_status_t ret;

    // Clear event buffer
    memset(buf_a, 0, sizeof(buf_a));
    memset(buf_b, 0, sizeof(buf_b));

    hw_init();

    gsr_dma = (gsr_dma_acq_t){
        .enabled = true,
        .running = false,
        .buf_a = buf_a,
        .buf_b = buf_b,
        .samples_per_window = RAW_BUF_SIZE,
        .write_buf = buf_a,
        .completed_buf = NULL,
        .window_ready = false,
        .overrun = false,
    };

    if (init_stack(&controller, &opctrl, &gsr_dma) != 0) {
        return -1;
    }
    int total_windows = 0;
    int total_samples = 0;
    // test 1: simple read and process window
    /* Request 1: low range */
    opst = gsr_opctrl_request(&opctrl, &request_range_low, &planned);
    if (opst != GSR_OPCTRL_OK) {
        debug_mark(0xEAU, (uint32_t)opst);
        return -1;
    }
    while (total_windows < WINDOWS_TO_PROCESS) {
        if (process_window(&opctrl, &sample) != 0) {
            return -1;
        }
        total_samples += get_valid_samples(opctrl.operating_point);
        total_windows++;
    }

    total_windows = 0;
    /* Request 2: high range */
    opst = gsr_opctrl_request(&opctrl, &request_range_high, &planned);
    if (opst != GSR_OPCTRL_OK) {
        debug_mark(0xEAU, (uint32_t)opst);
        return -1;
    }
    while (total_windows < WINDOWS_TO_PROCESS) {
        if (process_window(&opctrl, &sample) != 0) {
            return -1;
        }
        total_samples += get_valid_samples(opctrl.operating_point);
        total_windows++;
    }

    #ifdef TEST_DUTY 
        total_windows = 0;
        /* Request 3: low power */
        opst = gsr_opctrl_request(&opctrl, &request_power_low, &planned);
        if (opst != GSR_OPCTRL_OK) {
            debug_mark(0xEAU, (uint32_t)opst);
            return -1;
        }
        while (total_windows < WINDOWS_TO_PROCESS) {
            if (process_window(&opctrl, &sample) != 0) {
                return -1;
            }
            total_samples += get_valid_samples(opctrl.operating_point);
            total_windows++;
        }
    #endif
    
    iDACs_enable(false, false);

    debug_mark(0xFFU, total_samples);

    return EXIT_SUCCESS;
}
