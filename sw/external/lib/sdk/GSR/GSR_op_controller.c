// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// File: GSR_op_controller.c
// Author: Ismail Essaidi
// Date: 08/04/2026
// Description: Operating-point request layer for the GSR front-end.

#include "GSR_op_controller.h"

#include <stddef.h>
#define GSR_IDAC_LSB_NA              40U
#define GUARD_IDC_NA                 (GSR_IDAC_LSB_NA * 2) // guard i_dc to prevent going out of range in the next conductance measurement; 80nA corresponds to 170 nS of change in conductance
#define GSR_IDAC_MAX_CODE            255U
#define MAX_OUT_OF_RANGE_EVENTS      2U

static uint32_t underflow_cnt = 0U;
static uint32_t overflow_cnt = 0U;


static void reset_out_of_range_counters(void) {
    underflow_cnt = 0U;
    overflow_cnt = 0U;
}

static bool gsr_opctrl_is_valid_request(const gsr_op_request_t *request) {
    return (request->range <= HIGH) &&
           (request->resolution <= HIGH) &&
           (request->power <= HIGH);
}

static gsr_opctrl_status_t gsr_opctrl_status_from_gsr(gsr_status_t status) {
    if (status == GSR_STATUS_OK) return GSR_OPCTRL_OK;
    if (status == GSR_STATUS_INVALID_ARGUMENT) return GSR_OPCTRL_INVALID_ARGUMENT;
    if (status == GSR_STATUS_NOT_INITIALIZED) return GSR_OPCTRL_NOT_INITIALIZED;
    if (status == GSR_STATUS_OVERFLOW) return GSR_OPCTRL_MEASUREMENT_OVERFLOW;
    if (status == GSR_STATUS_UNDERFLOW) return GSR_OPCTRL_MEASUREMENT_UNDERFLOW;
    return GSR_OPCTRL_MEASUREMENT_ERROR; // NO_NEW_SAMPLE or MISSED_UPDATE 
}

gsr_opctrl_status_t gsr_opctrl_init(gsr_op_controller_t *ctrl,
                                    gsr_controller_t *controller) {
    if (ctrl == NULL || controller == NULL) return GSR_OPCTRL_INVALID_ARGUMENT;

    ctrl->operating_point = controller;
    ctrl->current_request = (gsr_op_request_t){
        .range = HIGH,
        .resolution = LOW,
        .power = HIGH
    };
    ctrl->has_valid_op = false;
    ctrl->initialized = true;
    ctrl->request_changed = false;
    overflow_cnt = 0U;
    underflow_cnt = 0U;
    return GSR_OPCTRL_OK;
}

/*
 * Compute the iDAC code for one requested range.
 *
 * max_current_nA is the latest conductance-dependent current limit. The range
 * selects a target current below that limit:
 *
 *   LOW    -> i_dc,max - I_guard
 *   MEDIUM -> 3/4 i_dc,max
 *   HIGH   -> 1/2 i_dc,max
 *
 * The targets are clamped to preserve LOW >= MEDIUM >= HIGH, then quantized to
 * an iDAC code. The function returns false only if the max_idc doesn't exceed GUARD_IDC_NA.
 */
