// Copyright 2026 EPFL.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "dma.h"
#include "core_v_mini_mcu.h"

#define ESA_DMA_CHANNEL 1
#define TEST_WORDS 16
#define DMA_TIMEOUT 1000000u

static uint32_t source[TEST_WORDS] __attribute__((aligned(4))) = {
    0, 1, 2, 3, 0x12345678, 0xffffffff, 0x7fffffff, 0x80000000,
    0xabcdef01, 10, 20, 30, 40, 50, 60, 70
};
static uint32_t destination[TEST_WORDS] __attribute__((aligned(4)));

int main(void)
{
    printf("ESA HW FIFO test starting\n");

    dma_target_t src = {
        .ptr = (uint8_t *)source,
        .inc_d1_du = 1,
        .trig = DMA_TRIG_MEMORY,
        .type = DMA_DATA_TYPE_WORD,
    };
    dma_target_t dst = {
        .ptr = (uint8_t *)destination,
        .inc_d1_du = 1,
        .trig = DMA_TRIG_MEMORY,
        .type = DMA_DATA_TYPE_WORD,
    };
    dma_trans_t trans = {
        .src = &src,
        .dst = &dst,
        .size_d1_du = TEST_WORDS,
        .dim = DMA_DIM_CONF_1D,
        .mode = DMA_TRANS_MODE_SINGLE,
        .end = DMA_TRANS_END_POLLING,
        .hw_fifo_en = 1,
        .channel = ESA_DMA_CHANNEL,
    };

    dma_init(NULL);
    printf("DMA initialized\n");
    if (dma_validate_transaction(&trans, DMA_ENABLE_REALIGN,
                                 DMA_PERFORM_CHECKS_INTEGRITY) != DMA_CONFIG_OK ||
        dma_load_transaction(&trans) != DMA_CONFIG_OK ||
        dma_launch(&trans) != DMA_CONFIG_OK) {
        printf("ESA DMA setup failed\n");
        return EXIT_FAILURE;
    }
    printf("DMA transaction launched\n");

    uint32_t timeout = DMA_TIMEOUT;
    while (!dma_is_ready(ESA_DMA_CHANNEL) && timeout != 0) {
        --timeout;
    }
    if (timeout == 0) {
        printf("ESA DMA timed out\n");
        return EXIT_FAILURE;
    }

    for (uint32_t i = 0; i < TEST_WORDS; ++i) {
        // With reset-default zero SES shifts, the two averages are pass-through.
        // The HPF is x[n] - x[n-1], saturated to +/-INT32_MAX, then abs is taken.
        int64_t difference = (int64_t)(int32_t)source[i] -
                             (i == 0 ? 0 : (int32_t)source[i - 1]);
        if (difference > 0x7fffffffLL) difference = 0x7fffffffLL;
        if (difference < -0x7fffffffLL) difference = -0x7fffffffLL;
        int32_t signed_hpf = (int32_t)difference;
        uint32_t expected = signed_hpf < 0 ? (uint32_t)(-signed_hpf)
                                           : (uint32_t)signed_hpf;
        if (destination[i] != expected) {
            printf("ESA mismatch at %lu: got 0x%08lx, expected 0x%08lx\n",
                   (unsigned long)i, (unsigned long)destination[i],
                   (unsigned long)expected);
            return EXIT_FAILURE;
        }
    }

    printf("ESA HW FIFO high-pass feature test passed\n");
    return EXIT_SUCCESS;
}
