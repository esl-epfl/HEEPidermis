# Copyright 2025 EPFL contributors
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# File: SES_filter.sh
# Author: Jérémie Moullet
# Description: Script to generate the HEEPidermis SES_filter controller registers

REG_DIR=$(dirname $0)
ROOT=$(realpath "$(dirname $0)/../../../..")
REGTOOL=$ROOT/hw/vendor/x-heep/hw/vendor/pulp_platform_register_interface/vendor/lowrisc_opentitan/util/regtool.py
HJSON_FILE=$REG_DIR/data/SES_filter.hjson
RTL_DIR=$REG_DIR/rtl
SW_DIR=$ROOT/sw/external/lib/drivers/SES_filter

mkdir -p $RTL_DIR $SW_DIR
printf -- "Generating SES_filter registers RTL..."
$REGTOOL -r -t $RTL_DIR $HJSON_FILE
[ $? -eq 0 ] && printf " OK\n" || exit $?
printf -- "Generating SES_filter software header..."
$REGTOOL --cdefines -o $SW_DIR/SES_filter_regs.h $HJSON_FILE
$REGTOOL -d $HJSON_FILE > $SW_DIR/SES_filter_regs.md
[ $? -eq 0 ] && printf " OK\n" || exit $?