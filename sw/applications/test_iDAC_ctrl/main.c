// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: David Mallasen
// Description: Test application for the iDAC controller

#include "iDAC_ctrl.h"
#include "VCO_decoder.h"
#include "cheep.h"

int main() {

    iDACs_enable(true, true);
    VCOp_enable(true);
    VCOn_enable(true);

    // Set the calibration values
    iDAC1_calibrate(16);
    iDAC2_calibrate(16);

    // Set the VCO refresh rate
    VCO_set_refresh_rate(100);

    // This will vary the iDAC currents from 0 to 255 and back to 0.
    // A "piramid" should be formed in the iout of the iDACs and the 
    // vin of the VCOs.
    uint8_t c = 0;
    while( c < 255 ){
        iDACs_set_currents( c >> 2, c);
        c++;
    }
    while( c > 0 ){
        iDACs_set_currents( c >> 2, c);
        c--;
    }

    // Stabilize the iDACs
    iDAC1_calibrate(15);
    iDAC2_calibrate(16);
    iDACs_set_currents(100, 100);

    // Wait a bit for the vco count to stabilize
    for (int i = 0 ; i < 2000 ; i++) {
        asm volatile ("nop");
    }

    // This value should be stable-ish now
    uint32_t vco_count = VCO_get_count();

    return 0;
}