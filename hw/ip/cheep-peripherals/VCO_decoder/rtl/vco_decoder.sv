// Copyright 2025 EPFL contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: vco_decoder.sv
// Author: David Mallasen
// Description: HEEPidermis VCO decoder

module vco_decoder #(
    parameter int unsigned DELAY_CC = vco_pkg::VcoTrigger2drDelayCc
) (
    input logic clk_i,
    input logic rst_ni,

    // Bus interface
    input  reg_pkg::reg_req_t req_i,
    output reg_pkg::reg_rsp_t rsp_o,

    output logic p_enable_o,
    output logic n_enable_o,

    // Interface with VCO
    input logic [vco_pkg::VcoCoarseWidth-1:0] p_coarse_i,
    input logic [vco_pkg::VcoCoarseWidth-1:0] n_coarse_i,
    input logic [vco_pkg::VcoFineWidth-1:0] p_fine_i,
    input logic [vco_pkg::VcoFineWidth-1:0] n_fine_i,
    output logic counter_overflow_o,
    output logic refresh_o,
    output logic refresh_notif_o
);

  // Hardware --> Registers
  vco_decoder_reg_pkg::vco_decoder_hw2reg_t hw2reg;

  // Registers --> hardware
  vco_decoder_reg_pkg::vco_decoder_reg2hw_t reg2hw;

  // VCO decoder registers
  vco_decoder_reg_top #(
      .reg_req_t(reg_pkg::reg_req_t),
      .reg_rsp_t(reg_pkg::reg_rsp_t)
  ) u_vco_decoder_reg_top (
      .clk_i    (clk_i),
      .rst_ni   (rst_ni),
      .reg_req_i(req_i),
      .reg_rsp_o(rsp_o),
      .reg2hw   (reg2hw),
      .hw2reg   (hw2reg),
      .devmode_i(1'b0)
  );

  // Generate a refresh signal every reg2hw.refresh_cycles cycles, as
  // long as the VCO is enabled. Also generate a delayed version of the
  // refresh signal 1, 2, and 3 cycles later.
  logic [DELAY_CC-1:0] refresh_train;

  counter_trigger #(
      .TRAIN_LENGTH(DELAY_CC)
  ) u_counter_trigger (
      .clk_i,
      .rst_ni,
      .count_limit_i(reg2hw.refresh_cycles),
      .manual_trigger_i(reg2hw.manual_trigger),
      .trigger_o(refresh_train)
  );

  assign refresh_o = refresh_train[0] | reg2hw.manual_refresh_train0;

  // Set the values of the registers (d) and enable them (de) at the appropriate time.
  // First the VCO sets the coarse and fine values and then the vco_computation
  assign hw2reg.adc_p_fine_out.d = p_fine_i;
  assign hw2reg.adc_p_fine_out.de = refresh_train[1] | reg2hw.manual_refresh_train1;
  assign hw2reg.adc_n_fine_out.d = n_fine_i;
  assign hw2reg.adc_n_fine_out.de = refresh_train[1] | reg2hw.manual_refresh_train1;
  assign hw2reg.adc_p_coarse_out.d = p_coarse_i;
  assign hw2reg.adc_p_coarse_out.de = refresh_train[1] | reg2hw.manual_refresh_train1;
  assign hw2reg.adc_n_coarse_out.d = n_coarse_i;
  assign hw2reg.adc_n_coarse_out.de = refresh_train[1] | reg2hw.manual_refresh_train1;

  logic [31:0] p_decoder_cnt;
  logic [31:0] n_decoder_cnt;
  logic [31:0] decoder_cnt;

  vco_computation u_vco_p_computation (
      .rstn_i(rst_ni),
      .clk_i(refresh_train[1] | reg2hw.manual_refresh_train1),  // 1 cycle delayed refresh signal
      .phasesS2(p_fine_i),
      .coarsecAS(p_coarse_i),
      .digdata_o(p_decoder_cnt)
  );

  vco_computation u_vco_n_computation (
      .rstn_i(rst_ni),
      .clk_i(refresh_train[1] | reg2hw.manual_refresh_train1),  // 1 cycle delayed refresh signal
      .phasesS2(n_fine_i),
      .coarsecAS(n_coarse_i),
      .digdata_o(n_decoder_cnt)
  );

  // If both VCOs are enabled, the decoder count is the difference between the two VCOs.
  // Otherwise, it is the count of the enabled VCO, if any.
  always_comb begin
    if (p_enable_o && n_enable_o) begin
      decoder_cnt = p_decoder_cnt - n_decoder_cnt;
    end else if (p_enable_o) begin
      decoder_cnt = p_decoder_cnt;
    end else if (n_enable_o) begin
      decoder_cnt = n_decoder_cnt;
    end else begin
      decoder_cnt = '0;
    end
  end

  // sets the decoder count (output of the decoders, computed from the coarse and fine counts)
  assign hw2reg.vco_decoder_cnt.d = decoder_cnt;
  assign hw2reg.vco_decoder_cnt.de = refresh_train[2] | reg2hw.manual_refresh_train2;
  assign refresh_notif_o = refresh_train[2] | reg2hw.manual_refresh_train2;
  assign p_enable_o = reg2hw.enable.p_enable;
  assign n_enable_o = reg2hw.enable.n_enable;

  // VCO counter
  // The VCO counter is a 32-bit counter that counts the number of ticks
  // of the P0 pin of the VCO. It is incremented on the rising edge of
  // the P0 pin, and it is reset to zero when it reaches the reg2hw.counter_limit
  // value. When the counter is reset, it also triggers a pulse on the
  // counter_overflow_o output signal.
  logic p0_d;
  logic p0_rising_edge;
  logic [31:0] vco_counter;

  // Detect rising edge of P0
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      p0_d <= 1'b0;
    end else begin
      p0_d <= p_fine_i[0];
    end
  end

  assign p0_rising_edge = p_fine_i[0] & ~p0_d;

  // VCO counter logic
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      vco_counter <= '0;
      counter_overflow_o <= 1'b0;
    end else begin
      if (p0_rising_edge) begin
        if (vco_counter == reg2hw.counter_limit) begin
          vco_counter <= '0;
          counter_overflow_o <= 1'b1;  // Trigger overflow pulse
        end else begin
          vco_counter <= vco_counter + 1;
          counter_overflow_o <= 1'b0;
        end
      end else begin
        counter_overflow_o <= 1'b0;
      end
    end
  end

endmodule  // vco_decoder
