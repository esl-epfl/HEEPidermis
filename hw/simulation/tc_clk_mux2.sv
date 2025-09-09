// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: tc_clk_mux2.sv
// Author: Michele Caon
// Date: 14/05/2023
// Description: 2:1 clock mux simulation model

module tc_clk_mux2 (
    input  logic clk0_i,
    input  logic clk1_i,
    input  logic clk_sel_i,
    output logic clk_o
);
  // select clock
  assign clk_o = (clk_sel_i) ? clk1_i : clk0_i;
endmodule
