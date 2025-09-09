// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: cheep_peripherals.sv
// Author: Michele Caon, Luigi Giuffrida, Juan Sapriza
// Date: 20/02/2025
// Description: cheep peripheral subsystem

module cheep_peripherals (
    input logic ref_clk_i,
    input logic rst_ni,

    // System clock
    output logic system_clk_o,

    // iDAC ctrl signals
    input  reg_pkg::reg_req_t idac_ctrl_req_i,
    output reg_pkg::reg_rsp_t idac_ctrl_rsp_o,

    // Interface with iDAC 1
    output logic idac1_enable_o,
    output reg [idac_pkg::IdacCurrentWidth-1:0] idac1_current_o,
    output reg [idac_pkg::IdacCalibrationWidth-1:0] idac1_calibration_o,

    // Interface with iDAC 2
    output logic idac2_enable_o,
    output reg [idac_pkg::IdacCurrentWidth-1:0] idac2_current_o,
    output reg [idac_pkg::IdacCalibrationWidth-1:0] idac2_calibration_o,


    // Notifications (shared between both iDACs)
    output logic idac_refresh_o,
    output logic idac_refresh_notif_o,

    // VCO decoder signals
    input reg_pkg::reg_req_t vco_decoder_req_i,
    output reg_pkg::reg_rsp_t vco_decoder_rsp_o,
    output logic vcop_enable_o,
    output logic vcon_enable_o,

    // Interface with VCO
    input logic [vco_pkg::VcoCoarseWidth-1:0] vcop_coarse_i,
    input logic [vco_pkg::VcoCoarseWidth-1:0] vcon_coarse_i,
    input logic [vco_pkg::VcoFineWidth-1:0] vcop_fine_i,
    input logic [vco_pkg::VcoFineWidth-1:0] vcon_fine_i,
    output logic vco_counter_overflow_o,
    output logic vco_refresh_o,

    // Notification for DMA
    output logic vco_refresh_notif_o,

    // REFs control signals
    input  reg_pkg::reg_req_t refs_ctrl_req_i,
    output reg_pkg::reg_rsp_t refs_ctrl_rsp_o,

    // aMUX control signals
    input  reg_pkg::reg_req_t amux_ctrl_req_i,
    output reg_pkg::reg_rsp_t amux_ctrl_rsp_o,

    // Interaction with the aMUX
    output logic [amux_pkg::AmuxSelWidth-1:0] amux_sel_o,  // Select the mux's output

    // iREF signals
    output logic [iref_pkg::IrefCalibrationWidth-1:0] iref1_calibration_o,
    output logic [iref_pkg::IrefCalibrationWidth-1:0] iref2_calibration_o,

    // vREF signals
    output logic [vref_pkg::VrefCalibrationWidth-1:0] vref_calibration_o,

    // dLC signals
    output logic dlc_done_o,
    input reg_pkg::reg_req_t dlc_req_i,
    output reg_pkg::reg_rsp_t dlc_resp_o,
    input fifo_pkg::fifo_req_t hw_fifo_req_i,
    output fifo_pkg::fifo_resp_t hw_fifo_resp_o,
    output logic dlc_xing_o,
    output logic dlc_dir_o,

    // DSM decimation signals
    output logic dsm_decimation_refresh_notif_o,
    input reg_pkg::reg_req_t cic_req_i,
    output reg_pkg::reg_rsp_t cic_rsp_o,
    input reg_pkg::reg_req_t ses_filter_req_i,
    output reg_pkg::reg_rsp_t ses_filter_rsp_o,
    input logic dsm_in_i,
    output logic dsm_clk_o,

    // Interrupts
    output [core_v_mini_mcu_pkg::NEXT_INT-1:0] ext_int_vector_o
);
  import cheep_pkg::*;

  // INTERNAL SIGNALS
  // ----------------
  // System clock
  logic system_clk;

  // --------------
  // OUTPUT CONTROL
  // --------------
  assign system_clk_o                                        = system_clk;
  assign ext_int_vector_o[core_v_mini_mcu_pkg::NEXT_INT-1:0] = '0;

  assign system_clk                                          = ref_clk_i;

  idac_ctrl u_idac_ctrl (
      .clk_i          (system_clk),
      .rst_ni         (rst_ni),
      .req_i          (idac_ctrl_req_i),
      .rsp_o          (idac_ctrl_rsp_o),
      .enable_1_o     (idac1_enable_o),
      .current_1_o    (idac1_current_o),
      .calibration_1_o(idac1_calibration_o),
      .enable_2_o     (idac2_enable_o),
      .current_2_o    (idac2_current_o),
      .calibration_2_o(idac2_calibration_o),
      .refresh_o      (idac_refresh_o),
      .refresh_notif_o(idac_refresh_notif_o)
  );

  vco_decoder u_vco_decoder (
      .clk_i             (system_clk),
      .rst_ni            (rst_ni),
      .req_i             (vco_decoder_req_i),
      .rsp_o             (vco_decoder_rsp_o),
      .p_enable_o        (vcop_enable_o),
      .n_enable_o        (vcon_enable_o),
      .p_coarse_i        (vcop_coarse_i),
      .n_coarse_i        (vcon_coarse_i),
      .p_fine_i          (vcop_fine_i),
      .n_fine_i          (vcon_fine_i),
      .counter_overflow_o(vco_counter_overflow_o),
      .refresh_o         (vco_refresh_o),
      .refresh_notif_o   (vco_refresh_notif_o)
  );

  amux_ctrl u_amux_ctrl (
      .clk_i (system_clk),
      .rst_ni(rst_ni),
      .req_i (amux_ctrl_req_i),
      .rsp_o (amux_ctrl_rsp_o),
      .sel_o (amux_sel_o)
  );


  refs_ctrl u_refs_ctrl (
      .clk_i              (system_clk),
      .rst_ni             (rst_ni),
      .req_i              (refs_ctrl_req_i),
      .rsp_o              (refs_ctrl_rsp_o),
      .iref1_calibration_o(iref1_calibration_o),
      .iref2_calibration_o(iref2_calibration_o),
      .vref_calibration_o (vref_calibration_o)
  );

  dlc dlc_i (
      .clk_i(system_clk),
      .rst_ni(rst_ni),
      .dlc_done_o,
      .reg_req_i(dlc_req_i),
      .reg_rsp_o(dlc_resp_o),
      .hw_fifo_req_i,
      .hw_fifo_resp_o,
      .dlc_xing_o,
      .dlc_dir_o
  );

  dsm_decimation u_dsm_decimation (
      .clk_i           (system_clk),
      .rst_ni          (rst_ni),
      .refresh_notif_o (dsm_decimation_refresh_notif_o),
      .cic_req_i       (cic_req_i),
      .cic_rsp_o       (cic_rsp_o),
      .ses_filter_req_i(ses_filter_req_i),
      .ses_filter_rsp_o(ses_filter_rsp_o),
      .dsm_in_i        (dsm_in_i),
      .dsm_clk_o       (dsm_clk_o)
  );

endmodule
