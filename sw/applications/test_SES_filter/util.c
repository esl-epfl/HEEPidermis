// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Jérémie Moullet
// Description: basic test application for the SES filter

#include <stdint.h>
#include "util.h"

const uint32_t NUMBER_OUTPUT = SES_SAMPLE_NUMBER/SES_DECIM_FACTOR;

bool detect_rising_edge(uint32_t status, uint32_t mask) {
    static bool prev_val = false;
    bool current_val = (status & mask) != 0;

    bool rising_edge = (!prev_val && current_val);
    prev_val = current_val;
    return rising_edge;
}