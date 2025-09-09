// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: cheep.h
// Author: Michele Caon
// Date: 13/05/2023
// Description: Address map for cheep external peripherals.

#ifndef CHEEP_H_
#define CHEEP_H_

#ifdef __cplusplus
extern "C" {
#endif // __cplusplus

#include "core_v_mini_mcu.h"

// Number of masters and slaves on the external crossbar
#define EXT_XBAR_NMASTER ${xbar_nmasters}
#define EXT_XBAR_NSLAVE ${xbar_nslaves}

// Peripherals address map
// -----------------------

// iDAC registers
#define IDAC_CTRL_START_ADDRESS (EXT_PERIPHERAL_START_ADDRESS + 0x${iDAC_ctrl_start_address})
#define IDAC_CTRL_SIZE 0x${iDAC_ctrl_size}
#define IDAC_CTRL_END_ADDRESS (IDAC_CTRL_START_ADDRESS + IDAC_CTRL_SIZE)

// VCO decoder registers
#define VCO_DECODER_START_ADDRESS (EXT_PERIPHERAL_START_ADDRESS + 0x${VCO_decoder_start_address})
#define VCO_DECODER_SIZE 0x${VCO_decoder_size}
#define VCO_DECODER_END_ADDRESS (VCO_DECODER_START_ADDRESS + VCO_DECODER_SIZE)

// SES filter registers
#define SES_FILTER_START_ADDRESS (EXT_PERIPHERAL_START_ADDRESS + 0x${SES_filter_start_address})
#define SES_FILTER_SIZE 0x${SES_filter_size}
#define SES_FILTER_END_ADDRESS (SES_FILTER_START_ADDRESS + SES_FILTER_SIZE)

// aMUX decoder registers
#define AMUX_CTRL_START_ADDRESS (EXT_PERIPHERAL_START_ADDRESS + 0x${aMUX_ctrl_start_address})
#define AMUX_CTRL_SIZE 0x${VCO_decoder_size}
#define AMUX_CTRL_END_ADDRESS (AMUX_CTRL_START_ADDRESS + AMUX_CTRL_SIZE)

// REFs controller registers
#define REFS_CTRL_START_ADDRESS (EXT_PERIPHERAL_START_ADDRESS + 0x${REFs_ctrl_start_address})
#define REFS_CTRL_SIZE 0x${REFs_ctrl_size}
#define REFS_CTRL_END_ADDRESS (REFS_CTRL_START_ADDRESS + REFS_CTRL_SIZE)

// dLC controller registers
#define DLC_START_ADDRESS (EXT_PERIPHERAL_START_ADDRESS + 0x${dLC_start_address})
#define DLC_SIZE 0x${dLC_size}
#define DLC_END_ADDRESS (DLC_START_ADDRESS + DLC_SIZE)

// CIC registers
#define CIC_START_ADDRESS (EXT_PERIPHERAL_START_ADDRESS + 0x${CIC_start_address})
#define CIC_SIZE 0x${CIC_size}
#define CIC_END_ADDRESS (CIC_START_ADDRESS + CIC_SIZE)

#ifdef __cplusplus
} // extern "C"
#endif // __cplusplus

#endif /* CHEEP_H_ */
