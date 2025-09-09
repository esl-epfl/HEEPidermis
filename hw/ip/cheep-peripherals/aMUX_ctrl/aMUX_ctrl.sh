#!/bin/bash
# Copyright 2025 EPFL contributors
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# File: iDAC-ctrl.sh
# Author: Michele Caon
# Description: Script to generate the HEEPidermis iDAC controller registers
BLOCK_NAME="aMUX_ctrl"
REG_DIR=$(dirname -- $0)
ROOT=$(realpath "$(dirname -- $0)/../../../..")
REGTOOL=$ROOT/hw/vendor/x-heep/hw/vendor/pulp_platform_register_interface/vendor/lowrisc_opentitan/util/regtool.py
HJSON_FILE="${REG_DIR}/data/${BLOCK_NAME}.hjson"
RTL_DIR=$REG_DIR/rtl
SW_DIR="${ROOT}/sw/external/lib/drivers/${BLOCK_NAME}"

mkdir -p $RTL_DIR $SW_DIR
printf -- "Generating ${BLOCK_NAME}_ctrl registers RTL..."
$REGTOOL -r -t $RTL_DIR $HJSON_FILE
[ $? -eq 0 ] && printf " OK\n" || exit $?
printf -- "Generating ${BLOCK_NAME} software header..."
$REGTOOL --cdefines -o "${SW_DIR}/${BLOCK_NAME}_regs.h" $HJSON_FILE
$REGTOOL -d $HJSON_FILE > "${SW_DIR}/${BLOCK_NAME}_regs.md"
[ $? -eq 0 ] && printf " OK\n" || exit $?
