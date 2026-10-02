// Copyright 2026 EPFL.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// First-difference high-pass filter: y[n] = x[n] - x[n-1].
// The result is saturated to signed 32-bit range so the following absolute
// value stage can represent its magnitude as a positive signed value.
module esa_hpf_op (
    input logic clk_i,
    input logic rst_ni,
    input logic enable_i,
    input logic filter_enable_i,
    input logic [31:0] data_in_i,
    input logic data_in_available_i,
    output logic data_in_ready_o,
    output logic [31:0] data_out_o,
    output logic data_out_available_o,
    input logic data_out_ready_i
);
  localparam logic signed [32:0] MaxSigned32 = 33'sh0_7fffffff;
  localparam logic signed [32:0] MinSigned32 = -33'sh0_7fffffff;

  logic [31:0] previous_sample_q;
  logic [31:0] output_data_q;
  logic output_valid_q;
  logic input_fire;
  logic signed [32:0] current_sample_ext;
  logic signed [32:0] previous_sample_ext;
  logic signed [32:0] difference;
  logic [31:0] filtered_sample;

  assign data_in_ready_o = enable_i && (!output_valid_q || data_out_ready_i);
  assign data_out_available_o = enable_i && output_valid_q;
  assign data_out_o = output_data_q;
  assign input_fire = data_in_available_i && data_in_ready_o;

  always_comb begin
    current_sample_ext = $signed({data_in_i[31], data_in_i});
    previous_sample_ext = $signed({previous_sample_q[31], previous_sample_q});
    difference = current_sample_ext - previous_sample_ext;
    filtered_sample = data_in_i;
    if (filter_enable_i) begin
      if (difference > MaxSigned32) begin
        filtered_sample = 32'h7fff_ffff;
      end else if (difference < MinSigned32) begin
        filtered_sample = 32'h8000_0001;
      end else begin
        filtered_sample = difference[31:0];
      end
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      previous_sample_q <= '0;
      output_data_q <= '0;
      output_valid_q <= 1'b0;
    end else if (!enable_i) begin
      previous_sample_q <= '0;
      output_data_q <= '0;
      output_valid_q <= 1'b0;
    end else begin
      if (output_valid_q && data_out_ready_i) output_valid_q <= 1'b0;
      if (input_fire) begin
        previous_sample_q <= data_in_i;
        output_data_q <= filtered_sample;
        output_valid_q <= 1'b1;
      end
    end
  end
endmodule
