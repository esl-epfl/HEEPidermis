// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Juan Sapriza
// Description: Drivers for the aMUX controller

#ifndef AMUX_CTRL_H
#define AMUX_CTRL_H

#include <stdint.h>
#include <stdbool.h>
#include "aMUX_ctrl_regs.h"
#include "cheep.h"

/**
* @brief Set the channel to be selected from the mux value for the aMUX.
*
* @param sel The channel to be selected
*/
static inline void aMUX_select(uint8_t sel) {
    *(volatile uint32_t *)(AMUX_CTRL_START_ADDRESS + AMUX_CTRL_SEL_REG_OFFSET) = sel;
}


#endif  // AMUX_CTRL_H