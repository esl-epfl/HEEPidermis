// Copyright 2025 EPFL and contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: counter_trigger.sv
// Author: David Mallasen, Juan Sapriza
// Date: 25/04/2025
// Description: A counter with a register to set a limit.
// Upon reaching the limit, a trigger signal of 1cc is output.
// Several subsequent trigger signals (train) can be obtained.
// Also includes a manual trigger, which resets the count.


module counter_trigger #(
    parameter int unsigned TRAIN_LENGTH = 4
) (
    input logic clk_i,
    input logic rst_ni,
    input logic [31:0] count_limit_i,
    input logic manual_trigger_i,
    output logic [TRAIN_LENGTH-1:0] trigger_o
);

  logic [31:0] count;
  logic manual_trigger_prev;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      count <= '0;
      trigger_o <= '0;
      manual_trigger_prev <= 0;
    end else begin
      manual_trigger_prev <= manual_trigger_i;
      // The counter trigger does not operate if the count limit is 0, but
      // can still be overriden by the manual trigger.
      // If the manual trigger is has a positive edge, it's equivalent to reaching the count limit.
      if ((manual_trigger_i && !manual_trigger_prev) || (count_limit_i != '0 && count == count_limit_i)) begin
        count <= 0;
        trigger_o <= 1;  // Pulse starts, delay pipeline begins
      end else if (count_limit_i != '0) begin
        count <= count + 1;
        /* verilator lint_off SELRANGE */
        /* verilator lint_off WIDTH */
        trigger_o <= (trigger_o << 1'b1);
        /* verilator lint_on SELRANGE */
        /* verilator lint_on WIDTH */
      end else begin
        count <= 0;
        trigger_o <= 0;
      end
    end
  end

endmodule