static bool compute_range_idac_code(const uint32_t max_current_nA,
                                    const request_levels_t range,
                                    uint8_t *idac_code)
{
    uint32_t target_current_nA;
    uint32_t target_code;
    uint32_t low_target_nA;
    uint32_t medium_target_nA;
    uint32_t high_target_nA;

    if (idac_code == NULL) return false;
    if (max_current_nA <= GUARD_IDC_NA) return false; // max current is too low to satisfy the guard constraint for any range

    low_target_nA = max_current_nA - GUARD_IDC_NA; // i_dc,max - guard margin
    medium_target_nA = max_current_nA - (max_current_nA >> 2); // 3/4 of i_dc,max
    high_target_nA = max_current_nA >> 1; // 1/2 of i_dc,max

    /* LOW >= MEDIUM >= HIGH. */
    if (medium_target_nA > low_target_nA) {
        medium_target_nA = low_target_nA;
    }

    if (high_target_nA > medium_target_nA) {
        high_target_nA = medium_target_nA;
    }

    switch (range) {
    case LOW:
        target_current_nA = low_target_nA;
        break;
    case MEDIUM:
        target_current_nA = medium_target_nA;
        break;
    case HIGH:
        target_current_nA = high_target_nA;
        break;
    default:
        return false;
    }

    target_code = target_current_nA / GSR_IDAC_LSB_NA;

    if (target_code == 0U) return false; // never gonna happen because covered by the GUARD_IDC_NA check above, but for completeness.

    if (target_code > GSR_IDAC_MAX_CODE) {
        target_code = GSR_IDAC_MAX_CODE;
    }

    *idac_code = (uint8_t)target_code;
    return true;
}

/*
 * Map the requested range to an iDAC code using the latest max_current_nA.
 */
static gsr_opctrl_status_t resolve_range_request(const gsr_op_request_t *request, const uint32_t max_current_nA,
                                            gsr_controller_t *planned_operating_point, gsr_op_request_t *planned_request) {
    uint8_t target_code;

    if (!compute_range_idac_code(max_current_nA, request->range, &target_code)) return GSR_OPCTRL_REQUEST_UNSATISFIABLE;

    planned_operating_point->config.idac_code = target_code;
    planned_request->range = request->range;

    return GSR_OPCTRL_OK;
}

/* 
*  Translate an application request into concrete operating point for our controller. 
*/
static gsr_opctrl_status_t gsr_opctrl_plan(const gsr_op_request_t *request,const gsr_op_controller_t *ctrl,
                                        gsr_controller_t *planned_operating_point, gsr_op_request_t *planned_request) {
    
    if (!gsr_opctrl_is_valid_request(request)) return GSR_OPCTRL_REQUEST_INVALID;
    
    gsr_opctrl_status_t status = GSR_OPCTRL_OK;
    
    /* 
    * resolve the request into concrete config fields using the preconfigured profiles in config_profiles.h 
    */
    planned_operating_point->config.channel = VCO_CHANNEL_P;
    /*
    * We want to control refresh rate because it impacts resolution. 
    * However the way it is implemented now in GSR_controller is by changing modes (which makes sense but is a bit rigid)
    * So to change refresh rate we select the corresponding mode.
    * NOTE 1: In GSR_controller we should decouple refresh rate and modes because what if we want high refresh rates for baseline? 
    * NOTE 2: preconfigured values are in config_profiles.h.
    * TLDR: For now: resolution -> refresh rate (through OSR) -> mode with higher refresh rate in the controller config
    */
    planned_operating_point->mode = k_resolution_profiles[(uint32_t)request->resolution].mode;
    planned_operating_point->config.baseline_refresh_rate_Hz = k_refresh_profiles[0].refresh_rate_Hz;
    planned_operating_point->config.recovery_refresh_rate_Hz = k_refresh_profiles[1].refresh_rate_Hz;
    planned_operating_point->config.phasic_refresh_rate_Hz =  k_refresh_profiles[2].refresh_rate_Hz;

    planned_request->resolution = request->resolution; // Store the actually achieved resolution request
    
    // resolve power profile ==> duty_cycle VCO and iDAC
    planned_operating_point->config.duty_cycle_code = k_power_profiles[(uint32_t)request->power].duty_cycle_code;    
    planned_request->power = request->power; // Store the actually achieved power request

    // resolve range request into iDAC code
    if (!ctrl->has_valid_op) { 
        planned_operating_point->config.idac_code = 6U; // Initial conservative value allowing G_min = 510 nS which is a reasonnable lower bound for skin conductance
    } else { 
        status = resolve_range_request(request, ctrl->operating_point->max_current_nA, planned_operating_point, planned_request);
    }

    return status;
}

