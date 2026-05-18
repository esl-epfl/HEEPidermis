// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// File: GSR_op_controller.h
// Author: Ismail Essaidi
// Date: 08/04/2026
// Description: Operating-point request layer for the GSR front-end.

#ifndef GSR_OP_CONTROLLER_H
#define GSR_OP_CONTROLLER_H

#include <stdbool.h>
#include <stdint.h>

#include "GSR_controller.h"

/* Status codes returned by the operating-point controller. */
typedef enum {
    GSR_OPCTRL_OK = 0,
    GSR_OPCTRL_INVALID_REQUEST,
    GSR_OPCTRL_UNSATISFIABLE,
    GSR_OPCTRL_NOT_INITIALIZED,
    GSR_OPCTRL_NO_VALID_SAMPLE,
    GSR_OPCTRL_MEASUREMENT_ERROR
} gsr_opctrl_status_t;

/*
 * Conductance range request. Higher range uses a higher injected current and
 * therefore consumes more power; The is a tradoff between measurable conductance range and power consumption.
 */
typedef enum {
    LOW = 0,
    MEDIUM = 1,
    HIGH = 2,
} request_levels_t;

/* Application-level intent. This is what the main application should request. */
typedef struct {
    request_levels_t range;               /* Low/medium/high conductance range. */
    request_levels_t resolution;       /* Low/medium/high VCO readout resolution. */
    request_levels_t power;              /* Power configuration. */
} gsr_op_request_t;

/* Deferred range event raised by interrupt-side logic. */
typedef enum {
    GSR_OPCTRL_RANGE_EVENT_NONE = 0,
    GSR_OPCTRL_RANGE_EVENT_VIN_TOO_LOW,
    GSR_OPCTRL_RANGE_EVENT_VIN_TOO_HIGH,
} gsr_opctrl_range_event_t;

/* Thin request layer state. The GSR controller owns measurement/config state. */
typedef struct {
    gsr_controller_t *controller;    /* Lower-layer controller configured by requests. */

    bool has_active_op;              /* True after a successful apply/request. */
    bool initialized;                /* True after gsr_opctrl_init(). */
} gsr_op_controller_t;

/* Initialize the operating-point controller with the GSR controller it drives. */
gsr_opctrl_status_t gsr_opctrl_init(gsr_op_controller_t *ctrl,
                                    gsr_controller_t *controller);

/* Translate an application request into concrete controller configuration fields. */
gsr_opctrl_status_t gsr_opctrl_plan(const gsr_op_request_t *request,
                                    gsr_controller_t *operating_point);

/* Apply an already planned operating point through the GSR controller. */
gsr_opctrl_status_t gsr_opctrl_apply(gsr_op_controller_t *ctrl,
                                     const gsr_controller_t *operating_point);

/* Plan and apply a request in one call. */
gsr_opctrl_status_t gsr_opctrl_request(gsr_op_controller_t *ctrl,
                                       const gsr_op_request_t *request,
                                       gsr_controller_t *operating_point);

/* Read one sample through the GSR controller. */
gsr_opctrl_status_t gsr_opctrl_read_sample(gsr_op_controller_t *ctrl,
                                           gsr_sample_t *sample);
                                           
/* Clear op-controller state. Hardware shutdown remains owned by lower layers. */
void gsr_opctrl_shutdown(gsr_op_controller_t *ctrl);

#endif /* GSR_OP_CONTROLLER_H */
