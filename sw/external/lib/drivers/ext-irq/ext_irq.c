// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: ext_irq.c
// Author: Michele Caon
// Date: 20/06/2023
// Description: External interrupt driver

#include "ext_irq.h"
#include "core_v_mini_mcu.h"
#include "rv_plic.h"
#include "cheep.h"
#include "vcd_util.h"

/******************************/
/* ---- GLOBAL VARIABLES ---- */
/******************************/



/**********************************/
/* ---- FUNCTION DEFINITIONS ---- */
/**********************************/

int ext_irq_init(void) {
    // Initialize PLIC for external interrupts
    if (plic_Init() != kPlicOk)
        return -1;
    if (plic_irq_set_priority(EXT_INTR_0, 1) != kPlicOk)
        return -1;
    if (plic_irq_set_enabled(EXT_INTR_0, kPlicToggleEnabled) != kPlicOk)
        return -1;

    // Return success
    return 0;
}

