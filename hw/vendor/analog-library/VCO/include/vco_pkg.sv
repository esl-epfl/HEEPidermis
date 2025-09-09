// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: vco_pkg.sv
// Author: Juan Sapriza
// Date: 26/04/2025
// Description: Package containing definitions for the VCO-ADC.

package vco_pkg;
    parameter int unsigned VcoTrigger2drDelayCc = 3;
    parameter int unsigned VcoCoarseWidth = 26;
    parameter int unsigned VcoFineWidth   = 31;
    parameter int unsigned VcoFineBinWidth = 6;
    parameter int unsigned VcoDataWidth   = 32;
    parameter int unsigned VcoBhvFreqGain = 100;

endpackage
