// Copyright 2023 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: vcd_util.h
// Author: Michele Caon
// Date: 26/09/2023
// Description: VCD dump control utility

#ifndef VCD_UTIL_H_
#define VCD_UTIL_H_

#define VCD_GPIO 0

/********************************/
/* ---- EXPORTED FUNCTIONS ---- */
/********************************/

/**
 * @brief Initialize the VCD dump
 * 
 * @return int 0 if success, -1 otherwise
 */
int vcd_init();

/**
 * @brief Enable the VCD dump
 * 
 */
void vcd_enable();

/**
 * @brief Disable the VCD dump
 * 
 */
void vcd_disable();

#endif /* VCD_UTIL_H_ */
