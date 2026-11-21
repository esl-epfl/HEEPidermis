// Copyright 2026 EPFL.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// ESA streaming feature pipeline:
// signed input -> high-pass -> absolute value -> ESA SES average
//              -> ESA feature SES average -> output decimation -> HW FIFO.
module esa #(
    parameter bit EnableVisualizationLatches = 1'b1,
    parameter bit EnableOnReset = 1'b0,
    parameter int unsigned InputSamplesPerTransaction = 0
) (
    input logic clk_i,
    input logic rst_ni,

    input  reg_pkg::reg_req_t reg_req_i,
    output reg_pkg::reg_rsp_t reg_rsp_o,

    input fifo_pkg::fifo_req_t hw_fifo_req_i,
    output fifo_pkg::fifo_resp_t hw_fifo_resp_o,
    output logic esa_done_o
);
  /* verilator lint_off UNUSED */
  logic enable_q;
  logic hpf_enable_q;
  logic [4:0] esa_window_shift_q;
  logic [4:0] esa_input_gain_shift_q;
  logic [4:0] feature_window_shift_q;
  logic [4:0] feature_input_gain_shift_q;
  logic [31:0] output_decimation_rate_q;

  logic [31:0] input_fifo_data;
  logic input_fifo_available;
  logic input_fifo_ready;
  logic [31:0] hpf_output_data;
  logic hpf_output_available;
  logic hpf_output_ready;
  logic [31:0] abs_output_data;
  logic abs_output_available;
  logic abs_output_ready;
  logic [31:0] esa_output_data;
  logic esa_output_available;
  logic esa_output_ready;
  logic [31:0] feature_output_data;
  logic feature_output_available;
  logic feature_output_ready;
  logic [31:0] decimated_output_data;
  logic decimated_output_available;
  logic decimated_output_ready;

  logic [31:0] input_data_hold_q;
  logic [31:0] output_data_hold_q;
  logic [31:0] accepted_samples_q;
  logic input_sample_fire;
  logic pipeline_enable;
  logic pipeline_empty;

  assign pipeline_enable = enable_q && !hw_fifo_req_i.flush;

  esa_registers #(
      .EnableOnReset(EnableOnReset)
  ) esa_registers_i (
      .clk_i,
      .rst_ni,
      .reg_req_i,
      .reg_rsp_o,
      .enable_o(enable_q),
      .hpf_enable_o(hpf_enable_q),
      .esa_window_shift_o(esa_window_shift_q),
      .esa_input_gain_shift_o(esa_input_gain_shift_q),
      .feature_window_shift_o(feature_window_shift_q),
      .feature_input_gain_shift_o(feature_input_gain_shift_q),
      .output_decimation_rate_o(output_decimation_rate_q)
  );

  esa_fifo_buffer esa_fifo_buffer_i (
      .clk_i,
      .rst_ni,
      .enable_i(enable_q),
      .hw_fifo_req_i,
      .hw_fifo_resp_o,
      .data_out_o(input_fifo_data),
      .data_out_available_o(input_fifo_available),
      .data_out_ready_i(input_fifo_ready),
      .data_in_i(decimated_output_data),
      .data_in_available_i(decimated_output_available),
      .data_in_ready_o(decimated_output_ready)
  );

  esa_hpf_op esa_hpf_op_i (
      .clk_i,
      .rst_ni,
      .enable_i(pipeline_enable),
      .filter_enable_i(hpf_enable_q),
      .data_in_i(input_fifo_data),
      .data_in_available_i(input_fifo_available),
      .data_in_ready_o(input_fifo_ready),
      .data_out_o(hpf_output_data),
      .data_out_available_o(hpf_output_available),
      .data_out_ready_i(hpf_output_ready)
  );

  esa_abs_op esa_abs_op_i (
      .enable_i(pipeline_enable),
      .signed_mode_i(1'b1),
      .zero_bit_i(5'b0),
      .data_in_i(hpf_output_data),
      .data_in_available_i(hpf_output_available),
      .data_in_ready_o(hpf_output_ready),
      .data_out_o(abs_output_data),
      .data_out_available_o(abs_output_available),
      .data_out_ready_i(abs_output_ready)
  );

  esa_ses_average_op esa_average_op_i (
      .clk_i,
      .rst_ni,
      .enable_i(pipeline_enable),
      .window_shift_i(esa_window_shift_q),
      .input_gain_shift_i(esa_input_gain_shift_q),
      .data_in_i(abs_output_data),
      .data_in_available_i(abs_output_available),
      .data_in_ready_o(abs_output_ready),
      .data_out_o(esa_output_data),
      .data_out_available_o(esa_output_available),
      .data_out_ready_i(esa_output_ready)
  );

  esa_ses_average_op esa_feature_average_op_i (
      .clk_i,
      .rst_ni,
      .enable_i(pipeline_enable),
      .window_shift_i(feature_window_shift_q),
      .input_gain_shift_i(feature_input_gain_shift_q),
      .data_in_i(esa_output_data),
      .data_in_available_i(esa_output_available),
      .data_in_ready_o(esa_output_ready),
      .data_out_o(feature_output_data),
      .data_out_available_o(feature_output_available),
      .data_out_ready_i(feature_output_ready)
  );

  esa_decimation_op esa_decimation_op_i (
      .clk_i,
      .rst_ni,
      .enable_i(pipeline_enable),
      .output_decimation_rate_i(output_decimation_rate_q),
      .data_in_i(feature_output_data),
      .data_in_available_i(feature_output_available),
      .data_in_ready_o(feature_output_ready),
      .data_out_o(decimated_output_data),
      .data_out_available_o(decimated_output_available),
      .data_out_ready_i(decimated_output_ready)
  );

  assign input_sample_fire = input_fifo_available && input_fifo_ready;
  assign pipeline_empty = !input_fifo_available && !hpf_output_available &&
                          !esa_output_available && !feature_output_available;
  if (InputSamplesPerTransaction != 0) begin : gen_transaction_done
    assign esa_done_o = enable_q && !hw_fifo_req_i.flush &&
                        (accepted_samples_q >= InputSamplesPerTransaction) &&
                        pipeline_empty && hw_fifo_resp_o.empty;

    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        accepted_samples_q <= '0;
      end else if (!enable_q || hw_fifo_req_i.flush) begin
        accepted_samples_q <= '0;
      end else if (input_sample_fire && (accepted_samples_q < InputSamplesPerTransaction)) begin
        accepted_samples_q <= accepted_samples_q + 1'b1;
      end
    end
  end else begin : gen_no_transaction_done
    assign esa_done_o = 1'b0;
    always_comb accepted_samples_q = '0;
  end

  if (EnableVisualizationLatches) begin : gen_visualization_latches
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        input_data_hold_q  <= '0;
        output_data_hold_q <= '0;
      end else if (!enable_q) begin
        input_data_hold_q  <= '0;
        output_data_hold_q <= '0;
      end else begin
        if (input_sample_fire) input_data_hold_q <= input_fifo_data;
        if (decimated_output_available && decimated_output_ready)
          output_data_hold_q <= decimated_output_data;
      end
    end
  end else begin : gen_no_visualization_latches
    always_comb begin
      input_data_hold_q  = '0;
      output_data_hold_q = '0;
    end
  end
  /* verilator lint_on UNUSED */
endmodule
