// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: ext_irq.h
// Author: Michele Caon
// Date: 20/06/2023
// Description: Header file for external IRQ driver

#include "rv_plic.h"

/********************************/
/* ---- EXPORTED VARIABLES ---- */
/********************************/


/********************************/
/* ---- EXPORTED FUNCTIONS ---- */
/********************************/

/**
 * @brief Initialize external interrupt handler.
 * @return 0 if successful, -1 otherwise.
 */
int ext_irq_init(void);
