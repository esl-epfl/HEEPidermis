// Copyright EPFL contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include <stdio.h>
#include <stdlib.h>
#include "csr.h"
#include "hart.h"
#include "handler.h"
#include "core_v_mini_mcu.h"
#include "gpio.h"
#include "fast_intr_ctrl.h"
#include "fast_intr_ctrl_regs.h"

#define GPIO_TB_OUT 0
#define GPIO_TB_IN  1

gpio_t gpio;
int8_t fast_gpio_1_flag;

void handler_irq_fast_gpio_1(void)
{
    fast_intr_ctrl_t fast_intr_ctrl;
    fast_intr_ctrl.base_addr = mmio_region_from_addr((uintptr_t)FAST_INTR_CTRL_START_ADDRESS);
    gpio_irq_set_enabled(&gpio, GPIO_TB_IN, false);
    clear_fast_interrupt(&fast_intr_ctrl, kGpio_1_fic_e);

    fast_gpio_1_flag = 1;
}

int main(int argc, char *argv[])
{
    gpio_params_t gpio_params;
    gpio_result_t gpio_res;
    gpio_params.base_addr = mmio_region_from_addr((uintptr_t)GPIO_AO_START_ADDRESS);
    gpio_res = gpio_init(gpio_params, &gpio);
    if (gpio_res != kGpioOk) {
        printf("Fail.\n;");
        return -1;
    }

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    // Set mie.MEIE bit to one to enable machine-level gpio_1 interrupt
    const uint32_t mask = 1 << 23;
    CSR_SET_BITS(CSR_REG_MIE, mask);
    fast_gpio_1_flag = 0;

    gpio_res = gpio_output_set_enabled(&gpio, GPIO_TB_OUT, true);
    if (gpio_res != kGpioOk) {
        printf("Fail.\n;");
        return -1;
    }

    gpio_res = gpio_irq_set_trigger(&gpio, 1 << GPIO_TB_IN, kGpioIrqTriggerLevelHigh);
    if (gpio_res != kGpioOk) {
        printf("Fail.\n;");
        return -1;
    }

    gpio_res = gpio_irq_set_enabled(&gpio, GPIO_TB_IN, true);
    if (gpio_res != kGpioOk) {
        printf("Fail.\n;");
        return -1;
    }

    printf("Write 1 to GPIO 0 and wait for interrupt...");
    gpio_write(&gpio, GPIO_TB_OUT, true);
    while(fast_gpio_1_flag==0) {
        wait_for_interrupt();
    }

    printf("Success.\n");
    return EXIT_SUCCESS;
}
