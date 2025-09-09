// Copyright 2023 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: main.c
// Author: Michele Caon
// Date: 26/09/2023
// Description: VCD dump trigger test program

#include "vcd_util.h"

#define LOOP_COUNT 100

int main(void) {
    // Initialize VCD dump
    if (vcd_init() != 0)
        return 1;

    // Enable VCD dump
    vcd_enable();

    // Busy loop
    for (unsigned int i = 0; i < LOOP_COUNT; i++) {
        asm volatile ("nop");
    }

    // Disable VCD dump
    vcd_disable();
    
    // Busy loop
    for (unsigned int i = 0; i < LOOP_COUNT; i++) {
        asm volatile ("nop");
    }

    // Enable VCD dump
    vcd_enable();

    // Busy loop
    for (unsigned int i = 0; i < LOOP_COUNT; i++) {
        asm volatile ("nop");
    }

    // Disable VCD dump
    vcd_disable();
    
    // Return success
    return 0;
}