/* Apply an already planned operating point through the GSR controller. */
static gsr_opctrl_status_t gsr_opctrl_apply(gsr_op_controller_t *ctrl, const gsr_controller_t *planned_operating_point) {
    gsr_status_t status;

    if (!ctrl->initialized || ctrl->operating_point == NULL) return GSR_OPCTRL_NOT_INITIALIZED;

    // apply the resolved operating point to the controller 
    ctrl->operating_point->config = planned_operating_point->config;
    ctrl->operating_point->mode = planned_operating_point->mode;

    status = gsr_opctrl_status_from_gsr(gsr_controller_set_config(ctrl->operating_point));

    return status;
}

gsr_opctrl_status_t gsr_opctrl_request(gsr_op_controller_t *ctrl,
                                       const gsr_op_request_t *request,
                                       gsr_controller_t *operating_point) {
    gsr_controller_t planned_operating_point;
    gsr_op_request_t planned_request;
    gsr_opctrl_status_t status;
    gsr_opctrl_status_t apply_status;

    if (ctrl == NULL || request == NULL) return GSR_OPCTRL_INVALID_ARGUMENT;
    if (!ctrl->initialized || ctrl->operating_point == NULL) return GSR_OPCTRL_NOT_INITIALIZED;
   
    planned_operating_point = *(ctrl->operating_point);
    planned_request = *request;

    status = gsr_opctrl_plan(request, ctrl, &planned_operating_point, &planned_request); // can return REQUEST_UNSATISFIABLE, GSR_OPCTRL_OK
    
    if (status != GSR_OPCTRL_OK) return status;

    apply_status = gsr_opctrl_apply(ctrl, &planned_operating_point);
    if (apply_status != GSR_OPCTRL_OK) return apply_status;
    
    ctrl->current_request = planned_request;
    ctrl->request_changed = false; // If an automatic range change happened (flag is true) we clear it during successful explicit request

    if (operating_point != NULL) {
        *operating_point = planned_operating_point;
    }

    return status;
}

static gsr_opctrl_status_t gsr_opctrl_recover_underflow(gsr_op_controller_t *ctrl)
{
    uint8_t code;

    code = ctrl->operating_point->config.idac_code;

    if (code <= 1U) return GSR_OPCTRL_MEASUREMENT_ERROR; // can't decrease current any further, we probably have a measurement error.

    /* Halve current. Round up so nonzero codes remain nonzero. */
    code = (code + 1U) >> 1;

    gsr_controller_set_current(ctrl->operating_point, code);

    return GSR_OPCTRL_MEASUREMENT_UNDERFLOW;
}

static gsr_opctrl_status_t gsr_opctrl_recover_overflow(gsr_op_controller_t *ctrl)
{
    uint8_t code;
    uint16_t next_code;
    uint16_t max_code;

    code = ctrl->operating_point->config.idac_code;
    max_code = ctrl->operating_point->max_current_nA / GSR_IDAC_LSB_NA;

    if (max_code == 0U || code >= max_code) return GSR_OPCTRL_MEASUREMENT_ERROR;

    if (max_code > GSR_IDAC_MAX_CODE) {
        max_code = GSR_IDAC_MAX_CODE;
    }
    /* Double current, saturating at max_code. */
    next_code = (uint16_t)code << 1;
    if (next_code > max_code) {
        next_code = max_code;
    }

    if (next_code == code) return GSR_OPCTRL_MEASUREMENT_ERROR; // can't increase current any further, we probably have a measurement error.

    gsr_controller_set_current(ctrl->operating_point, (uint8_t)next_code);

    return GSR_OPCTRL_MEASUREMENT_OVERFLOW;
}
/*
 * Retune the injected current from the latest measured operating limit.
 *
 * After each valid conductance sample, the lower GSR controller updates
 * ctrl->operating_point->max_current_nA. This value is the largest injected
 * current that can be used while keeping the VCO inside its valid operating
 * region for the last measured conductance.
 *
 * Under normal conditions, this function keeps the current aligned with the
 * active range request by computing a range-dependent target below i_dc,max:
 *
 *   LOW    -> i_dc,max - I_guard
 *   MEDIUM -> 3/4 i_dc,max
 *   HIGH   -> 1/2 i_dc,max
 *
 * If repeated directional out-of-range events have been observed, the function
 * also updates the active range request by one step before recomputing the
 * target current:
 *
 *   underflow: Vin too low  -> decrease i_dc -> move LOW->MEDIUM->HIGH
 *   overflow:  Vin too high -> increase i_dc -> move HIGH->MEDIUM->LOW
 *
 */

