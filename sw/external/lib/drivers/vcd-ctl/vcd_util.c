// Copyright 2023 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: vcd_util.c
// Author: Michele Caon
// Date: 26/09/2023
// Description: VCD dump utilities

#include "vcd_util.h"
#include "gpio.h"

/**************************************/
/* ---- FUNCTIONS IMPLEMENTATION ---- */
/**************************************/

// Initialize VCD dump
int vcd_init(void) {
    // Configure GPIO pin as output with push and pull
    gpio_cfg_t pin_cfg = {
        .pin = VCD_GPIO,
        .mode = GpioModeOutPushPull
    };

    // Write configuration
    if (gpio_config(pin_cfg) != GpioOk)
        return -1;

    // Return success
    return 0;
}

// Enable VCD dump
void vcd_enable(void) {
    gpio_write(VCD_GPIO, true);
}

// Disable VCD dump
void vcd_disable(void) {
    gpio_write(VCD_GPIO, false);
}
