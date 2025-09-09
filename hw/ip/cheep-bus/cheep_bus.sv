// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: cheep_bus.sv
// Author: Michele Caon, Luigi Giuffrida
// Date: 29/04/2024
// Description: External bus for cheep

module cheep_bus #(
    // Dependent parameters: do not override!
    localparam int unsigned ExtXbarNmasterRnd = (cheep_pkg::ExtXbarNMaster > 0) ? cheep_pkg::ExtXbarNMaster : 32'd1
) (
    input logic clk_i,
    input logic rst_ni,

    // X-HEEP master ports
    input  obi_pkg::obi_req_t  heep_core_instr_req_i,
    output obi_pkg::obi_resp_t heep_core_instr_resp_o,

    input  obi_pkg::obi_req_t  heep_core_data_req_i,
    output obi_pkg::obi_resp_t heep_core_data_resp_o,

    input  obi_pkg::obi_req_t  heep_debug_master_req_i,
    output obi_pkg::obi_resp_t heep_debug_master_resp_o,

    input  obi_pkg::obi_req_t  [cheep_pkg::DMAMasterPortsNum-1:0] heep_dma_read_req_i,
    output obi_pkg::obi_resp_t [cheep_pkg::DMAMasterPortsNum-1:0] heep_dma_read_resp_o,

    input  obi_pkg::obi_req_t  [cheep_pkg::DMAMasterPortsNum-1:0] heep_dma_write_req_i,
    output obi_pkg::obi_resp_t [cheep_pkg::DMAMasterPortsNum-1:0] heep_dma_write_resp_o,

    input  obi_pkg::obi_req_t  [cheep_pkg::DMAMasterPortsNum-1:0] heep_dma_addr_req_i,
    output obi_pkg::obi_resp_t [cheep_pkg::DMAMasterPortsNum-1:0] heep_dma_addr_resp_o,

    // External master ports
    input  obi_pkg::obi_req_t  [ExtXbarNmasterRnd-1:0] cheep_master_req_i,
    output obi_pkg::obi_resp_t [ExtXbarNmasterRnd-1:0] cheep_master_resp_o,

    // X-HEEP slave ports (one per external master)
    output obi_pkg::obi_req_t  [ExtXbarNmasterRnd-1:0] heep_slave_req_o,
    input  obi_pkg::obi_resp_t [ExtXbarNmasterRnd-1:0] heep_slave_resp_i,

    // X-HEEP peripheral master port
    input  reg_pkg::reg_req_t heep_periph_req_i,
    output reg_pkg::reg_rsp_t heep_periph_resp_o,

    // External peripherals slave ports
    output reg_pkg::reg_req_t idac_ctrl_req_o,
    input  reg_pkg::reg_rsp_t idac_ctrl_resp_i,

    output reg_pkg::reg_req_t vco_decoder_req_o,
    input  reg_pkg::reg_rsp_t vco_decoder_resp_i,

    output reg_pkg::reg_req_t ses_filter_req_o,
    input  reg_pkg::reg_rsp_t ses_filter_resp_i,

    output reg_pkg::reg_req_t amux_ctrl_req_o,
    input  reg_pkg::reg_rsp_t amux_ctrl_resp_i,

    output reg_pkg::reg_req_t refs_ctrl_req_o,
    input  reg_pkg::reg_rsp_t refs_ctrl_resp_i,

    output reg_pkg::reg_req_t dlc_req_o,
    input  reg_pkg::reg_rsp_t dlc_resp_i,

    output reg_pkg::reg_req_t cic_req_o,
    input  reg_pkg::reg_rsp_t cic_resp_i
);
  import cheep_pkg::*;
  import obi_pkg::*;
  import reg_pkg::*;

  // PARAMETERS
  localparam int unsigned IdxWidth = cf_math_pkg::idx_width(ExtXbarNSlave);

  // INTERNAL SIGNALS
  // ----------------
  // External slaves request
  obi_req_t  [  ExtXbarNSlave-1:0] ext_slave_req;
  obi_resp_t [  ExtXbarNSlave-1:0] ext_slave_rsp;

  // External peripherals request
  reg_req_t  [ExtPeriphNSlave-1:0] ext_periph_req;
  reg_rsp_t  [ExtPeriphNSlave-1:0] ext_periph_rsp;


  // External slave bus
  // ------------------
  // External slave mapping

  if (ExtXbarNMaster != 32'd0 || ExtXbarNSlave != 32'd0) begin : gen_ext_bus

    assign ext_slave_rsp = '0;

    // External bus
    ext_bus #(
        .EXT_XBAR_NMASTER(ExtXbarNMaster),
        .EXT_XBAR_NSLAVE(ExtXbarNSlave),
        .DMA_MASTER_PORTS_NUM(DMAMasterPortsNum)
    ) u_ext_bus (
        .clk_i                   (clk_i),
        .rst_ni                  (rst_ni),
        .addr_map_i              (ExtSlaveAddrRules),
        .default_idx_i           (ExtSlaveDefaultIdx[IdxWidth-1:0]),
        .heep_core_instr_req_i   (heep_core_instr_req_i),
        .heep_core_instr_resp_o  (heep_core_instr_resp_o),
        .heep_core_data_req_i    (heep_core_data_req_i),
        .heep_core_data_resp_o   (heep_core_data_resp_o),
        .heep_debug_master_req_i (heep_debug_master_req_i),
        .heep_debug_master_resp_o(heep_debug_master_resp_o),
        .heep_dma_read_req_i     (heep_dma_read_req_i),
        .heep_dma_read_resp_o    (heep_dma_read_resp_o),
        .heep_dma_write_req_i    (heep_dma_write_req_i),
        .heep_dma_write_resp_o   (heep_dma_write_resp_o),
        .heep_dma_addr_req_i     (heep_dma_addr_req_i),
        .heep_dma_addr_resp_o    (heep_dma_addr_resp_o),
        .ext_master_req_i        (cheep_master_req_i),
        .ext_master_resp_o       (cheep_master_resp_o),
        .heep_slave_req_o        (heep_slave_req_o),
        .heep_slave_resp_i       (heep_slave_resp_i),
        .ext_slave_req_o         (ext_slave_req),
        .ext_slave_resp_i        (ext_slave_rsp)
    );

  end else begin : gen_no_ext_bus
    assign heep_core_instr_resp_o = '0;
    assign heep_core_data_resp_o = '0;
    assign heep_debug_master_resp_o = '0;
    assign heep_dma_read_resp_o = '0;
    assign heep_dma_write_resp_o = '0;
    assign heep_dma_addr_resp_o = '0;
    assign cheep_master_resp_o = '0;
    assign heep_slave_req_o = '0;
    assign heep_periph_resp_o = '0;
    assign ext_slave_req = '0;
  end

  // External peripherals bus
  // ------------------------
  // External peripherals mapping
  assign idac_ctrl_req_o                    = ext_periph_req[CheepiDACCtrlIdx];
  assign ext_periph_rsp[CheepiDACCtrlIdx]   = idac_ctrl_resp_i;

  assign vco_decoder_req_o                  = ext_periph_req[CheepVCODecoderIdx];
  assign ext_periph_rsp[CheepVCODecoderIdx] = vco_decoder_resp_i;

  assign ses_filter_req_o                   = ext_periph_req[CheepSESFilterIdx];
  assign ext_periph_rsp[CheepSESFilterIdx]  = ses_filter_resp_i;

  assign amux_ctrl_req_o                    = ext_periph_req[CheepaMUXCtrlIdx];
  assign ext_periph_rsp[CheepaMUXCtrlIdx]   = amux_ctrl_resp_i;

  assign refs_ctrl_req_o                    = ext_periph_req[CheepREFsCtrlIdx];
  assign ext_periph_rsp[CheepREFsCtrlIdx]   = refs_ctrl_resp_i;

  assign dlc_req_o                          = ext_periph_req[CheepdLCIdx];
  assign ext_periph_rsp[CheepdLCIdx]        = dlc_resp_i;

  assign cic_req_o                          = ext_periph_req[CheepCICIdx];
  assign ext_periph_rsp[CheepCICIdx]        = cic_resp_i;

  // External peripherals bus
  periph_bus #(
      .NSLAVE(ExtPeriphNSlave)
  ) u_periph_bus (
      .clk_i       (clk_i),
      .rst_ni      (rst_ni),
      .addr_map_i  (ExtPeriphAddrRules),
      .master_req_i(heep_periph_req_i),
      .master_rsp_o(heep_periph_resp_o),
      .slave_req_o (ext_periph_req),
      .slave_rsp_i (ext_periph_rsp)
  );

endmodule