static void gsr_opctrl_adjust_range(gsr_op_controller_t *ctrl)
{
    uint8_t target_code;
    request_levels_t target_range;
    
    target_range = ctrl->current_request.range;
    ctrl->request_changed = false;


    /*
     * Persistent directional out-of-range events mean the requested range is
     * not robust enough. Move one range step in the corrective direction.
     */
    if (underflow_cnt > MAX_OUT_OF_RANGE_EVENTS) {
        if (target_range < HIGH) {
            target_range = (request_levels_t)(target_range + 1);
        }
        reset_out_of_range_counters();
    } else if (overflow_cnt > MAX_OUT_OF_RANGE_EVENTS) {
        if (target_range > LOW) {
            target_range = (request_levels_t)(target_range - 1);
        }
        reset_out_of_range_counters();
    }

    if (!compute_range_idac_code(ctrl->operating_point->max_current_nA, target_range, &target_code)) {
        // can't satisfy the request even after adjusting it, probably because the max current is too low. 
        // We keep the current range and let the next read decide if we are in underflow/overflow or if it's a measurement error.
        return;
    }

    gsr_controller_set_current(ctrl->operating_point, target_code);
    
    ctrl->request_changed = (target_range != ctrl->current_request.range);
    ctrl->current_request.range = target_range;
}

gsr_opctrl_status_t gsr_opctrl_read_sample(gsr_op_controller_t *ctrl, gsr_sample_t *sample) {
    gsr_opctrl_status_t status;

    if (ctrl == NULL || sample == NULL) return GSR_OPCTRL_INVALID_ARGUMENT;
    if (!ctrl->initialized || ctrl->operating_point == NULL) return GSR_OPCTRL_NOT_INITIALIZED;

    status = gsr_opctrl_status_from_gsr(gsr_read_sample(ctrl->operating_point)); 
    
    if (status == GSR_OPCTRL_MEASUREMENT_UNDERFLOW) { // VIN too low ==> decrease i_dc
        ctrl->has_valid_op = false;
        underflow_cnt++;
        overflow_cnt = 0;
        status = gsr_opctrl_recover_underflow(ctrl);
        return status; // UNDERFLOW or MEASUREMENT_ERROR if we can't recover
    } else if (status == GSR_OPCTRL_MEASUREMENT_OVERFLOW) { // VIN too high ==> increase i_dc
        ctrl->has_valid_op = false;
        overflow_cnt++;
        underflow_cnt = 0;
        status = gsr_opctrl_recover_overflow(ctrl);
        return status; // OVERFLOW or MEASUREMENT_ERROR if we can't recover
    } else if (status != GSR_OPCTRL_OK) { // NOT_INITIALIZED, INVALID_ARGUMENT or MEASUREMENT_ERROR (which includes NO_NEW_SAMPLE and MISSED_UPDATE)
        ctrl->has_valid_op = false;
        return status;
    }

    ctrl->has_valid_op = true;
    *sample = ctrl->operating_point->sample;

    // After a successful read, we want to adjust i_dc to respect range algorithm and request (this can change the request)
    gsr_opctrl_adjust_range(ctrl);

    return status; // could be GSR_OPCTRL_REQUEST_UNSATISFIABLE, GSR_OPCTRL_OK
}

void gsr_opctrl_shutdown(gsr_op_controller_t *ctrl) {
    if (ctrl != NULL) {
        reset_out_of_range_counters();
        ctrl->has_valid_op = false;
        ctrl->initialized = false;
    }
}
