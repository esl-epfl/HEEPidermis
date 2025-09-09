// Copyright 2025 EPFL
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Authors: Jérémie Moullet <jeremie.moullet@epfl.ch>, EPFL, STI-SEL
//
// Date: 06.2025
//
// Description: Single stage of a Simple Exponential Smoothing (SES) filter.
//              Applies exponential smoothing with gain and window control.
//
// Parameters:
//   - MAXIMUM_WIDTH         : Bit-width of the internal datapath.
//   - WINDOW_SIZE_WIDTH     : Bit-width of the window size shift (Ww).
//   - INPUT_GAIN_SIZE_WIDTH : Bit-width of the input gain shift (Wg).
//
// Ports:
//   - rst_ni, clk_i         : Active-low reset and clock.
//   - activated_i           : Enables stage computation.
//   - data_i                : Input sample.
//   - Wg                    : Gain shift applied to input.
//   - Ww                    : Window shift applied to output.
//   - data_o                : Smoothed output sample.
//
// Notes:
//   - Implements: m[i]xb = m[i-1]xb - m[i-1] + s[i]
//                 m[i]   = m[i]xb /b
//   - When deactivated or reset, accumulator is cleared

module ses_stage #(
    parameter integer MAXIMUM_WIDTH = 32,
    parameter integer WINDOW_SIZE_WIDTH = 5,
    parameter integer INPUT_GAIN_SIZE_WIDTH = 5
) (
    input logic                     rst_ni,
    input logic                     clk_i,
    input logic                     activated_i,
    input logic [MAXIMUM_WIDTH-1:0] data_i,

    input logic [WINDOW_SIZE_WIDTH-1:0] Ww,
    input logic [INPUT_GAIN_SIZE_WIDTH-1:0] Wg,

    output logic [MAXIMUM_WIDTH-1:0] data_o
);
  // "Wire" signal
  logic [MAXIMUM_WIDTH-1:0] summed_value;
  logic [MAXIMUM_WIDTH-1:0] shifted_input;
  logic [MAXIMUM_WIDTH-1:0] feedback;

  // "Reg" signal
  logic [MAXIMUM_WIDTH-1:0] r_summed_value;

  // Left-shifted input (from 1-bit data_i)
  assign shifted_input = (activated_i) ? (data_i << Wg) : '0;

  // Two's complement of current output (for feedback)
  assign feedback = ~data_o + 1;

  // Sum calculation
  assign summed_value = r_summed_value + shifted_input + feedback;

  // Sequential register update
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (~rst_ni) begin
      r_summed_value <= '0;
    end else begin
      if (~activated_i) begin
        r_summed_value <= '0;
      end else begin
        r_summed_value <= summed_value;
      end
    end
  end

  // Output value (computed from register)
  assign data_o = r_summed_value >> Ww;

endmodule
