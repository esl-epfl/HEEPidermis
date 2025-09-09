// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: idac_pkg.sv
// Author: Juan Sapriza
// Date: 26/04/2025
// Description: Package containing definitions for the VCO-ADC.

package idac_pkg;

    parameter int unsigned IdacTrigger2drDelayCc = 3;
    parameter int unsigned IdacCurrentWidth = 8;
    parameter int unsigned IdacCalibrationWidth = 5;

endpackage
