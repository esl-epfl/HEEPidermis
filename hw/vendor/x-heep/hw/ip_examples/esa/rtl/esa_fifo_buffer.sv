// Copyright 2026 EPFL.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// Buffers the HW FIFO input and the generated ESA output, adapting both to
// ready/available streams used by the processing-operation chain.
module esa_fifo_buffer (
    input logic clk_i,
    input logic rst_ni,
    input logic enable_i,
    input fifo_pkg::fifo_req_t hw_fifo_req_i,
    output fifo_pkg::fifo_resp_t hw_fifo_resp_o,
    output logic [31:0] data_out_o,
    output logic data_out_available_o,
    input logic data_out_ready_i,
    input logic [31:0] data_in_i,
    input logic data_in_available_i,
    output logic data_in_ready_o
);
  logic input_fifo_full;
  logic input_fifo_empty;
  logic [1:0] input_fifo_usage;
  logic input_fifo_pop;
  logic output_fifo_full;
  logic output_fifo_push;

  fifo_v3 #(
      .DEPTH(4),
      .FALL_THROUGH(1'b0),
      .DATA_WIDTH(32)
  ) esa_input_fifo_i (
      .clk_i,
      .rst_ni,
      .flush_i(!enable_i || hw_fifo_req_i.flush),
      .testmode_i(1'b0),
      .full_o(input_fifo_full),
      .empty_o(input_fifo_empty),
      .usage_o(input_fifo_usage),
      .data_i(hw_fifo_req_i.data),
      .push_i(hw_fifo_req_i.push && enable_i),
      .data_o(data_out_o),
      .pop_i(input_fifo_pop)
  );

  fifo_v3 #(
      .DEPTH(4),
      .FALL_THROUGH(1'b0),
      .DATA_WIDTH(32)
  ) esa_output_fifo_i (
      .clk_i,
      .rst_ni,
      .flush_i(!enable_i || hw_fifo_req_i.flush),
      .testmode_i(1'b0),
      .full_o(output_fifo_full),
      .empty_o(hw_fifo_resp_o.empty),
      .usage_o(),
      .data_i(data_in_i),
      .push_i(output_fifo_push),
      .data_o(hw_fifo_resp_o.data),
      .pop_i(hw_fifo_req_i.pop && enable_i)
  );

  assign input_fifo_pop = !input_fifo_empty && data_out_ready_i;
  assign output_fifo_push = data_in_available_i && !output_fifo_full;
  assign data_out_available_o = !input_fifo_empty;
  assign data_in_ready_o = !output_fifo_full;
  assign hw_fifo_resp_o.full = input_fifo_full;
  assign hw_fifo_resp_o.alm_full = (input_fifo_usage == 2'd3);
endmodule
