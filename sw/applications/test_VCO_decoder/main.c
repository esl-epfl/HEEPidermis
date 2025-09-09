// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: David Mallasen
// Description: Test application for the VCO decoder

#include "VCO_decoder.h"

//#define USE_PRINTF

int main() {
    // Enable the VCOp and VCOn
    VCOp_enable(true);
    VCOn_enable(true);

    // Set the VCO refresh rate to 1000 cycles
    VCO_set_refresh_rate(1000);

    // Wait a bit for the first refresh at least
    for (int i = 0 ; i < 2000 ; i++) {
        asm volatile ("nop");
    }

    // Get the VCO values
    uint32_t p_fine   = VCOp_get_fine();
    uint32_t n_fine   = VCOn_get_fine();
    uint32_t p_coarse = VCOp_get_coarse();
    uint32_t n_coarse = VCOn_get_coarse();
    uint32_t count  = VCO_get_count();

#ifdef USE_PRINTF
    // Print the values to the console
    printf("VCOp Fine: %u\n", p_fine);
    printf("VCOn Fine: %u\n", n_fine);
    printf("VCOp Coarse: %u\n", p_coarse);
    printf("VCOn Coarse: %u\n", n_coarse);
    printf("VCO Count: %u\n", count);
#endif

    // Disable the VCOn and get the values again
    VCOp_enable(true);
    VCOn_enable(false);

    // Wait a bit for the first refresh at least
    for (int i = 0 ; i < 2000 ; i++) {
        asm volatile ("nop");
    }

    // Get the VCO values
    p_fine   = VCOp_get_fine();
    n_fine   = VCOn_get_fine();
    p_coarse = VCOp_get_coarse();
    n_coarse = VCOn_get_coarse();
    count  = VCO_get_count();

#ifdef USE_PRINTF
    // Print the values to the console
    printf("VCOp Fine: %u\n", p_fine);
    printf("VCOn Fine: %u\n", n_fine);
    printf("VCOp Coarse: %u\n", p_coarse);
    printf("VCOn Coarse: %u\n", n_coarse);
    printf("VCO Count: %u\n", count);
#endif

    // Disable the VCOp and get the values again
    VCOp_enable(false);
    VCOn_enable(true);

    // Wait a bit for the first refresh at least
    for (int i = 0 ; i < 2000 ; i++) {
        asm volatile ("nop");
    }

    // Get the VCO values
    p_fine   = VCOp_get_fine();
    n_fine   = VCOn_get_fine();
    p_coarse = VCOp_get_coarse();
    n_coarse = VCOn_get_coarse();
    count  = VCO_get_count();

#ifdef USE_PRINTF
    // Print the values to the console
    printf("VCOp Fine: %u\n", p_fine);
    printf("VCOn Fine: %u\n", n_fine);
    printf("VCOp Coarse: %u\n", p_coarse);
    printf("VCOn Coarse: %u\n", n_coarse);
    printf("VCO Count: %u\n", count);
#endif

    // Set the VCO refresh rate back to 0 cycles
    VCO_set_refresh_rate(0);

    //set manual triggers to 1, then 0
    VCO_set_manual_refresh_train(0, 1);
    VCO_set_manual_refresh_train(0, 0);

    //set manual triggers to 1, then 0
    VCO_set_manual_refresh_train(1, 1);
    VCO_set_manual_refresh_train(1, 0);

    //set manual triggers to 1, then 0
    VCO_set_manual_refresh_train(2, 1);
    VCO_set_manual_refresh_train(2, 0);

    count  = VCO_get_count();

    return count != 20081;

}