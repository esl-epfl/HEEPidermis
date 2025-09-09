// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Juan Sapriza
// Description: Drivers for the REFs controller

#ifndef REFS_CTRL_H
#define REFS_CTRL_H

#include <stdint.h>
#include <stdbool.h>
#include "REFs_ctrl_regs.h"
#include "cheep.h"

typedef enum{
    IREF1,
    IREF2,
    VREF,
} REF_t;

/**
* @brief Enable/disable 
* 
* @param enable enable=true to enable the REFs, enable=false to disable it.
*/
static inline void REFs_enable(bool enable, REF_t ref) {
    *(volatile uint32_t *)(REFS_CTRL_START_ADDRESS + REFS_CTRL_ENABLE_REG_OFFSET) &= ~((uint32_t)1 << ref);
    *(volatile uint32_t *)(REFS_CTRL_START_ADDRESS + REFS_CTRL_ENABLE_REG_OFFSET) |= (uint32_t) enable << ref;
}

/**
* @brief Set the channel to be selected from the mux value for the REFs.
* 
* @param sel The channel to be selected
*/
static inline void REFs_calibrate(uint8_t calibration, REF_t ref) {
    *(volatile uint32_t *)(REFS_CTRL_START_ADDRESS + REFS_CTRL_IREF1_CALIBRATION_REG_OFFSET*(ref+1)) = calibration;
}


#endif  // REFS_CTRL_H