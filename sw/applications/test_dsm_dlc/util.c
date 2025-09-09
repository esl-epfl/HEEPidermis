// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Jérémie Moullet
// Description: Basic utilities for the DSM and dLC test application.

#include <stdint.h>
#include "util.h"


#ifdef USE_SES_NOT_CIC
    const uint32_t NUMBER_OUTPUT = SAMPLE_NUMBER/SES_DECIM_FACTOR;
#else
    const uint32_t NUMBER_OUTPUT = SAMPLE_NUMBER/CIC_DECIM_FACTOR;
#endif

const uint8_t goldenTruthSES[] = { 61u, 101u, 213u };  //TODO_heepidermis : adapt based on the test data
const uint16_t  goldenTruthSizeSES = sizeof(goldenTruthSES) / sizeof(goldenTruthSES[0]);

const uint8_t goldenTruthCIC[] = { 61u, 101u, 213u };  //TODO_heepidermis : adapt based on the test data
const uint16_t  goldenTruthSizeCIC = sizeof(goldenTruthCIC) / sizeof(goldenTruthCIC[0]);

bool detect_rising_edge(uint32_t status, uint32_t mask) {
    static bool prev_val = false;
    bool current_val = (status & mask) != 0;

    bool rising_edge = (!prev_val && current_val);
    prev_val = current_val;
    return rising_edge;
}