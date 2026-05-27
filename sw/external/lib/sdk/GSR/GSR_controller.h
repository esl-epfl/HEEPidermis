// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// File: GSR_sdk.h
// Author: Omar Shibli & Ismail Essaidi
// Date: 08/04/2026
/* Description:
 * The GSR SDK owns measurement state and applies configs to the GSR front-end:
 * - reads Vin from the VCO SDK
 * - computes conductance, sensitivity (deltaG), and power
 * - adjusts sampling rate depending on whether the signal is in baseline tracking, phasic event capture, or recovery after an event
 * - tracks the active configuration (VCO channel, refresh rate, iDAC code) applies it  to the hardware
 * Note: Operating-point policy lives in GSR_op_controller.
*/

#ifndef GSR_CONTROLLER_H_
#define GSR_CONTROLLER_H_

#include <stdbool.h>
#include <stdint.h>
#include "GSR_sdk.h"
#include "config_profiles.h"
#include "GSR_types.h"
#include "dma.h"

/* Hardware configuration required by the measurement layer. */
typedef struct {
    vco_channel_t channel;       /* VCO path used to reconstruct Vin. */
    uint8_t  duty_cycle_code;                 /* is the VCO duty cycle inverse (1/D) (2 is 50% Duty Cycle, 4 is 25% Duty Cycle, 1 is 100% Duty cycle)) */
    uint32_t baseline_refresh_rate_Hz;
    uint32_t phasic_refresh_rate_Hz;
    uint32_t recovery_refresh_rate_Hz;
    uint32_t current_refresh_rate_Hz;
    uint8_t idac_code;           /* Raw iDAC code used to set the injected current. */
    uint8_t M;                  /* Number of averaged measurements for 1 sample; 1 means no averaging . */
} gsr_config_t;

/* One reconstructed GSR sample. */
typedef struct {
    uint32_t G_nS;          /* Conductance computed from current_nA and vin_uV. */
    uint32_t prev_G_nS;     /* Previous conductance value. */
    uint32_t vin_uV;        /* Reconstructed front-end voltage from the VCO readout. */
    uint32_t baseline_nS;
    int32_t slope_nS;
    uint32_t amplitude_nS;        /* Absolute change in conductance compared to the baseline. */

    uint32_t current_nA;         /* Injected current used for this sample's conductance computation. */
    /*
     * True only after a successful VCO read and conductance conversion.
     * False means the sample must not be used for control/math; the numeric
     * fields may contain old values or placeholders from a failed read.
     */
    bool valid;
} gsr_sample_t;

typedef struct{
    uint32_t conductance_sensitivity_nS; /* Estimated conductance sensitivity (delta G) in nS around the current operation point */
    uint32_t resolution_dB;     /* Estimated conductance resolution in dB around the current operation point, used as QoS metric. */
    // uint32_t power_nW;                   /* Estimated power consumption of the measurement, used for control decisions. */
} gsr_metrics_t;

typedef struct {
    bool enabled;              /* True when DMA acquisition is configured for use. */
    bool running;              /* True after the DMA has been successfully launched. */

    uint32_t *buf_a;           /* First ping-pong buffer for VCO counts. */
    uint32_t *buf_b;           /* Second ping-pong buffer for VCO counts. */
    uint32_t samples_per_window; /* Number of VCO count samples captured per DMA transaction. */

    uint32_t *write_buf;       /* Buffer currently used as the DMA destination. */
    uint32_t *completed_buf;   /* Buffer most recently completed and ready for processing. */

    volatile bool window_ready; /* Set by the DMA ISR when write_buf has been filled. */
    volatile bool overrun;      /* Set if DMA finishes a new window before SW consumed the previous one. */

    uint8_t discard_samples;     /* Number of initial samples to discard after starting DMA or any config change */
} gsr_dma_acq_t;

//Controller state and configuration parameters.
typedef struct {
    gsr_ctrl_mode_t mode;
    gsr_config_t config;
    gsr_sample_t sample;
    uint32_t amplitude_threshold_nS;
    uint32_t slope_threshold_nS;
    uint32_t settle_threshold_nS;
    uint32_t recovery_count_required;
    uint32_t recovery_counter;
    uint32_t max_current_nA;     /* Highest injectable current supported by the iDAC model and the last measured conductance. */
    gsr_dlc_config_t dlc_cfg;
    bool dlc_used;
    bool initialized;
    gsr_dma_acq_t *dma;
    bool dma_used;
    uint8_t valid_samples;
} gsr_controller_t;

uint8_t get_valid_samples(gsr_controller_t *ctrl);

// Initialize the controller state and underlying GSR front-end.
gsr_status_t gsr_controller_init(gsr_controller_t *ctrl);

//Populate the controller with a default parameter set.
gsr_status_t gsr_set_default_settings(gsr_controller_t *ctrl);

// Update the idac code and apply it to the hardware.
void gsr_controller_set_current(gsr_controller_t *ctrl, uint8_t idac_code);

// Update the controller configuration and apply it to the hardware.
gsr_status_t gsr_controller_set_config(gsr_controller_t *ctrl);

/* Read one sample and store it in the controller. Duty-cycled reads sleep until the VCO ON window has completed. */
gsr_status_t gsr_read_sample(gsr_controller_t *ctrl);

/* Return the last valid/attempted sample stored in the context. */
const gsr_sample_t *gsr_get_last_sample(const gsr_controller_t *ctrl);

/* Returns performance metrics (sensitivity, power, resolution)*/
gsr_metrics_t get_metrics(gsr_controller_t *ctrl);

//Execute one controller update step from the latest available sample.
gsr_status_t gsr_controller_step(gsr_controller_t *ctrl);


void dma_intr_handler_trans_done(uint8_t channel);
#endif /* GSR_CONTROLLER_H_ */