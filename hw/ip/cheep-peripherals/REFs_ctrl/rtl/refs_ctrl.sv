// Copyright 2025 EPFL contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: refs_ctrl.sv
// Author: David Mallasen
// Description: HEEPidermis Refs controller

module refs_ctrl (
    input logic clk_i,
    input logic rst_ni,

    // Bus interface
    input  reg_pkg::reg_req_t req_i,
    output reg_pkg::reg_rsp_t rsp_o,

    // iREF signals
    output logic [iref_pkg::IrefCalibrationWidth-1:0] iref1_calibration_o,
    output logic [iref_pkg::IrefCalibrationWidth-1:0] iref2_calibration_o,

    // vREF signals
    output logic [vref_pkg::VrefCalibrationWidth-1:0] vref_calibration_o
);

  // Registers --> hardware
  refs_ctrl_reg_pkg::refs_ctrl_reg2hw_t reg2hw;

  // Refs controller registers
  refs_ctrl_reg_top #(
      .reg_req_t(reg_pkg::reg_req_t),
      .reg_rsp_t(reg_pkg::reg_rsp_t)
  ) u_refs_ctrl_reg_top (
      .clk_i    (clk_i),
      .rst_ni   (rst_ni),
      .reg_req_i(req_i),
      .reg_rsp_o(rsp_o),
      .reg2hw   (reg2hw),
      .devmode_i(1'b0)
  );

  assign iref1_calibration_o = reg2hw.iref1_calibration;
  assign iref2_calibration_o = reg2hw.iref2_calibration;
  assign vref_calibration_o  = reg2hw.vref_calibration;

endmodule  // idac_ctrl
