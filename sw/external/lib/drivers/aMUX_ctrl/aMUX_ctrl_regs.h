// Generated register defines for aMUX_ctrl

// Copyright information found in source file:
// Copyright 2025 EPFL contributors

// Licensing information found in source file:
// 
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

#ifndef _AMUX_CTRL_REG_DEFS_
#define _AMUX_CTRL_REG_DEFS_

#ifdef __cplusplus
extern "C" {
#endif
// Register width
#define AMUX_CTRL_PARAM_REG_WIDTH 32

// Selection among the aMUX options
#define AMUX_CTRL_SEL_REG_OFFSET 0x0
#define AMUX_CTRL_SEL_SEL_MASK 0x1f
#define AMUX_CTRL_SEL_SEL_OFFSET 0
#define AMUX_CTRL_SEL_SEL_FIELD \
  ((bitfield_field32_t) { .mask = AMUX_CTRL_SEL_SEL_MASK, .index = AMUX_CTRL_SEL_SEL_OFFSET })

#ifdef __cplusplus
}  // extern "C"
#endif
#endif  // _AMUX_CTRL_REG_DEFS_
// End generated register defines for aMUX_ctrl