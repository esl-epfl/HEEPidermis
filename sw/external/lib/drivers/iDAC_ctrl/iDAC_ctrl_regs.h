// Generated register defines for iDAC_ctrl

// Copyright information found in source file:
// Copyright 2025 EPFL contributors

// Licensing information found in source file:
// 
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

#ifndef _IDAC_CTRL_REG_DEFS_
#define _IDAC_CTRL_REG_DEFS_

#ifdef __cplusplus
extern "C" {
#endif
// Register width
#define IDAC_CTRL_PARAM_REG_WIDTH 32

// Every how many cycles should the iDAC be refreshed
#define IDAC_CTRL_REFRESH_CYCLES_REG_OFFSET 0x0

// Trigger manually the refresh signal
#define IDAC_CTRL_MANUAL_TRIGGER_REG_OFFSET 0x4
#define IDAC_CTRL_MANUAL_TRIGGER_MANUAL_TRIGGER_BIT 0

// Enable signals for the iDACs and the controller
#define IDAC_CTRL_ENABLE_REG_OFFSET 0x8
#define IDAC_CTRL_ENABLE_IDAC1_ENABLE_BIT 0
#define IDAC_CTRL_ENABLE_IDAC2_ENABLE_BIT 1

// Calibration bits for the iDAC 1
#define IDAC_CTRL_CALIBRATION_1_REG_OFFSET 0xc
#define IDAC_CTRL_CALIBRATION_1_CALIBRATION_1_MASK 0x1f
#define IDAC_CTRL_CALIBRATION_1_CALIBRATION_1_OFFSET 0
#define IDAC_CTRL_CALIBRATION_1_CALIBRATION_1_FIELD \
  ((bitfield_field32_t) { .mask = IDAC_CTRL_CALIBRATION_1_CALIBRATION_1_MASK, .index = IDAC_CTRL_CALIBRATION_1_CALIBRATION_1_OFFSET })

// Calibration bits for the iDAC 2
#define IDAC_CTRL_CALIBRATION_2_REG_OFFSET 0x10
#define IDAC_CTRL_CALIBRATION_2_CALIBRATION_2_MASK 0x1f
#define IDAC_CTRL_CALIBRATION_2_CALIBRATION_2_OFFSET 0
#define IDAC_CTRL_CALIBRATION_2_CALIBRATION_2_FIELD \
  ((bitfield_field32_t) { .mask = IDAC_CTRL_CALIBRATION_2_CALIBRATION_2_MASK, .index = IDAC_CTRL_CALIBRATION_2_CALIBRATION_2_OFFSET })

// Value of the current
#define IDAC_CTRL_CURRENT_REG_OFFSET 0x14
#define IDAC_CTRL_CURRENT_CURRENT_1_MASK 0xff
#define IDAC_CTRL_CURRENT_CURRENT_1_OFFSET 0
#define IDAC_CTRL_CURRENT_CURRENT_1_FIELD \
  ((bitfield_field32_t) { .mask = IDAC_CTRL_CURRENT_CURRENT_1_MASK, .index = IDAC_CTRL_CURRENT_CURRENT_1_OFFSET })
#define IDAC_CTRL_CURRENT_CURRENT_2_MASK 0xff
#define IDAC_CTRL_CURRENT_CURRENT_2_OFFSET 8
#define IDAC_CTRL_CURRENT_CURRENT_2_FIELD \
  ((bitfield_field32_t) { .mask = IDAC_CTRL_CURRENT_CURRENT_2_MASK, .index = IDAC_CTRL_CURRENT_CURRENT_2_OFFSET })

#ifdef __cplusplus
}  // extern "C"
#endif
#endif  // _IDAC_CTRL_REG_DEFS_
// End generated register defines for iDAC_ctrl