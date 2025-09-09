// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Jérémie Moullet
// Description: basic test application for the SES filter

#ifndef UTIL_H
#define UTIL_H

#include <stdint.h>
#include <stdbool.h>
#include "SES_filter.h"

//#define EXTENDED_TEST

//Parameters for the SES filter
//#define PRINTF_OUTPUT

#ifndef EXTENDED_TEST
#define SES_SAMPLE_NUMBER 161

#define SES_WINDOW_SIZE 4
#define SES_DECIM_FACTOR 32
#define SES_SYSCLK_DIVISION 128
#define SES_ACTIVATED_STAGES 0b111111

#define SES_GAIN_STAGE_0 10
#define SES_GAIN_STAGE_1 2
#define SES_GAIN_STAGE_2 2
#define SES_GAIN_STAGE_3 2
#define SES_GAIN_STAGE_4 2
#define SES_GAIN_STAGE_5 2

#else
#define SES_SAMPLE_NUMBER 32769

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
#endif

//Useful stuff
extern const uint32_t NUMBER_OUTPUT;

bool detect_rising_edge(uint32_t status, uint32_t mask);

#endif