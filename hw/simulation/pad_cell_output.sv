// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: pad_cell_output.sv
// Author: Michele Caon
// Date: 14/05/2023
// Description: Output pad standard cell

module pad_cell_output #(
    parameter int unsigned PADATTR = 16
) (
    input logic pad_in_i,  // pad input value
    input logic pad_oe_i,  // pad output enable
    output logic pad_out_o,  // pad output value
    inout logic pad_io,  // pad value
    input logic [PADATTR-1:0] pad_attributes_i  // pad attributes
);
  // INTERNAL SIGNALS
  logic pad;

  // --------------
  // INTERNAL LOGIC
  // --------------
  assign pad_out_o = 1'b0;
  assign pad_io = pad;
  assign pad = pad_in_i;
endmodule
