// Copyright 2026 EPFL.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// Pass one sample every output_decimation_rate_i accepted input samples.
module esa_decimation_op (
    input logic clk_i,
    input logic rst_ni,
    input logic enable_i,
    input logic [31:0] output_decimation_rate_i,
    input logic [31:0] data_in_i,
    input logic data_in_available_i,
    output logic data_in_ready_o,
    output logic [31:0] data_out_o,
    output logic data_out_available_o,
    input logic data_out_ready_i
);
  logic [31:0] sample_count_q;
  logic emit_sample;
  logic input_fire;

  always_comb begin
    emit_sample = enable_i && data_in_available_i &&
                  (sample_count_q + 32'd1 >= output_decimation_rate_i);
    data_in_ready_o = enable_i && (!emit_sample || data_out_ready_i);
    data_out_available_o = emit_sample;
    data_out_o = data_in_i;
    input_fire = data_in_available_i && data_in_ready_o;
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      sample_count_q <= '0;
    end else if (!enable_i) begin
      sample_count_q <= '0;
    end else if (input_fire) begin
      if (sample_count_q + 32'd1 >= output_decimation_rate_i) begin
        sample_count_q <= '0;
      end else begin
        sample_count_q <= sample_count_q + 1'b1;
      end
    end
  end
endmodule
