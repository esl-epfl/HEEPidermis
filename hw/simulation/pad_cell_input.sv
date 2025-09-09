// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: pad_cell_input.sv
// Author: Michele Caon
// Date: 14/05/2023
// Description: Inpud pad standard cell simulation model

// NOTE: Based on the same cell from HEEPocrates (https://eslgit.epfl.ch/heep/HEEPpocrates)

module pad_cell_input #(
    parameter int unsigned PAD_ATTR = 16
) (
    input logic pad_in_i,  // pad input value
    input logic pad_oe_i,  // pad output enable
    output logic pad_out_o,  // pad output value
    input logic pad_io,  // pad value
    input logic [PAD_ATTR-1:0] pad_attributes_i  // pad attributes
);
  // INTERNAL SIGNALS
  logic pad;

  // --------------
  // INTERNAL LOGIC
  // --------------
  assign pad_out_o = pad_io;
  assign pad_io = pad;
  assign pad = 1'bz;

  // ----------
  // ASSERTIONS
  // ----------
`ifndef SYNTHESIS
  `include "assertions/pad_cell_input_sva.svh"
`endif  // SYNTHESIS
endmodule
