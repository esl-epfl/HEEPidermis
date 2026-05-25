// Copyright 2024 EPFL
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: test_reconstruct_dlc/main.c
// Author: Omar Shibli
// Description: Tests event-based GSR reconstruction using the dLC pipeline.
//              Uses GSR_sdk (compiled with GSR_USE_VCO_DLC) to get conductance
//              directly from dLC events.
//              Output format matches test_reconstruction for easy comparison.

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
#include "DLC_sdk.h"
#include "GSR_controller.h"

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
// dLC configuration
#define DLC_LOG_LVL_W      5            // level width = 128 counts
#define DLC_INPUT_SAMPLES  20         // samples per transaction → 200 ms
#define DLC_BUF_SIZE       DLC_INPUT_SAMPLES

// Stop after this many transactions
#define WINDOWS_TO_PROCESS 15

#define INTR_DMA_TRANS_DONE  (1 << 19)
#define INTR_DMA_WINDOW_DONE (1 << 30)

volatile uint32_t debug __attribute__((section(".xheep_debug_mem")));

static uint8_t dlc_buf[DLC_BUF_SIZE];

volatile int32_t g_trans_flag   = 0;
volatile int32_t g_window_flag  = 0;

void dma_intr_handler_trans_done(uint8_t channel) {
    if (channel == 0) g_trans_flag++;
}

void dma_intr_handler_window_done(uint8_t channel) {
    if (channel == 0) g_window_flag++;
}

// Suppress the DMA window-ratio warning
uint8_t dma_window_ratio_warning_threshold(void) { return 0; }

void __attribute__((aligned(4), interrupt)) handler_irq_timer(void) {
    // timer_arm_stop();
    timer_irq_clear();
    // timer_start();
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
static gsr_status_t set_default_settings(gsr_controller_t *ctrl) {

    if (ctrl == 0) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    ctrl->config.channel = VCO_CHANNEL_P;
    ctrl->config.duty_cycle_code = 1; // 100% duty cycle
    ctrl->config.M = 1; // no oversampling by default, just take one measurement per sample. This can be increased for more noisy environments at the cost of temporal resolution and power consumption.
    ctrl->config.baseline_refresh_rate_Hz = 100;
    ctrl->config.phasic_refresh_rate_Hz = 10;
    ctrl->config.recovery_refresh_rate_Hz = 5;
    ctrl->config.idac_code = 7;
    ctrl->config.current_refresh_rate_Hz = ctrl->config.baseline_refresh_rate_Hz; // initialize the current refresh rate to the baseline rate
    ctrl->amplitude_threshold_nS = 80;
    ctrl->slope_threshold_nS = 40;
    ctrl->settle_threshold_nS = 25;
    ctrl->recovery_count_required = 8;

    ctrl->dlc_used = false;

    return GSR_STATUS_OK;
}

static int init_controller(gsr_controller_t *ctrl, gsr_dlc_config_t *gsr_dlc_cfg) {
    gsr_status_t st;

    st = set_default_settings(ctrl);
    if (st != GSR_STATUS_OK) {
        debug_mark(0xE1U, (uint32_t)st);
        return -1;
    }
    
    // we use dlc
    ctrl->dlc_used = true;
    ctrl->dlc_cfg = *gsr_dlc_cfg;

    st = gsr_controller_init(ctrl);
    if (st != GSR_STATUS_OK) {
        debug_mark(0xE2U, (uint32_t)st);
        return -1;
    }

    return 0;
}

// Process one transaction window.
static int process_window(gsr_controller_t *ctrl) {
    int valid = 0;
    const gsr_sample_t *sample;
    gsr_status_t st;

    for (int i = 0; i < DLC_BUF_SIZE; i++) {
        uint32_t conductance_nS = 0;
        // st = gsr_read_sample(ctrl);  // attempt tap/read after event
        gsr_status_t st = gsr_get_conductance_nS(&conductance_nS, 0);

        if (st == GSR_STATUS_OK) {
            // sample = gsr_get_last_sample(ctrl);
            // if (sample == NULL || !sample->valid) {
            //     debug_mark((uint8_t)(0xE1), 0U);
            //     return -1;
            // }
            // debug_mark(0, sample->G_nS);
            debug_mark(0, conductance_nS);
            valid++;
        }
        // NO_NEW_SAMPLE and MISSED_UPDATE skipped
    }

    return valid;
}

int main(void) {

    gsr_controller_t ctrl;

    debug = 'Init';
    hw_init();

    // Clear event buffer
    memset(dlc_buf, 0, sizeof(dlc_buf));

    dlc_config_t dlc_cfg = {
        .log_level_width = DLC_LOG_LVL_W,
        .dlvl_format     = 0,            /* sign-magnitude */
        .hysteresis_en   = 1,
    };

    gsr_dlc_config_t gsr_dlc_cfg = {
        .dlc_cfg       = &dlc_cfg,
        .results_buf   = dlc_buf,
        .buf_size      = DLC_BUF_SIZE,
        .input_samples = DLC_INPUT_SAMPLES,
    };


    if (init_controller(&ctrl, &gsr_dlc_cfg) != 0) {
        return -1;
    }

    // Enable interrupts and run the processing loop.
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    CSR_SET_BITS(CSR_REG_MIE, INTR_DMA_TRANS_DONE | INTR_DMA_WINDOW_DONE);

    int total_windows = 0;
    int total_samples = 0;

    while (total_windows < WINDOWS_TO_PROCESS) {
        CSR_CLEAR_BITS(CSR_REG_MSTATUS, 0x8);
        if (g_trans_flag == 0) wait_for_interrupt();
        CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

        if (g_trans_flag > 0) {
            g_trans_flag--;
            total_samples += process_window(&ctrl);
            total_windows++;
            memset(dlc_buf, 0, sizeof(dlc_buf));
        }

        if (g_window_flag > 0) g_window_flag--;
    }

    dma_stop_circular(0);
    iDACs_enable(false, false);

    debug_mark(0xFFU, total_samples);

    return EXIT_SUCCESS;
}
