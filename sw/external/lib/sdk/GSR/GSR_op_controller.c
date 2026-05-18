// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// File: GSR_op_controller.c
// Author: Ismail Essaidi
// Date: 08/04/2026
// Description: Operating-point request layer for the GSR front-end.

#include "GSR_op_controller.h"

#include <stddef.h>

static bool gsr_opctrl_is_valid_request(const gsr_op_request_t *request) {
    if (request == NULL) {
        return false;
    }

    return (request->range <= HIGH) &&
           (request->resolution <= HIGH) &&
           (request->power <= HIGH);
}

static gsr_opctrl_status_t gsr_opctrl_status_from_gsr(gsr_status_t status) {
    if (status == GSR_STATUS_OK) {
        return GSR_OPCTRL_OK;
    }
    if (status == GSR_STATUS_INVALID_ARGUMENT) {
        return GSR_OPCTRL_INVALID_REQUEST;
    }
    if (status == GSR_STATUS_NOT_INITIALIZED) {
        return GSR_OPCTRL_NOT_INITIALIZED;
    }
    if (status == GSR_STATUS_OUT_OF_RANGE) {
        return GSR_OPCTRL_UNSATISFIABLE;
    }

    return GSR_OPCTRL_MEASUREMENT_ERROR;
}

gsr_opctrl_status_t gsr_opctrl_init(gsr_op_controller_t *ctrl,
                                    gsr_controller_t *controller) {
    if (ctrl == NULL || controller == NULL) {
        return GSR_OPCTRL_INVALID_REQUEST;
    }

    ctrl->controller = controller;
    ctrl->has_active_op = false;
    ctrl->initialized = true;

    return GSR_OPCTRL_OK;
}

/* Translate an application request into concrete controller configuration fields with preconfigured profiles 
* (TODO: will be replaced with dynamic profiling) 
*/
gsr_opctrl_status_t gsr_opctrl_plan(const gsr_op_request_t *request,
                                    gsr_controller_t *operating_point) {
    if (!gsr_opctrl_is_valid_request(request) || operating_point == NULL) {
        return GSR_OPCTRL_INVALID_REQUEST;
    }

    // resolve the request into concrete config fields using the preconfigured profiles in config_profiles.h
    operating_point->config.channel = VCO_CHANNEL_P;
    operating_point->config.idac_code = k_range_profiles[(uint32_t)request->range].idac_code;
    /*
    * We want to control refresh rate because it impacts resolution. 
    * However the way it is implemented now in GSR_controller is by changing modes (which makes sense but is a bit rigid)
    * So to change refresh rate we select the corresponding mode.
    * NOTE 1: In GSR_controller we should decouple refresh rate and modes because what if we want high refresh rates for baseline? 
    * NOTE 2: preconfigured values are in config_profiles.h.
    * TLDR: For now: resolution -> refresh rate (through OSR) -> mode with higher refresh rate in the controller config
    */
    operating_point->mode = k_resolution_profiles[(uint32_t)request->resolution].mode;

    operating_point->config.baseline_refresh_rate_Hz = k_refresh_profiles[0].refresh_rate_Hz;
    operating_point->config.recovery_refresh_rate_Hz = k_refresh_profiles[1].refresh_rate_Hz;
    operating_point->config.phasic_refresh_rate_Hz =  k_refresh_profiles[2].refresh_rate_Hz;

    // placeholder for now because duty cycling the VCO is not implemented yet.
    operating_point->config.D = k_power_profiles[(uint32_t)request->power].D;
    
    return GSR_OPCTRL_OK;
}

/* Apply an already planned operating point through the GSR controller. */
gsr_opctrl_status_t gsr_opctrl_apply(gsr_op_controller_t *ctrl,
                                     const gsr_controller_t *operating_point) {
    gsr_status_t status;
    gsr_config_t merged_config;

    if (ctrl == NULL || operating_point == NULL) return GSR_OPCTRL_INVALID_REQUEST;
    if (!ctrl->initialized || ctrl->controller == NULL) return GSR_OPCTRL_NOT_INITIALIZED;

    // apply the resolved operating point to the controller
    ctrl->controller->config = operating_point->config;
    ctrl->controller->mode = operating_point->mode;

    status = gsr_controller_set_config(ctrl->controller);
    if (status != GSR_STATUS_OK) {
        return gsr_opctrl_status_from_gsr(status);
    }

    ctrl->has_active_op = true;
    return GSR_OPCTRL_OK;
}

gsr_opctrl_status_t gsr_opctrl_request(gsr_op_controller_t *ctrl,
                                       const gsr_op_request_t *request,
                                       gsr_controller_t *operating_point) {
    gsr_controller_t planned_operating_point;
    gsr_opctrl_status_t status;

    if (ctrl == NULL || request == NULL) {
        return GSR_OPCTRL_INVALID_REQUEST;
    }
    if (!ctrl->initialized || ctrl->controller == NULL) {
        return GSR_OPCTRL_NOT_INITIALIZED;
    }

    status = gsr_opctrl_plan(request, &planned_operating_point);
    if (status != GSR_OPCTRL_OK) {
        return status;
    }

    status = gsr_opctrl_apply(ctrl, &planned_operating_point);
    if (status != GSR_OPCTRL_OK) {
        return status;
    }

    if (operating_point != NULL) {
        *operating_point = planned_operating_point;
    }

    return GSR_OPCTRL_OK;
}

gsr_opctrl_status_t gsr_opctrl_read_sample(gsr_op_controller_t *ctrl,
                                           gsr_sample_t *sample) {
    gsr_status_t status;

    if (ctrl == NULL || sample == NULL) {
        return GSR_OPCTRL_INVALID_REQUEST;
    }
    if (!ctrl->initialized || ctrl->controller == NULL) {
        return GSR_OPCTRL_NOT_INITIALIZED;
    }

    status = gsr_read_sample(ctrl->controller);
    if (status != GSR_STATUS_OK) {
        return gsr_opctrl_status_from_gsr(status);
    }

    *sample = ctrl->controller->sample;
    return GSR_OPCTRL_OK;
}

void gsr_opctrl_shutdown(gsr_op_controller_t *ctrl) {
    if (ctrl != NULL) {
        ctrl->has_active_op = false;
        ctrl->initialized = false;
    }
}
