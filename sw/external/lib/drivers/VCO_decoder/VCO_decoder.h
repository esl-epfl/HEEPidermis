// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: David Mallasen
// Description: Drivers for the VCO decoder

#ifndef VCO_DECODER_H
#define VCO_DECODER_H

#include <stdint.h>
#include <stdbool.h>
#include "VCO_decoder_regs.h"
#include "cheep.h"

/**
* @brief Enable/disable VCOp
* 
* @param enable enable=true to enable the VCOp, enable=false to disable it.
*/
static inline void VCOp_enable( bool enable ){
    // Reset the VCOp enable bit to 0 and then set it to the new value
    *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_ENABLE_REG_OFFSET) &= ~((uint32_t)1 << VCO_DECODER_ENABLE_P_ENABLE_BIT);
    *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_ENABLE_REG_OFFSET) |= (uint32_t) enable << VCO_DECODER_ENABLE_P_ENABLE_BIT;
}

/**
* @brief Enable/disable VCOn
* 
* @param enable enable=true to enable the VCOn, enable=false to disable it.
*/
static inline void VCOn_enable( bool enable ){
    // Reset the VCOn enable bit to 0 and then set it to the new value
    *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_ENABLE_REG_OFFSET) &= ~((uint32_t) 1 << VCO_DECODER_ENABLE_N_ENABLE_BIT);
    *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_ENABLE_REG_OFFSET) |= (uint32_t) enable << VCO_DECODER_ENABLE_N_ENABLE_BIT;
}

/**
* @brief Set the VCO-ADC refresh rate.
* 
* @param num_cycles Number of cycles to wait before refreshing the VCO-ADC.
*/
static inline void VCO_set_refresh_rate(uint32_t num_cycles) {
    *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_REFRESH_CYCLES_REG_OFFSET) = num_cycles;
}

/**
* @brief Get the VCOp-ADC fine output.
*/
static inline uint32_t VCOp_get_fine() {
    return *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_ADC_P_FINE_OUT_REG_OFFSET) &
            VCO_DECODER_ADC_P_FINE_OUT_ADC_P_FINE_OUT_MASK;
}

/**
* @brief Get the VCOn-ADC fine output.
*/
static inline uint32_t VCOn_get_fine() {
    return *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_ADC_N_FINE_OUT_REG_OFFSET) &
            VCO_DECODER_ADC_N_FINE_OUT_ADC_N_FINE_OUT_MASK;
}

/**
* @brief Get the VCOp-ADC coarse output.
*/
static inline uint32_t VCOp_get_coarse() {
    return *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_ADC_P_COARSE_OUT_REG_OFFSET) &
            VCO_DECODER_ADC_P_COARSE_OUT_ADC_P_COARSE_OUT_MASK;
}

/**
* @brief Get the VCOn-ADC coarse output.
*/
static inline uint32_t VCOn_get_coarse() {
    return *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_ADC_N_COARSE_OUT_REG_OFFSET) &
            VCO_DECODER_ADC_N_COARSE_OUT_ADC_N_COARSE_OUT_MASK;
}

/**
* @brief Get the VCO-ADC count.
*/
static inline uint32_t VCO_get_count() {
    return *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_VCO_DECODER_CNT_REG_OFFSET);
}

/**
* @brief Trigger a single read from the VCO-ADC 
*/
static inline void VCO_trigger(){
    *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_MANUAL_TRIGGER_REG_OFFSET) = 1;
    *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_MANUAL_TRIGGER_REG_OFFSET) = 0;
}

/**
* @brief Set the VCO counter limit.
* 
* @param num_cycles Number of cycles that the VCO counter counts up to (its limit).
*/
static inline void VCO_set_counter_limit(uint32_t num_cycles) {
    *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_COUNTER_LIMIT_REG_OFFSET) = num_cycles;
}

/**
* @brief Set the manual refresh train registers.
* 
* @param train_id train id: whether to trigger the refresh train register 0, 1, or 2.
* @param value whether to set the refresh train register to 0 or 1.
*/
static inline void VCO_set_manual_refresh_train(uint8_t train_id, uint32_t value) {
    if(train_id == 0){
        *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_MANUAL_REFRESH_TRAIN0_REG_OFFSET) = value;
    } else if (train_id == 1) {
        *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_MANUAL_REFRESH_TRAIN1_REG_OFFSET) = value;
    } else if (train_id == 2) {
        *(volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_MANUAL_REFRESH_TRAIN2_REG_OFFSET) = value;
    }
}



#endif  // VCO_DECODER_H