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

// Stop after this many transactions
#define WINDOWS_TO_PROCESS 6

#define INTR_DMA_TRANS_DONE  (1 << 19)
#define INTR_DMA_WINDOW_DONE (1 << 30)

volatile uint32_t debug __attribute__((section(".xheep_debug_mem")));


volatile int32_t g_window_flag  = 0;

#define RAW_INPUT_SAMPLES  5U
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
    timer_irq_clear();
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

static int init_controller(gsr_controller_t *ctrl, gsr_dma_acq_t *dma) {
    gsr_status_t st;

    st = set_default_settings(ctrl, dma);
    if (st != GSR_STATUS_OK) {
        debug_mark(0xE1U, (uint32_t)st);
        return -1;
    }
    
    debug = 'Cfg0';

    st = gsr_controller_init(ctrl);
    if (st != GSR_STATUS_OK) {
        debug_mark(0xE2U, (uint32_t)st);
        return -1;
    }
    debug = 'Dma0';

    return 0;
}

static int process_window(gsr_controller_t *ctrl) {
    gsr_status_t ret;
    const gsr_sample_t *sample;

    ret = gsr_read_sample(ctrl);
    if (ret == GSR_STATUS_OK) {
        sample = gsr_get_last_sample(ctrl);
        if (sample == NULL || !sample->valid) {
            debug = (0xF7 << 24);
            return -1;
        }
        debug_mark(0 ,sample->G_nS);
        debug_mark(0xE1U, get_valid_samples(ctrl));
    } else if (ret == GSR_STATUS_MISSED_UPDATE) {
        debug_mark(0xE3U, (uint32_t)ret);
    } else if (ret != GSR_STATUS_NO_NEW_SAMPLE) {
        debug_mark(0xE4U, (uint32_t)ret);
    } else {
        debug_mark(0xF1U, (uint32_t)ret);
    }
    return 0;
}

int main(void) {

    gsr_controller_t ctrl;
    const gsr_sample_t *sample;
    gsr_status_t ret;

    // Clear event buffer
    memset(buf_a, 0, sizeof(buf_a));
    memset(buf_b, 0, sizeof(buf_b));

    debug = 'Init';
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

    if (init_controller(&ctrl, &gsr_dma) != 0) {
        return -1;
    }
    debug = 'Star';

    int total_windows = 0;
    int total_samples = 0;
    // test 1: simple read and process window
    while (total_windows < WINDOWS_TO_PROCESS) {
        if (process_window(&ctrl) != 0) {
            return -1;
        }
        total_windows++;
    }

    total_windows = 0;
    total_samples = 0;

    // test 2: change config (refresh rate)
    ctrl.mode = GSR_CTRL_MODE_PHASIC; // switch to baseline mode at the end just to check that mode switching works correctly in this flow where DMA is used for sampling and there is no duty cycling and the reading is event based. In this case we expect the controller to switch the VCO refresh rate to the baseline refresh rate but keep the duty cycle code unchanged.
    ret = gsr_controller_set_config(&ctrl);
    if (ret != GSR_STATUS_OK) {
        debug = (0xF2 << 24 | ret); 
        return -1;
    }
    while (total_windows < WINDOWS_TO_PROCESS) {
        if (process_window(&ctrl) != 0) {
            return -1;
        }
        total_windows++;
    }
    
    iDACs_enable(false, false);

    debug_mark(0xFFU, total_samples);

    return EXIT_SUCCESS;
}
