// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// File: VCO_sdk.c
// Author: Omar Shibli & Ismail Essaidi
// Date: 08/04/2026
// Description: Implementation of the VCO SDK functions


#ifndef VCO_SDK_H_
#define VCO_SDK_H_

#include <stdint.h>
#include <stdbool.h>
#include "VCO_decoder.h"
#include "timer_sdk.h"
#include "iDAC_ctrl.h"
/*
This file provides the low-level interface to the VCO-based front-end.
It is used to configure the VCO channel, counter updates, and the conversion
of the counter to voltage.
*/

//Selects which VCO path is used for measurement.
typedef enum {
    VCO_CHANNEL_NONE           = 0b00,
    VCO_CHANNEL_P              = 0b01,
    VCO_CHANNEL_N              = 0b10,
    VCO_CHANNEL_DIFFERENTIAL   = 0b11
} vco_channel_t;

//Status codes returned by the VCO SDK functions.
typedef enum {
    VCO_STATUS_OK = 0,
    VCO_STATUS_NO_NEW_SAMPLE,
    VCO_STATUS_MISSED_UPDATE,
    VCO_STATUS_NOT_INITIALIZED,
    VCO_STATUS_INVALID_ARGUMENT,
    VCO_STATUS_INVALID_CONFIGURATION,
    VCO_STATUS_UNDERFLOW,
    VCO_STATUS_OVERFLOW
} vco_status_t;

/*
Internal state used by the VCO SDK to reconstruct frequency from
successive counter readings.
 */
typedef struct {
    uint32_t on_cycles;                // VCO ON time T_int in system cycles
    uint32_t off_cycles;               // sleep window in system cycles
    uint32_t last_timestamp;           // timer_get_cycles() at previous read
    uint32_t integration_rate_Hz;       // inverse of T_int, precomputed to avoid division in the read path
    uint8_t duty_cycle_code;           // duty_cycle_code = 1/D is actually the inverse duty cycle (2 is 50% Duty Cycle, 4 is 25% Duty Cycle, 1 is 100% Duty cycle)
    uint8_t channel;                   // channel configuration
    uint8_t flags;                     // packed internal state bits
} vco_sdk_t;

// Initialize the VCO path and configure its measurement refresh rate.
vco_status_t vco_initialize(vco_channel_t channel, uint32_t refresh_rate_Hz);

// sets new measurement refresh rate T_s for the VCO + applies duty cycling to the VCO by setting its duty cycle inverse 1/D
vco_status_t vco_config(vco_channel_t channel, uint32_t refresh_rate_Hz, uint8_t duty_cycle_code);

// Estimate local VCO sensitivity K_VCO = df/dV around a given Vin.
uint32_t vco_get_kvco_Hz_per_V(uint32_t vin_uV);

// Read the latest Vin value reconstructed from the VCO frequency.
vco_status_t vco_get_Vin_uV(uint32_t *vin_uV);

// Interpolate Vin from a VCO oscillation frequency using the calibration table.
uint32_t interpolate_Vin_uV(uint32_t f_target);

// True while the selected VCO channel is currently enabled by the duty-cycle engine.
bool vco_is_on(void);

// Advance the duty-cycle state machine from the timer IRQ handler.
void vco_handle_timer_irq(void);

#endif /* VCO_SDK_H_ */
