// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Jérémie Moullet
// Description: Extended test for the SES filter.
//              Initializes filter parameters, enables processing,
//              and optionally compares or logs output samples.
//              Behavior toggled via DEBUG_FROM_RTL and PRINTF_OUTPUT.

#include <stdbool.h>
#include "extended_test.h"
#include "util.h"

#define DEBUG_FROM_RTL

int extended_test() {
    // Set SES filter parameters
    SES_set_window_size(SES_WINDOW_SIZE);
    SES_set_decim_factor(SES_DECIM_FACTOR);
    SES_set_sysclk_division(SES_SYSCLK_DIVISION);
    SES_set_activated_stages(SES_ACTIVATED_STAGES);

    SES_set_gain(0, SES_GAIN_STAGE_0);
    SES_set_gain(1, SES_GAIN_STAGE_1);
    SES_set_gain(2, SES_GAIN_STAGE_2);
    SES_set_gain(3, SES_GAIN_STAGE_3);
    SES_set_gain(4, SES_GAIN_STAGE_4);
    SES_set_gain(5, SES_GAIN_STAGE_5);

    // Start the SES filter
    SES_set_control_reg(true);

    // Wait for the SES filter to be ready
    uint32_t status;
    do{
        status = SES_get_status();
    } while (status != 0b11);


    //Get the SES filter output or wait until the end
    #ifndef DEBUG_FROM_RTL
    uint32_t ses_output[SES_SAMPLE_NUMBER];

    int i = 0;
    while (i < SES_SAMPLE_NUMBER) {
        uint32_t status = SES_get_status();
        if (detect_rising_edge(status, 0b10)) {
            ses_output[i] = SES_get_filtered_output();
            i++;
        }
    }
    #else
    int i = 0;
    while (i < NUMBER_OUTPUT) {
        uint32_t status = SES_get_status();
        if (detect_rising_edge(status, 0b10)) {
            i++;
        }
    }
    #endif

    //Stop the SES filter
    SES_set_control_reg(false);

    //print the SES filter output
    #ifdef PRINTF_OUTPUT
    printf("SES output: \n");
    for (int i = 0 ; i < SES_SAMPLE_NUMBER ; i++) {
        printf("%u\n",ses_output[i]);
    }
    #endif

    // Test finished without errors
    return 0;
}