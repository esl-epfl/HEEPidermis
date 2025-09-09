// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: David Mallasen
// Description: Test application for the VCO counter in the VCO decoder

#include "VCO_decoder.h"

int main() {
    // Enable the VCOp and VCOn
    VCOp_enable(true);
    VCOn_enable(true);

    // Set the VCO refresh rate to 1000 cycles
    VCO_set_refresh_rate(1000);

    // Set the VCO counter limit to 100 cycles
    VCO_set_counter_limit(100);

    // Trigger a manual refresh
    VCO_trigger();

    // Wait a bit for the first refresh at least
    for (int i = 0 ; i < 2000 ; i++) {
        asm volatile ("nop");
    }

    // CHECK manually in the waveforms the vco_counter_overflow signal
    // in the tb_system. It should have one-cycle pulses every 100+1
    // cycles.

    return 0;
}