// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Jérémie Moullet
// Description: Driver functions for configuring and accessing the SES (Simple Exponential Smoothing) filter.
//              Provides register-level control of stage activation, gain, decimation, and output sampling.

#ifndef SES_FILTER_H
#define SES_FILTER_H

#include <stdint.h>
#include <stdbool.h>
#include <bitfield.h>
#include "SES_filter_regs.h"
#include "cheep.h"

/*
* @brief Set the control register.
* 
* @param control Control sequence for the filter ({start}).
*/
static inline void SES_set_control_reg(bool control){
#define SES_FILTER_SES_CONTROL_SES_CONTROL_BIT 0
    *(volatile uint32_t *)(SES_FILTER_START_ADDRESS + SES_FILTER_SES_CONTROL_REG_OFFSET) = 
            (control << SES_FILTER_SES_CONTROL_SES_CONTROL_BIT);
}

/*
* @brief Get the status of the SES filter ({Data valid, Control}).
*/
static inline uint32_t SES_get_status() {
    return *(volatile uint32_t *)(SES_FILTER_START_ADDRESS + SES_FILTER_SES_STATUS_REG_OFFSET) & 
            SES_FILTER_SES_STATUS_SES_STATUS_MASK;
}


/*
* @brief Set the SES windows size.
* 
* @param window_size_width bit width of the filter window (log2(window_size)).
*/
static inline void SES_set_window_size(uint32_t window_size_width){
    *(volatile uint32_t *)(SES_FILTER_START_ADDRESS + SES_FILTER_SES_WINDOW_SIZE_REG_OFFSET) = window_size_width;
}

/*
* @brief Set the division factor from the sys clock.
* 
* @param sysclk_division division factor.
*/
static inline void SES_set_sysclk_division(uint32_t sysclk_division) {
    *(volatile uint32_t *)(SES_FILTER_START_ADDRESS + SES_FILTER_SES_SYSCLK_DIVISION_REG_OFFSET) = sysclk_division;
}

/*
* @brief Set the SES decimation factor.
* 
* @param decim_factor decimation factor.
*/
static inline void SES_set_decim_factor(uint32_t decim_factor) {
    *(volatile uint32_t *)(SES_FILTER_START_ADDRESS + SES_FILTER_SES_DECIM_FACTOR_REG_OFFSET) = decim_factor;
}

/*
* @brief Set which SES stage are activated.
* 
* @param activated_stages Thermometric, RHS aligned and contiguous value indicating the "ON" stages.
*/
static inline void SES_set_activated_stages(uint32_t activated_stages) {
    *(volatile uint32_t *)(SES_FILTER_START_ADDRESS + SES_FILTER_SES_ACTIVATED_STAGES_REG_OFFSET) = activated_stages;
}

/*
* @brief Set the gain of a stage of the SES filter.
* 
* @param stage Stage number to set the gain for.
* @param gain Gain value to set.
*/
static inline void SES_set_gain(uint8_t stage, uint32_t gain) {
    volatile uint32_t *reg_addr = (volatile uint32_t *)(SES_FILTER_START_ADDRESS + SES_FILTER_SES_GAIN_STAGE_REG_OFFSET);
    switch (stage) {
        case 0: *reg_addr = bitfield_field32_write(*reg_addr, SES_FILTER_SES_GAIN_STAGE_GAIN_STG_0_FIELD, gain); break;
        case 1: *reg_addr = bitfield_field32_write(*reg_addr, SES_FILTER_SES_GAIN_STAGE_GAIN_STG_1_FIELD, gain); break;
        case 2: *reg_addr = bitfield_field32_write(*reg_addr, SES_FILTER_SES_GAIN_STAGE_GAIN_STG_2_FIELD, gain); break;
        case 3: *reg_addr = bitfield_field32_write(*reg_addr, SES_FILTER_SES_GAIN_STAGE_GAIN_STG_3_FIELD, gain); break;
        case 4: *reg_addr = bitfield_field32_write(*reg_addr, SES_FILTER_SES_GAIN_STAGE_GAIN_STG_4_FIELD, gain); break;
        case 5: *reg_addr = bitfield_field32_write(*reg_addr, SES_FILTER_SES_GAIN_STAGE_GAIN_STG_5_FIELD, gain); break;
    }
}

/*
* @brief Get the SES filtered output.
*/
static inline uint32_t SES_get_filtered_output() {
    return *(volatile uint32_t *)(SES_FILTER_START_ADDRESS + SES_FILTER_RX_DATA_REG_OFFSET);
}

#endif  // SES_FILTER_H