// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Juan Sapriza
// Description: Test application for the aMUX controller

#include "aMUX_ctrl.h"
#include "cheep.h"

int main() {

    for( uint8_t sel = 0; sel < 5; sel++){
        aMUX_select(sel);
        for (int i = 0 ; i < 2000 ; i++) {
            asm volatile ("nop");
        }
    }

    return 0;
}