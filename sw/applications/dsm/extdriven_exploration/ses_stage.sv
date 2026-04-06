// Copyright 2025 EPFL
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Authors: Jérémie Moullet <jeremie.moullet@epfl.ch>, EPFL, STI-SEL
//          Juan Sapriza <juan.sapriza@eplf.ch>
//   - Implements: m[i]xb = m[i-1]xb - m[i-1] + s[i]
//                 m[i]   = m[i]xb /b


module ses_stage (
    input   logic           rst_ni,
    input   logic           clk_i,
    input   logic   [31:0]  data_i,
    output  logic   [31:0]  data_o
);
  logic [4:0] Ww,
  logic [4:0] Wg,

  logic [31:0] sum;
  logic [31:0] r_sum;
  logic [31:0] data_wg;
  logic [31:0] feedback;

  // Left-shifted input (from 1-bit data_i)
  assign data_wg = data_i << Wg;
  // Sum calculation
  assign sum = r_sum + data_wg - data_o;
  // Sequential register update
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (~rst_ni) begin
      r_sum <= '0;
    end else begin
      if (~activated_i) begin
        r_sum <= '0;
      end else begin
        r_sum <= sum;
      end
    end
  end
  // Output value (computed from register)
  assign data_o = r_sum >> Ww;
endmodule
