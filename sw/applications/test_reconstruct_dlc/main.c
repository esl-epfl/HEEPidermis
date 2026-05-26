// Copyright 2024 EPFL
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: test_reconstruct_dlc/main.c
// Author: Omar Shibli
// Description: Tests event-based GSR capture using the dLC pipeline.
//              The chip dumps raw packed dLC packets; reconstruction happens
//              off-chip in gsr_eval_dlc/process_dlc.py.

#include <stdio.h>
#include <stdlib.h>

#include "dma.h"
#include "core_v_mini_mcu.h"
#include "x-heep.h"
#include "cheep.h"
#include "csr.h"
#include "fast_intr_ctrl.h"
#include "hart.h"
#include "timer_sdk.h"
#include "soc_ctrl.h"
#include "REFs_ctrl.h"
#include "iDAC_ctrl.h"
#include "DLC_sdk.h"
#include "VCO_dlc_sdk.h"


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
#define VCO_FS_HZ          100          // VCO sampling rate
#define VCO_SIM_RATE_MULTIPLIER 100
#define CAPTURE_INPUT_SAMPLES 2000
#define IDAC_DEFAULT_CODE  45            // iDAC code -> I = 40 x code nA
#define IREF_DEFAULT_CAL   255
#define IDAC_DEFAULT_CAL   15

// dLC configuration
#define DLC_LOG_LVL_W      8            // level width = 256 counts
#define DLC_BUF_SIZE       2048         // captured paced DMA slots
#define DLC_INPUT_SAMPLES  CAPTURE_INPUT_SAMPLES
#define DLC_DMA_CHANNEL    0
#define DLC_HEX_BYTES_PER_LINE 32

#define INTR_DMA_TRANS_DONE  (1u << 19)
#define INTR_DMA_WINDOW_DONE (1u << 30)
#define INTR_EXTERNAL        (1u << 31)


static volatile uint8_t dlc_buf[DLC_BUF_SIZE];

volatile int32_t g_event_flag = 0;

void fic_irq_ext_peripheral(void) {
    g_event_flag++;
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
    iDACs_set_currents(IDAC_DEFAULT_CODE, 0);

    timer_start();
}

static void raw_dlc_clear_buffer(void) {
    for (uint16_t i = 0; i < DLC_BUF_SIZE; i++) {
        dlc_buf[i] = 0;
    }
}

static uint32_t capture_refresh_cycles(void) {
#if TARGET_SIM
    return SYS_FCLK_HZ / (VCO_SIM_RATE_MULTIPLIER * VCO_FS_HZ);
#else
    return SYS_FCLK_HZ / VCO_FS_HZ;
#endif
}

static void raw_dlc_wait_cycles(uint32_t cycles) {
    uint32_t start = timer_get_cycles();

    while ((uint32_t)(timer_get_cycles() - start) < cycles) {
        asm volatile("nop");
    }
}

static uint32_t count_raw_dlc_event_bytes(void) {
    uint32_t valid = 0;

    for (uint32_t i = 0; i < DLC_BUF_SIZE; i++) {
        if (dlc_buf[i] != 0) {
            valid++;
        }
    }

    return valid;
}

static void dump_raw_dlc_buffer(void) {
    static const char hex[] = "0123456789abcdef";
    char line[(DLC_HEX_BYTES_PER_LINE * 2u) + 1u];
    uint32_t col = 0;

    for (uint32_t i = 0; i < DLC_BUF_SIZE; i++) {
        uint8_t packed_event = dlc_buf[i];
        if (packed_event == 0) {
            continue;
        }

        line[(2u * col) + 0u] = hex[(packed_event >> 4) & 0x0fu];
        line[(2u * col) + 1u] = hex[packed_event & 0x0fu];
        col++;

        if (col == DLC_HEX_BYTES_PER_LINE) {
            line[DLC_HEX_BYTES_PER_LINE * 2u] = '\0';
            PRINTF("HEX %s\n", line);
            col = 0;
        }
    }

    if (col > 0) {
        line[col * 2u] = '\0';
        PRINTF("HEX %s\n", line);
    }
}

int main(void) {

    hw_init();

    // Clear event buffer
    raw_dlc_clear_buffer();

    dlc_config_t dlc_cfg = {
        .log_level_width = DLC_LOG_LVL_W,
        .discard_bits    = 0,
        .dlvl_format     = 0,            /* sign-magnitude */
        .hysteresis_en   = 0,
    };

    vco_status_t st = vco_dlc_initialize(
        VCO_CHANNEL_P,
        VCO_FS_HZ,
        &dlc_cfg,
        (uint8_t *)dlc_buf,
        DLC_BUF_SIZE,
        DLC_INPUT_SAMPLES
    );

    if (st != VCO_STATUS_OK) {
        PRINTF("ERROR: vco_dlc_initialize failed (%d)\n", (int)st);
        return EXIT_FAILURE;
    }

    /*
     * Do not print while acquisition is live. UART output is slow enough to let
     * the dLC DMA overwrite a circular buffer, which corrupts reconstruction.
     * Capture a bounded source-sample window, stop the VCO trigger source, then
     * dump the packed nonzero dLC event bytes from the buffer.
     */
    raw_dlc_wait_cycles(capture_refresh_cycles() * (DLC_INPUT_SAMPLES + 8u));
    (void)vco_enable(VCO_CHANNEL_P, false);
    dma_stop_circular(DLC_DMA_CHANNEL);
    raw_dlc_wait_cycles(capture_refresh_cycles() * 4u);

    uint32_t valid_event_bytes = count_raw_dlc_event_bytes();

    PRINTF("SES_CFG none\n");
    PRINTF("DLC_CFG VCO_FS_HZ=%u SIM_RATE_MULTIPLIER=%u SAMPLE_RATE_HZ=%u "
           "LOG_LEVEL_WIDTH=%u DLVL_BITS=2 DT_BITS=6 FORMAT=sign_magnitude "
           "BYTE_ORDER=little INITIAL_LEVEL=%ld IDAC_CODE=%u "
           "VCO_INPUT_DISCARD_BITS=%u HYSTERESIS=0 CAPTURE_SAMPLES=%u "
           "VALID_EVENT_BYTES=%lu\n",
           VCO_FS_HZ, VCO_SIM_RATE_MULTIPLIER,
           VCO_FS_HZ * VCO_SIM_RATE_MULTIPLIER, DLC_LOG_LVL_W,
           (long)vco_dlc_get_current_level(), IDAC_DEFAULT_CODE,
           vco_dlc_get_input_discard_bits(), DLC_BUF_SIZE,
           (unsigned long)valid_event_bytes);

    dump_raw_dlc_buffer();

    return EXIT_SUCCESS;
}
