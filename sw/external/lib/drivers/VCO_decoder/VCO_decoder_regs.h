// Generated register defines for VCO_decoder

// Copyright information found in source file:
// Copyright 2025 EPFL contributors

// Licensing information found in source file:
// 
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

#ifndef _VCO_DECODER_REG_DEFS_
#define _VCO_DECODER_REG_DEFS_

#ifdef __cplusplus
extern "C" {
#endif
// Register width
#define VCO_DECODER_PARAM_REG_WIDTH 32

// Every how many cycles should the VCO-ADC be refreshed
#define VCO_DECODER_REFRESH_CYCLES_REG_OFFSET 0x0

// Limit of the VCO dynamic counter
#define VCO_DECODER_COUNTER_LIMIT_REG_OFFSET 0x4

// Trigger manually the refresh signal
#define VCO_DECODER_MANUAL_TRIGGER_REG_OFFSET 0x8
#define VCO_DECODER_MANUAL_TRIGGER_MANUAL_TRIGGER_BIT 0

// Enable signals for the VCOs and VCO decoder
#define VCO_DECODER_ENABLE_REG_OFFSET 0xc
#define VCO_DECODER_ENABLE_P_ENABLE_BIT 0
#define VCO_DECODER_ENABLE_N_ENABLE_BIT 1

// VCOp ADC fine output
#define VCO_DECODER_ADC_P_FINE_OUT_REG_OFFSET 0x10
#define VCO_DECODER_ADC_P_FINE_OUT_ADC_P_FINE_OUT_MASK 0x7fffffff
#define VCO_DECODER_ADC_P_FINE_OUT_ADC_P_FINE_OUT_OFFSET 0
#define VCO_DECODER_ADC_P_FINE_OUT_ADC_P_FINE_OUT_FIELD \
  ((bitfield_field32_t) { .mask = VCO_DECODER_ADC_P_FINE_OUT_ADC_P_FINE_OUT_MASK, .index = VCO_DECODER_ADC_P_FINE_OUT_ADC_P_FINE_OUT_OFFSET })

// VCOn ADC fine output
#define VCO_DECODER_ADC_N_FINE_OUT_REG_OFFSET 0x14
#define VCO_DECODER_ADC_N_FINE_OUT_ADC_N_FINE_OUT_MASK 0x7fffffff
#define VCO_DECODER_ADC_N_FINE_OUT_ADC_N_FINE_OUT_OFFSET 0
#define VCO_DECODER_ADC_N_FINE_OUT_ADC_N_FINE_OUT_FIELD \
  ((bitfield_field32_t) { .mask = VCO_DECODER_ADC_N_FINE_OUT_ADC_N_FINE_OUT_MASK, .index = VCO_DECODER_ADC_N_FINE_OUT_ADC_N_FINE_OUT_OFFSET })

// VCOp ADC coarse output
#define VCO_DECODER_ADC_P_COARSE_OUT_REG_OFFSET 0x18
#define VCO_DECODER_ADC_P_COARSE_OUT_ADC_P_COARSE_OUT_MASK 0x3ffffff
#define VCO_DECODER_ADC_P_COARSE_OUT_ADC_P_COARSE_OUT_OFFSET 0
#define VCO_DECODER_ADC_P_COARSE_OUT_ADC_P_COARSE_OUT_FIELD \
  ((bitfield_field32_t) { .mask = VCO_DECODER_ADC_P_COARSE_OUT_ADC_P_COARSE_OUT_MASK, .index = VCO_DECODER_ADC_P_COARSE_OUT_ADC_P_COARSE_OUT_OFFSET })

// VCOn ADC coarse output
#define VCO_DECODER_ADC_N_COARSE_OUT_REG_OFFSET 0x1c
#define VCO_DECODER_ADC_N_COARSE_OUT_ADC_N_COARSE_OUT_MASK 0x3ffffff
#define VCO_DECODER_ADC_N_COARSE_OUT_ADC_N_COARSE_OUT_OFFSET 0
#define VCO_DECODER_ADC_N_COARSE_OUT_ADC_N_COARSE_OUT_FIELD \
  ((bitfield_field32_t) { .mask = VCO_DECODER_ADC_N_COARSE_OUT_ADC_N_COARSE_OUT_MASK, .index = VCO_DECODER_ADC_N_COARSE_OUT_ADC_N_COARSE_OUT_OFFSET })

// VCO decoder count output
#define VCO_DECODER_VCO_DECODER_CNT_REG_OFFSET 0x20

// Drivers the refresh_o signal
#define VCO_DECODER_MANUAL_REFRESH_TRAIN0_REG_OFFSET 0x24
#define VCO_DECODER_MANUAL_REFRESH_TRAIN0_MANUAL_REFRESH_TRAIN0_BIT 0

// Drivers the clk of the vco_computation units
#define VCO_DECODER_MANUAL_REFRESH_TRAIN1_REG_OFFSET 0x28
#define VCO_DECODER_MANUAL_REFRESH_TRAIN1_MANUAL_REFRESH_TRAIN1_BIT 0

// Drivers the refresh_notif_o signal
#define VCO_DECODER_MANUAL_REFRESH_TRAIN2_REG_OFFSET 0x2c
#define VCO_DECODER_MANUAL_REFRESH_TRAIN2_MANUAL_REFRESH_TRAIN2_BIT 0

#ifdef __cplusplus
}  // extern "C"
#endif
#endif  // _VCO_DECODER_REG_DEFS_
// End generated register defines for VCO_decoder