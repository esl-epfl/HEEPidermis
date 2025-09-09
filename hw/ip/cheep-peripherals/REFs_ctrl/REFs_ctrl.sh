# Copyright 2025 EPFL contributors
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# File: REFs_ctrl.sh
# Author: Michele Caon, David Mallasen
# Description: Script to generate the HEEPidermis REFs controller registers

REG_DIR=$(dirname -- $0)
ROOT=$(realpath "$(dirname -- $0)/../../../..")
REGTOOL=$ROOT/hw/vendor/x-heep/hw/vendor/pulp_platform_register_interface/vendor/lowrisc_opentitan/util/regtool.py
HJSON_FILE=$REG_DIR/data/REFs_ctrl.hjson
RTL_DIR=$REG_DIR/rtl
SW_DIR=$ROOT/sw/external/lib/drivers/REFs_ctrl

mkdir -p $RTL_DIR $SW_DIR

printf -- "Generating REFs_ctrl registers RTL..."
$REGTOOL -r -t $RTL_DIR $HJSON_FILE
[ $? -eq 0 ] && printf " OK\n" || exit $?

printf -- "Generating REFs_ctrl software header..."
$REGTOOL --cdefines -o $SW_DIR/REFs_ctrl_regs.h $HJSON_FILE
[ $? -eq 0 ] && printf " OK\n" || exit $?

printf -- "Generating REFs_ctrl documentation..."
$REGTOOL -d $HJSON_FILE > $SW_DIR/REFs_ctrl_regs.md
[ $? -eq 0 ] && printf " OK\n" || exit $?
