// Copyright EPFL contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include <stdio.h>
#include <stdlib.h>
#include "csr.h"
#include "hart.h"
#include "handler.h"
#include "core_v_mini_mcu.h"
#include "rv_timer.h"
#include "power_manager.h"
#include "soc_ctrl.h"
#include "rv_plic.h"
#include "rv_plic_regs.h"
#include "fast_intr_ctrl.h"
#include "gpio.h"

static rv_timer_t timer_0_1;
static rv_timer_t timer_2_3;
static const uint64_t kTickFreqHz = 1000 * 1000; // 1 MHz
int8_t intr_flag;

void handler_irq_timer(void)
{
    rv_timer_irq_enable(&timer_0_1, 0, 0, kRvTimerDisabled);
    intr_flag = 1;
}

void handler_irq_fast_timer_1(void)
{
    fast_intr_ctrl_t fast_intr_ctrl;
    fast_intr_ctrl.base_addr = mmio_region_from_addr((uintptr_t)FAST_INTR_CTRL_START_ADDRESS);
    rv_timer_irq_enable(&timer_0_1, 1, 0, kRvTimerDisabled);
    clear_fast_interrupt(&fast_intr_ctrl, kTimer_1_fic_e);

    intr_flag = 1;
}

void handler_irq_fast_timer_2(void)
{
    fast_intr_ctrl_t fast_intr_ctrl;
    fast_intr_ctrl.base_addr = mmio_region_from_addr((uintptr_t)FAST_INTR_CTRL_START_ADDRESS);
    rv_timer_irq_enable(&timer_2_3, 0, 0, kRvTimerDisabled);
    clear_fast_interrupt(&fast_intr_ctrl, kTimer_2_fic_e);

    intr_flag = 1;
}

void handler_irq_fast_timer_3(void)
{
    fast_intr_ctrl_t fast_intr_ctrl;
    fast_intr_ctrl.base_addr = mmio_region_from_addr((uintptr_t)FAST_INTR_CTRL_START_ADDRESS);
    rv_timer_irq_enable(&timer_2_3, 1, 0, kRvTimerDisabled);
    clear_fast_interrupt(&fast_intr_ctrl, kTimer_3_fic_e);

    intr_flag = 1;
}

int main(int argc, char *argv[])
{
    // Setup fast interrupt controller
    fast_intr_ctrl_t fast_intr_ctrl;
    fast_intr_ctrl.base_addr = mmio_region_from_addr((uintptr_t)FAST_INTR_CTRL_START_ADDRESS);

    // Get current Frequency
    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);
    uint32_t freq_hz = soc_ctrl_get_frequency(&soc_ctrl);

    // Setup rv_timer_0_1
    mmio_region_t timer_0_1_reg = mmio_region_from_addr(RV_TIMER_AO_START_ADDRESS);
    rv_timer_init(timer_0_1_reg, (rv_timer_config_t){.hart_count = 2, .comparator_count = 1}, &timer_0_1);
    rv_timer_tick_params_t tick_params;
    rv_timer_approximate_tick_params(freq_hz, kTickFreqHz, &tick_params);

    // Setup rv_timer_2_3
    mmio_region_t timer_2_3_reg = mmio_region_from_addr(RV_TIMER_START_ADDRESS);
    rv_timer_init(timer_2_3_reg, (rv_timer_config_t){.hart_count = 2, .comparator_count = 1}, &timer_2_3);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Enable timer interrupt
    uint32_t mask = 1 << 7;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Power-gate and wake-up due to timer_0
    rv_timer_set_tick_params(&timer_0_1, 0, tick_params);
    rv_timer_irq_enable(&timer_0_1, 0, 0, kRvTimerEnabled);
    rv_timer_arm(&timer_0_1, 0, 0, 1024);
    rv_timer_counter_set_enabled(&timer_0_1, 0, kRvTimerEnabled);

    intr_flag = 0;
    while(intr_flag==0) {
        wait_for_interrupt();
    }

    // Enable timer_1 interrupt
    mask = 1 << 16;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Power-gate and wake-up due to timer_1
    rv_timer_set_tick_params(&timer_0_1, 1, tick_params);
    rv_timer_irq_enable(&timer_0_1, 1, 0, kRvTimerEnabled);
    rv_timer_arm(&timer_0_1, 1, 0, 1024);
    rv_timer_counter_set_enabled(&timer_0_1, 1, kRvTimerEnabled);

    intr_flag = 0;
    while(intr_flag==0) {
        wait_for_interrupt();
    }

    // Enable timer_2 interrupt
    mask = 1 << 17;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Power-gate and wake-up due to timer_2
    rv_timer_set_tick_params(&timer_2_3, 0, tick_params);
    rv_timer_irq_enable(&timer_2_3, 0, 0, kRvTimerEnabled);
    rv_timer_arm(&timer_2_3, 0, 0, 1024);
    rv_timer_counter_set_enabled(&timer_2_3, 0, kRvTimerEnabled);

    intr_flag = 0;
    while(intr_flag==0) {
        wait_for_interrupt();
    }

    // Enable timer_3 interrupt
    mask = 1 << 18;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Power-gate and wake-up due to timer_3
    rv_timer_set_tick_params(&timer_2_3, 1, tick_params);
    rv_timer_irq_enable(&timer_2_3, 1, 0, kRvTimerEnabled);
    rv_timer_arm(&timer_2_3, 1, 0, 1024);
    rv_timer_counter_set_enabled(&timer_2_3, 1, kRvTimerEnabled);

    intr_flag = 0;
    while(intr_flag==0) {
        wait_for_interrupt();
    }

    /* write something to stdout */
    printf("Success.\n");
    return EXIT_SUCCESS;
}
