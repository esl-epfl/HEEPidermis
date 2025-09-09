// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: Jérémie Moullet
// Description: basic test application for the SES filter

#include "basic_test.h"
#include "extended_test.h"
#include "util.h"

int main() {
    #ifndef EXTENDED_TEST
    return basic_test();
    #else
    return extended_test();
    #endif
}