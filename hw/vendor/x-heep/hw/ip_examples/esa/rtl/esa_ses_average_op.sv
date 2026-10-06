// Copyright 2026 EPFL.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// Signed streaming SES average. The recurrence matches ses_stage:
// accumulator += (input << input_gain_shift) - previous_average;
// output = accumulator >>> window_shift.
module esa_ses_average_op (
    input logic clk_i,
    input logic rst_ni,
    input logic enable_i,
    input logic [4:0] window_shift_i,
    input logic [4:0] input_gain_shift_i,
    input logic [31:0] data_in_i,
    input logic data_in_available_i,
    output logic data_in_ready_o,
    output logic [31:0] data_out_o,
    output logic data_out_available_o,
    input logic data_out_ready_i
);
  logic signed [63:0] accumulator_q;
  logic signed [63:0] scaled_input;
  logic signed [63:0] previous_average;
  logic signed [63:0] accumulator_next;
  logic signed [31:0] output_average_next;
  logic [31:0] output_data_q;
  logic output_valid_q;
  logic input_fire;

  assign data_in_ready_o = enable_i && (!output_valid_q || data_out_ready_i);
  assign data_out_available_o = enable_i && output_valid_q;
  assign data_out_o = output_data_q;
  assign input_fire = data_in_available_i && data_in_ready_o;

  always_comb begin
    scaled_input = {{32{data_in_i[31]}}, data_in_i};
    scaled_input = scaled_input <<< input_gain_shift_i;
    previous_average = accumulator_q >>> window_shift_i;
    accumulator_next = accumulator_q + scaled_input - previous_average;
    output_average_next = 32'(accumulator_next >>> window_shift_i);
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      accumulator_q  <= '0;
      output_data_q  <= '0;
      output_valid_q <= 1'b0;
    end else if (!enable_i) begin
      accumulator_q  <= '0;
      output_data_q  <= '0;
      output_valid_q <= 1'b0;
    end else begin
      if (output_valid_q && data_out_ready_i) output_valid_q <= 1'b0;
      if (input_fire) begin
        accumulator_q  <= accumulator_next;
        output_data_q  <= output_average_next;
        output_valid_q <= 1'b1;
      end
    end
  end
endmodule
