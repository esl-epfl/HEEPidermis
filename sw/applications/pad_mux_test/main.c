// Copyright EPFL contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include <stdio.h>
#include <stdlib.h>
#include "csr.h"
#include "hart.h"
#include "handler.h"
#include "core_v_mini_mcu.h"
#include "pad_control.h"
#include "pad_control_regs.h"
#include "gpio.h"

#define GPIO_5 5

// ================ IMPORTANT ================
// This application needs the pdm2pcm_dummy in the tb_system.sv to be commented out.
// Otherwise, that module will try to write to GPIO_5, which is used by this application.
// ===========================================

// Description: This application tests the pad muxing functionality by
// switching the DSM_IN pad to GPIO_5 and toggling it.

int main(int argc, char *argv[])
{
    // Setup pad_control
    pad_control_t pad_control;
    pad_control.base_addr = mmio_region_from_addr((uintptr_t)PAD_CONTROL_START_ADDRESS);
    // Switch pad mux to GPIO_5 (muxed with dsm_in)
    pad_control_set_mux(&pad_control, (ptrdiff_t)PAD_CONTROL_PAD_MUX_DSM_IN_REG_OFFSET, 1);

    // Setup gpio
    gpio_result_t gpio_res;
    gpio_cfg_t cfg = {
        .pin = GPIO_5,
        .mode = GpioModeOutPushPull
    };
    gpio_res = gpio_config(cfg);
    if (gpio_res != GpioOk) {
        return EXIT_FAILURE;
    }

    // Generate output toggles on GPIO_5
    for (int i = 0; i < 100; i++) {
      gpio_write(GPIO_5, true);
      for (int i = 0; i < 10; i++) {
        asm volatile("nop");
      }
      gpio_write(GPIO_5, false);
      for (int i = 0; i < 10; i++) {
        asm volatile("nop");
      }
    }

    // Check in the wavevorms that the GPIO_5 pin is toggling

    return EXIT_SUCCESS;
}
