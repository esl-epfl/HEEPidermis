// Copyright 2025 EPFL contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: amux_ctrl.sv
// Author: David Mallasen, Juan Sapriza
// Description: HEEPidermis aMUX controller

module amux_ctrl (
    input logic clk_i,
    input logic rst_ni,

    // Bus interface
    input  reg_pkg::reg_req_t req_i,
    output reg_pkg::reg_rsp_t rsp_o,

    output logic [amux_pkg::AmuxSelWidth-1:0] sel_o
);

  // Registers --> hardware
  amux_ctrl_reg_pkg::amux_ctrl_reg2hw_t reg2hw;

  // iDAC controller registers
  amux_ctrl_reg_top #(
      .reg_req_t(reg_pkg::reg_req_t),
      .reg_rsp_t(reg_pkg::reg_rsp_t)
  ) u_amux_ctrl_reg_top (
      .clk_i    (clk_i),
      .rst_ni   (rst_ni),
      .reg_req_i(req_i),
      .reg_rsp_o(rsp_o),
      .reg2hw   (reg2hw),
      .devmode_i(1'b0)
  );

  // Make sure that sel_o has at most one bit set to 1, prioritizing the LSB
  assign sel_o = reg2hw.sel & (~reg2hw.sel + 1'b1);

endmodule  // amux_ctrl
