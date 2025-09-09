// Copyright 2025 EPFL contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: idac_ctrl.sv
// Author: David Mallasen
// Description: HEEPidermis iDAC controller

module idac_ctrl #(
    parameter int unsigned DELAY_CC = idac_pkg::IdacTrigger2drDelayCc
) (
    input logic clk_i,
    input logic rst_ni,

    // Bus interface
    input  reg_pkg::reg_req_t req_i,
    output reg_pkg::reg_rsp_t rsp_o,

    output logic enable_1_o,
    output logic enable_2_o,

    output logic [idac_pkg::IdacCurrentWidth-1:0] current_1_o,
    output logic [idac_pkg::IdacCalibrationWidth-1:0] calibration_1_o,
    output logic [idac_pkg::IdacCurrentWidth-1:0] current_2_o,
    output logic [idac_pkg::IdacCalibrationWidth-1:0] calibration_2_o,

    output logic refresh_o,
    output logic refresh_notif_o
);

  // Registers --> hardware
  idac_ctrl_reg_pkg::idac_ctrl_reg2hw_t reg2hw;

  // iDAC controller registers
  idac_ctrl_reg_top #(
      .reg_req_t(reg_pkg::reg_req_t),
      .reg_rsp_t(reg_pkg::reg_rsp_t)
  ) u_idac_ctrl_reg_top (
      .clk_i    (clk_i),
      .rst_ni   (rst_ni),
      .reg_req_i(req_i),
      .reg_rsp_o(rsp_o),
      .reg2hw   (reg2hw),
      .devmode_i(1'b0)
  );


  // A counter to use for periodic updates.
  // This timer will not trigger a refresh on the iDAC, but rather will
  // output a trigger signal that can be catched by other blocks that do provide
  // data to the iDAC.
  // The iDAC refresh signal will be controlled (below) by the writing to the
  // current or calibration registers.

  counter_trigger #(
      .TRAIN_LENGTH(1)
  ) u_counter_trigger (
      .clk_i,
      .rst_ni,
      .count_limit_i(reg2hw.refresh_cycles),
      .manual_trigger_i(reg2hw.manual_trigger),
      .trigger_o(refresh_notif_o)
  );

  // The refresh_train signals the iDAC that it should obtain the new values from the
  // calibration and current_in registers
  /* verilator lint_off UNUSED */
  logic [DELAY_CC-1:0] refresh_train;
  /* verilator lint_on UNUSED */

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      refresh_train <= '0;
    end else if (reg2hw.enable.idac1_enable || reg2hw.enable.idac2_enable) begin : refresh_ff_train
      refresh_train[0] <= req_i.write && req_i.valid;
      refresh_train[DELAY_CC-1:1] <= refresh_train[DELAY_CC-2:0];
    end else begin : soft_reset
      refresh_train <= '0;
    end
  end

  assign current_1_o = reg2hw.current.current_1;
  assign calibration_1_o = reg2hw.calibration_1;
  assign enable_1_o = reg2hw.enable.idac1_enable;

  assign current_2_o = reg2hw.current.current_2;
  assign calibration_2_o = reg2hw.calibration_2;
  assign enable_2_o = reg2hw.enable.idac2_enable;

  assign refresh_o = refresh_train[2];

endmodule  // idac_ctrl
