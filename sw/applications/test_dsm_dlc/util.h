// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Jérémie Moullet
// Description: Basic utilities for the DSM and dLC test application.
#ifndef UTIL_H
#define UTIL_H

#include <stdint.h>
#include <stdbool.h>
#include "SES_filter.h"

#define PRINTF_IN_SIM 0
#define PRINTF_IN_FPGA 1

#define USE_SES_NOT_CIC
#define FULL_TEST

#define SAMPLE_NUMBER 32769

//Parameters for the SES filter
#define SES_WINDOW_SIZE 4
#define SES_DECIM_FACTOR 32
#define SES_SYSCLK_DIVISION 16
#define SES_ACTIVATED_STAGES 0b1111

#define SES_GAIN_STAGE_0 15
#define SES_GAIN_STAGE_1 0
#define SES_GAIN_STAGE_2 0
#define SES_GAIN_STAGE_3 0
#define SES_GAIN_STAGE_4 0
#define SES_GAIN_STAGE_5 0

//Parameters for the CIC filter
#define CIC_SYSCLK_DIVISION 16 // Must be an even number
#define CIC_DECIM_FACTOR 15    // Can be odd or even
#define CIC_ACTIVATED_STAGES 0b1111
#define CIC_DELAY_COMB 1

//Parameters for the DLC
#ifdef USE_SES_NOT_CIC
    #define LC_PARAMS_DATA_IN_TWOS_COMPLEMENT 0
#else
    #define LC_PARAMS_DATA_IN_TWOS_COMPLEMENT 1
#endif

#define LC_PARAMS_LC_LEVEL_WIDTH_BY_BITS 12
#define LC_PARAMS_LC_ACQUISITION_WORD_SIZE_OF_AMPLITUDE 2
#define LC_PARAMS_LC_ACQUISITION_WORD_SIZE_OF_TIME 6
#define LC_PARAMS_LC_HYSTERESIS_ENABLE 1
#define LC_PARAMS_LC_DISCARD_BITS 0

//Useful stuff
extern const uint32_t NUMBER_OUTPUT;

bool detect_rising_edge(uint32_t status, uint32_t mask);

//Golden truth
extern const uint8_t goldenTruthSES[];
extern const uint16_t  goldenTruthSizeSES;

extern const uint8_t goldenTruthCIC[];
extern const uint16_t  goldenTruthSizeCIC;

#endif