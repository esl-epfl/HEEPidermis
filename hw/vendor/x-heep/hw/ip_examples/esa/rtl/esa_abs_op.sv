// Copyright 2026 EPFL.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// Combinational absolute-value stage. The unsigned zero is the code 1<<zero_bit_i.
module esa_abs_op (
    input logic enable_i,
    input logic signed_mode_i,
    input logic [4:0] zero_bit_i,
    input logic [31:0] data_in_i,
    input logic data_in_available_i,
    output logic data_in_ready_o,
    output logic [31:0] data_out_o,
    output logic data_out_available_o,
    input logic data_out_ready_i
);
  logic [31:0] signed_magnitude;
  logic [31:0] unsigned_distance;
  logic prefix_any;
  logic at_or_above_zero;

  always_comb begin
    // Two's-complement magnitude: bit 0 is unchanged, and each higher bit is
    // toggled when any lower input bit is set. This avoids a subtractor.
    signed_magnitude = data_in_i;
    prefix_any = 1'b0;
    for (int bit_idx = 0; bit_idx < 32; bit_idx++) begin
      if (bit_idx != 0) signed_magnitude[bit_idx] = data_in_i[bit_idx] ^ prefix_any;
      prefix_any = prefix_any | data_in_i[bit_idx];
    end

    // For offset-binary input, compute |data_in_i - 2**zero_bit_i|. The
    // upper side uses a decrementer; the lower side uses two's-complement
    // bit logic. No general subtractor or barrel shifter is needed.
    at_or_above_zero = 1'b0;
    for (int bit_idx = 0; bit_idx < 32; bit_idx++) begin
      if ((bit_idx >= int'(zero_bit_i)) && data_in_i[bit_idx]) begin
        at_or_above_zero = 1'b1;
      end
    end

    unsigned_distance = '0;
    if (at_or_above_zero) begin
      prefix_any = 1'b0;
      for (int bit_idx = 0; bit_idx < 32; bit_idx++) begin
        if (bit_idx < int'(zero_bit_i)) begin
          unsigned_distance[bit_idx] = data_in_i[bit_idx];
        end else begin
          unsigned_distance[bit_idx] = data_in_i[bit_idx] ^ !prefix_any;
          prefix_any = prefix_any | data_in_i[bit_idx];
        end
      end
    end else if (data_in_i == 0) begin
      unsigned_distance[zero_bit_i] = 1'b1;
    end else begin
      prefix_any = 1'b0;
      for (int bit_idx = 0; bit_idx < 32; bit_idx++) begin
        if (bit_idx < int'(zero_bit_i)) begin
          unsigned_distance[bit_idx] = data_in_i[bit_idx] ^ prefix_any;
          prefix_any = prefix_any | data_in_i[bit_idx];
        end
      end
    end

    if (signed_mode_i) begin
      data_out_o = data_in_i[31] ? signed_magnitude : data_in_i;
    end else begin
      data_out_o = unsigned_distance;
    end
  end

  assign data_out_available_o = enable_i && data_in_available_i;
  assign data_in_ready_o = enable_i && data_out_ready_i;
endmodule
