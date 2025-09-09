// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: periph_bus.sv
// Author: Michele Caon
// Date: 01/06/2023
// Description: External peripherals crossbar for cheep

module periph_bus #(
    parameter  int unsigned NSLAVE   = 2,
    // Dependent parameters: do not override!
    localparam int unsigned IdxWidth = cf_math_pkg::idx_width(NSLAVE)
) (
    input logic clk_i,
    input logic rst_ni,

    // Address map
    input addr_map_rule_pkg::addr_map_rule_t [NSLAVE-1:0] addr_map_i,

    // Master port
    input  reg_pkg::reg_req_t master_req_i,
    output reg_pkg::reg_rsp_t master_rsp_o,

    // Slave ports
    output reg_pkg::reg_req_t [NSLAVE-1:0] slave_req_o,
    input  reg_pkg::reg_rsp_t [NSLAVE-1:0] slave_rsp_i
);
  import reg_pkg::*;
  import cheep_pkg::*;

  // INTERNAL SIGNALS
  // ----------------
  // Selected slave index
  logic [IdxWidth-1:0] slave_sel;

  // ----------
  // COMPONENTS
  // ----------
  // Address decoder
  addr_decode #(
      .NoIndices(NSLAVE),
      .NoRules  (NSLAVE),
      .addr_t   (logic [31:0]),
      .rule_t   (addr_map_rule_pkg::addr_map_rule_t)
  ) u_addr_decode (
      .addr_i          (master_req_i.addr),
      .addr_map_i      (addr_map_i),
      .idx_o           (slave_sel),
      .dec_valid_o     (),                   // unused
      .dec_error_o     (),                   // unused
      .en_default_idx_i(1'b0),
      .default_idx_i   ('0)
  );

  // Slave demultiplexer
  reg_demux #(
      .NoPorts(NSLAVE),
      .req_t  (reg_req_t),
      .rsp_t  (reg_rsp_t)
  ) u_reg_demux (
      .clk_i      (clk_i),
      .rst_ni     (rst_ni),
      .in_select_i(slave_sel),
      .in_req_i   (master_req_i),
      .in_rsp_o   (master_rsp_o),
      .out_req_o  (slave_req_o),
      .out_rsp_i  (slave_rsp_i)
  );
endmodule
