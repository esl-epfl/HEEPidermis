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


#define PRINTF_IN_SIM  1
#define PRINTF_IN_FPGA 1


#if TARGET_SIM && PRINTF_IN_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#else
    #define PRINTF(...)
#endif


#define SYS_FCLK_HZ        10000000
#define VCO_FS_HZ          500          // VCO sampling rate
#define IDAC_DEFAULT_CODE  7            // iDAC code → I = 40×7 = 280 nA
#define IREF_DEFAULT_CAL   255
#define IDAC_DEFAULT_CAL   15

// dLC configuration
#define DLC_LOG_LVL_W      7            // level width = 128 counts
#define DLC_INPUT_SAMPLES  100          // samples per transaction → 200 ms
#define DLC_BUF_SIZE       DLC_INPUT_SAMPLES

// Stop after this many transactions
#define WINDOWS_TO_PROCESS 15

#define INTR_DMA_TRANS_DONE  (1 << 19)
#define INTR_DMA_WINDOW_DONE (1 << 30)


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

// Hardware init
static void hw_init(void) {
    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);
    soc_ctrl_set_frequency(&soc_ctrl, SYS_FCLK_HZ);

    timer_cycles_init();

    REFs_calibrate(IREF_DEFAULT_CAL, IREF1);
    REFs_calibrate(0b1111111111, VREF);

    iDACs_enable(true, false);
    iDAC1_calibrate(IDAC_DEFAULT_CAL);

    enable_timer_interrupt();
    timer_irq_enable();
    timer_start();
}

// Process one transaction window.
static int process_window(void) {
    int valid = 0;

    for (int i = 0; i < DLC_BUF_SIZE; i++) {
        uint32_t conductance_nS = 0;
        gsr_status_t st = gsr_get_conductance_nS(&conductance_nS, 0);

        if (st == GSR_STATUS_OK) {
            PRINTF("%lu\n", conductance_nS);
            valid++;
        }
        // NO_NEW_SAMPLE and MISSED_UPDATE skipped
    }

    return valid;
}

int main(void) {

    PRINTF("HELLO\n");
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

    gsr_status_t st = gsr_init_dlc(VCO_CHANNEL_P, VCO_FS_HZ, IDAC_DEFAULT_CODE, &gsr_dlc_cfg);

    if (st != GSR_STATUS_OK) {
        PRINTF("ERROR: gsr_init failed (%d)\n", (int)st);
        return EXIT_FAILURE;
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
            total_samples += process_window();
            total_windows++;
            memset(dlc_buf, 0, sizeof(dlc_buf));
        }

        if (g_window_flag > 0) g_window_flag--;
    }

    dma_stop_circular(0);
    iDACs_enable(false, false);

    PRINTF("done: %d windows, %d conductance samples\n",
           total_windows, total_samples);

    return EXIT_SUCCESS;
}
