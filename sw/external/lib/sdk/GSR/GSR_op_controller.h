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
    GSR_OPCTRL_INVALID_ARGUMENT,
    GSR_OPCTRL_NOT_INITIALIZED,
    GSR_OPCTRL_REQUEST_INVALID,
    GSR_OPCTRL_REQUEST_UNSATISFIABLE,
    GSR_OPCTRL_MEASUREMENT_UNDERFLOW,
    GSR_OPCTRL_MEASUREMENT_OVERFLOW,
    GSR_OPCTRL_MEASUREMENT_ERROR
} gsr_opctrl_status_t;

/*
 * Conductance range request. Higher range uses a lower i_dc
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

/* Thin request layer state. The GSR controller owns measurement/config state. */
typedef struct {
    gsr_controller_t *operating_point;    /* Lower-layer operating point configured by requests. */
    gsr_op_request_t current_request; /* The most recent valid request. */
    bool request_changed;          /* True if the current request differs from the last request. (changed by automatic range adjustment) */
    bool has_valid_op;              /* True after a successful apply/request that resulted in a valid sample. */
    bool initialized;                /* True after gsr_opctrl_init(). */
} gsr_op_controller_t;

/* Initialize the operating-point controller with the GSR controller it drives. */
gsr_opctrl_status_t gsr_opctrl_init(gsr_op_controller_t *ctrl, gsr_controller_t *controller);

/* Plan and apply a request. */
gsr_opctrl_status_t gsr_opctrl_request(gsr_op_controller_t *ctrl,
                                       const gsr_op_request_t *request,
                                       gsr_controller_t *operating_point);

/* Read one sample through the GSR controller. */
gsr_opctrl_status_t gsr_opctrl_read_sample(gsr_op_controller_t *ctrl, gsr_sample_t *sample);
                                           
/* Clear op-controller state. Hardware shutdown remains owned by lower layers. */
void gsr_opctrl_shutdown(gsr_op_controller_t *ctrl);

#endif /* GSR_OP_CONTROLLER_H */
