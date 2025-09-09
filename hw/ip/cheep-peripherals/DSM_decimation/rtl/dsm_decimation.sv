// Copyright 2025 EPFL contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: dsm_decimation.sv
// Author: David Mallasen & Jérémie Moullet
// Date: 06.2025
//
// Description: DSM decimation block for HEEPidermis platform.
//              Dynamically routes a 1-bit DSM input to either a CIC or SES filter.
//              The active path is selected at runtime via register interface.
//
// Ports:
//   - clk_i, rst_ni           : System clock and active-low reset.
//   - cic_req_i, cic_rsp_o    : Register bus interface for CIC path.
//   - ses_filter_req_i, ses_filter_rsp_o : Register bus interface for SES path.
//   - dsm_in_i                : 1-bit delta-sigma modulated input.
//   - dsm_clk_o               : Clock forwarded to DSM source (from active filter).
//   - refresh_notif_o         : Pulse when new filtered PCM data is ready.
//
// Notes:
//   - Only one filter is active at a time.
//   - MUX_control = 1 : selects SES.
//                 = 0 : select CIC.
//   - If both filters are activated or deactivated, SES takes precedence.
//   - Produces a valid signal (refresh_notif_o) when either filter outputs valid data.

module dsm_decimation #(
) (
    input logic clk_i,
    input logic rst_ni,

    // CIC
    input  reg_pkg::reg_req_t cic_req_i,
    output reg_pkg::reg_rsp_t cic_rsp_o,

    // SES
    input  reg_pkg::reg_req_t ses_filter_req_i,
    output reg_pkg::reg_rsp_t ses_filter_rsp_o,

    // Data input from outside
    input  logic dsm_in_i,
    output logic dsm_clk_o,

    output logic refresh_notif_o
);

  //Filters
  logic cic_dsm_in_i;
  logic cic_dsm_clk_o;

  logic ses_dsm_in_i;
  logic ses_dsm_clk_o;

  logic MUX_control;

  logic SES_dataValid;
  logic CIC_dataValid;

  logic SES_activated;
  logic CIC_activated;

  always_comb begin
    if (MUX_control) begin
      ses_dsm_in_i = dsm_in_i;
      cic_dsm_in_i = '0;

      dsm_clk_o    = ses_dsm_clk_o;
    end else begin
      ses_dsm_in_i = '0;
      cic_dsm_in_i = dsm_in_i;

      dsm_clk_o    = cic_dsm_clk_o;
    end
  end

  pdm2pcm #(
      .FIFO_WIDTH(24),
      .reg_req_t (reg_pkg::reg_req_t),
      .reg_rsp_t (reg_pkg::reg_rsp_t)
  ) u_pdm2pcm_cic (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .reg_req_i(cic_req_i),
      .reg_rsp_o(cic_rsp_o),
      .pdm_i(cic_dsm_in_i),
      .pdm_clk_o(cic_dsm_clk_o),
      .CIC_activated,
      .CIC_dataValid
  );

  ses_filter #(
      .MAXIMUM_WIDTH(24),
      .reg_req_t(reg_pkg::reg_req_t),
      .reg_rsp_t(reg_pkg::reg_rsp_t)
  ) u_ses_filter (
      .clk_sys_i(clk_i),
      .rst_ni   (rst_ni),
      .dsm_i    (ses_dsm_in_i),
      .clk_fs_o (ses_dsm_clk_o),
      .req_i    (ses_filter_req_i),
      .rsp_o    (ses_filter_rsp_o),
      .SES_activated,
      .SES_dataValid
  );

  // Counter/refresh control 
  assign MUX_control = (SES_activated) ? '1 : ~CIC_activated;
  assign refresh_notif_o = (SES_activated) ? SES_dataValid : (CIC_activated) ? CIC_dataValid : '0;

endmodule  // dsm_decimation
