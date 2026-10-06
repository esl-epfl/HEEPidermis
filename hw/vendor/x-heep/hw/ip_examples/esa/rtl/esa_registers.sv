// Copyright 2026 EPFL.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// ESA filter configuration. Write configuration before enabling input traffic.
module esa_registers #(
    parameter bit EnableOnReset = 1'b0
) (
    input logic clk_i,
    input logic rst_ni,
    input reg_pkg::reg_req_t reg_req_i,
    output reg_pkg::reg_rsp_t reg_rsp_o,
    output logic enable_o,
    output logic hpf_enable_o,
    output logic [4:0] esa_window_shift_o,
    output logic [4:0] esa_input_gain_shift_o,
    output logic [4:0] feature_window_shift_o,
    output logic [4:0] feature_input_gain_shift_o,
    output logic [31:0] output_decimation_rate_o
);
  localparam logic [31:0] EnableOffset = 32'h00;
  localparam logic [31:0] HpfEnableOffset = 32'h04;
  localparam logic [31:0] EsaWindowShiftOffset = 32'h08;
  localparam logic [31:0] EsaGainShiftOffset = 32'h0c;
  localparam logic [31:0] FeatureWindowShiftOffset = 32'h10;
  localparam logic [31:0] FeatureGainShiftOffset = 32'h14;
  localparam logic [31:0] OutputDecimationRateOffset = 32'h18;

  logic [31:0] register_write_value;
  logic write_value_valid;

  always_comb begin
    register_write_value = reg_req_i.wdata;
    unique case (reg_req_i.addr)
      HpfEnableOffset: begin
        if (!reg_req_i.wstrb[0]) register_write_value[7:0] = {7'b0, hpf_enable_o};
      end
      EsaWindowShiftOffset: begin
        if (!reg_req_i.wstrb[0]) register_write_value[7:0] = {3'b0, esa_window_shift_o};
      end
      EsaGainShiftOffset: begin
        if (!reg_req_i.wstrb[0]) register_write_value[7:0] = {3'b0, esa_input_gain_shift_o};
      end
      FeatureWindowShiftOffset: begin
        if (!reg_req_i.wstrb[0]) register_write_value[7:0] = {3'b0, feature_window_shift_o};
      end
      FeatureGainShiftOffset: begin
        if (!reg_req_i.wstrb[0]) register_write_value[7:0] = {3'b0, feature_input_gain_shift_o};
      end
      OutputDecimationRateOffset: begin
        for (int byte_idx = 0; byte_idx < 4; byte_idx++) begin
          if (!reg_req_i.wstrb[byte_idx]) begin
            register_write_value[byte_idx*8+:8] = output_decimation_rate_o[byte_idx*8+:8];
          end
        end
      end
      default: ;
    endcase

    write_value_valid = (register_write_value[31:5] == 0);
    reg_rsp_o.rdata   = '0;
    reg_rsp_o.error   = 1'b0;
    reg_rsp_o.ready   = 1'b1;
    if (reg_req_i.valid) begin
      if (!reg_req_i.write) begin
        unique case (reg_req_i.addr)
          EnableOffset: reg_rsp_o.rdata = {31'b0, enable_o};
          HpfEnableOffset: reg_rsp_o.rdata = {31'b0, hpf_enable_o};
          EsaWindowShiftOffset: reg_rsp_o.rdata = {27'b0, esa_window_shift_o};
          EsaGainShiftOffset: reg_rsp_o.rdata = {27'b0, esa_input_gain_shift_o};
          FeatureWindowShiftOffset: reg_rsp_o.rdata = {27'b0, feature_window_shift_o};
          FeatureGainShiftOffset: reg_rsp_o.rdata = {27'b0, feature_input_gain_shift_o};
          OutputDecimationRateOffset: reg_rsp_o.rdata = output_decimation_rate_o;
          default: reg_rsp_o.error = 1'b1;
        endcase
      end else begin
        unique case (reg_req_i.addr)
          EnableOffset, HpfEnableOffset:
          reg_rsp_o.error = !reg_req_i.wstrb[0] || (reg_req_i.wdata[31:1] != 0);
          EsaWindowShiftOffset, EsaGainShiftOffset,
          FeatureWindowShiftOffset, FeatureGainShiftOffset:
          reg_rsp_o.error = !reg_req_i.wstrb[0] || !write_value_valid;
          OutputDecimationRateOffset:
          reg_rsp_o.error = (reg_req_i.wstrb == 0) || (register_write_value == 0);
          default: reg_rsp_o.error = 1'b1;
        endcase
      end
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      enable_o <= EnableOnReset;
      hpf_enable_o <= 1'b1;
      esa_window_shift_o <= '0;
      esa_input_gain_shift_o <= '0;
      feature_window_shift_o <= '0;
      feature_input_gain_shift_o <= '0;
      output_decimation_rate_o <= 32'd1;
    end else if (reg_req_i.valid && reg_req_i.write && !reg_rsp_o.error) begin
      unique case (reg_req_i.addr)
        EnableOffset: if (reg_req_i.wstrb[0]) enable_o <= reg_req_i.wdata[0];
        HpfEnableOffset: if (reg_req_i.wstrb[0]) hpf_enable_o <= reg_req_i.wdata[0];
        EsaWindowShiftOffset: esa_window_shift_o <= register_write_value[4:0];
        EsaGainShiftOffset: esa_input_gain_shift_o <= register_write_value[4:0];
        FeatureWindowShiftOffset: feature_window_shift_o <= register_write_value[4:0];
        FeatureGainShiftOffset: feature_input_gain_shift_o <= register_write_value[4:0];
        OutputDecimationRateOffset: output_decimation_rate_o <= register_write_value;
        default: ;
      endcase
    end
  end
endmodule
