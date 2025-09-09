// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Juan Sapriza
// Description: Test application for the aMUX controller

#include "REFs_ctrl.h"
#include "cheep.h"

int main() {

    for( uint8_t i = 5; i < 10; i++){
        REFs_calibrate( i, IREF1 );
        REFs_calibrate( i+2, IREF2 );
        REFs_calibrate( i-2, VREF );
        for (int i = 0 ; i < 2000 ; i++) {
            asm volatile ("nop");
        }
    }

    return 0;
}