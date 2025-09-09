// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: David Mallasen
// Description: Drivers for the iDAC controller

#ifndef IDAC_CTRL_H
#define IDAC_CTRL_H

#include <stdint.h>
#include <stdbool.h>
#include "iDAC_ctrl_regs.h"
#include "cheep.h"

/**
* @brief Enable/disable the iDACs.
* @param enable enable=true to enable the iDAC, enable=false to disable it.
*/
static inline void iDACs_enable(bool enable1, bool enable2) {
    *(volatile uint32_t *)(IDAC_CTRL_START_ADDRESS + IDAC_CTRL_ENABLE_REG_OFFSET) = (uint32_t)enable1 | ((uint32_t)enable2 <<1);
}

/**
* @brief Set the calibration value for the iDAC 1. Note that each iDAC has its own functions to minimize code overhead
*
* @param calibration The calibration value to set. This is a 5-bit value,
*                 so it should be between 0 and 31.
*/
static inline void iDAC1_calibrate(uint8_t calibration) {
    *(volatile uint32_t *)(IDAC_CTRL_START_ADDRESS + IDAC_CTRL_CALIBRATION_1_REG_OFFSET) = calibration;
}

/**
* @brief Set the calibration value for the iDAC 2. Note that each iDAC has its own functions to minimize code overhead
*
* @param calibration The calibration value to set. This is a 5-bit value,
*                 so it should be between 0 and 31.
*/
static inline void iDAC2_calibrate(uint8_t calibration) {
    *(volatile uint32_t *)(IDAC_CTRL_START_ADDRESS + IDAC_CTRL_CALIBRATION_2_REG_OFFSET) = calibration;
}


/**
* @brief Set the current value for both iDACs simultaneously. This is faster (in clock cycles)
*           than writing them separately, as no masking or multiple bus accesses are needed.
*
* @param iDAC1_current The current value to set for iDAC1 (0-255)
* @param iDAC2_current The current value to set for iDAC2 (0-255)
*/
static inline void iDACs_set_currents(uint8_t iDAC1_current, uint8_t iDAC2_current) {
    *(volatile uint32_t *)(IDAC_CTRL_START_ADDRESS + IDAC_CTRL_CURRENT_REG_OFFSET) = (uint32_t)(((uint32_t)iDAC1_current) | (uint32_t)(((uint32_t)iDAC2_current) << IDAC_CTRL_CURRENT_CURRENT_2_OFFSET));
}

/**
* @brief Set the iDACs refresh rate.
* 
* @param num_cycles Number of cycles to wait before refreshing the iDACs.
*/
static inline void iDACs_set_refresh_rate(uint32_t num_cycles) {
    *(volatile uint32_t *)(IDAC_CTRL_START_ADDRESS + IDAC_CTRL_REFRESH_CYCLES_REG_OFFSET) = num_cycles;
}

#endif  // IDAC_CTRL_H