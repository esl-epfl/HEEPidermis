// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// File: config_profiles.h
// Author: Ismail Essaidi
// Date: 29/04/2026
// Description: Defines preconfigured profiles for translating high-level operating-point requests into GSR controller configurations. 
//              This is a placeholder for a more dynamic profiling mechanism that will be developed in a future PR.
#ifndef GSR_CONFIG_PROFILES_H
#define GSR_CONFIG_PROFILES_H

#include <stdint.h>
#include "GSR_types.h"

#define GSR_OPCTRL_PROFILE_COUNT 3U

typedef struct {
    uint8_t idac_code;
} gsr_range_profile_t;

typedef struct {
    gsr_ctrl_mode_t mode;
} gsr_resolution_profile_t;

typedef struct {
    uint8_t refresh_rate_Hz;
} gsr_refresh_profile_t;

typedef struct {
    uint8_t D;
} gsr_power_profile_t;

extern const gsr_range_profile_t k_range_profiles[GSR_OPCTRL_PROFILE_COUNT];
extern const gsr_resolution_profile_t k_resolution_profiles[GSR_OPCTRL_PROFILE_COUNT];
extern const gsr_refresh_profile_t k_refresh_profiles[GSR_OPCTRL_PROFILE_COUNT];
extern const gsr_power_profile_t k_power_profiles[GSR_OPCTRL_PROFILE_COUNT];

#endif /* GSR_CONFIG_PROFILES_H */