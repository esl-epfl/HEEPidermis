// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: cheep-top.sv
// Author: Michele Caon, Luigi Giuffrida
// Date: 29/04/2024
// Description: cheep top-level module

module cheep_top (
    // X-HEEP interface
% for pad in total_pad_list:
${pad.x_heep_system_interface}
% endfor

);
  import obi_pkg::*;
  import reg_pkg::*;
  import cheep_pkg::*;
  import core_v_mini_mcu_pkg::*;

  // PARAMETERS
  localparam int unsigned ExtXbarNmasterRnd = (cheep_pkg::ExtXbarNMaster > 0) ?
    cheep_pkg::ExtXbarNMaster : 32'd1;
  localparam int unsigned ExtDomainsRnd = core_v_mini_mcu_pkg::EXTERNAL_DOMAINS == 0 ?
    32'd1 : core_v_mini_mcu_pkg::EXTERNAL_DOMAINS;

  // INTERNAL SIGNALS
  // ----------------
  // Synchronized reset
  logic rst_nin_sync;

  // System clock
  logic system_clk;

  // Exit value
  logic [31:0] exit_value;

  // X-HEEP external master ports
  obi_req_t  heep_core_instr_req;
  obi_resp_t heep_core_instr_rsp;
  obi_req_t  heep_core_data_req;
  obi_resp_t heep_core_data_rsp;
  obi_req_t  heep_debug_master_req;
  obi_resp_t heep_debug_master_rsp;
  obi_req_t  [DMA_NUM_MASTER_PORTS-1:0] heep_dma_read_req;
  obi_resp_t [DMA_NUM_MASTER_PORTS-1:0] heep_dma_read_rsp;
  obi_req_t  [DMA_NUM_MASTER_PORTS-1:0] heep_dma_write_req;
  obi_resp_t [DMA_NUM_MASTER_PORTS-1:0] heep_dma_write_rsp;
  obi_req_t  [DMA_NUM_MASTER_PORTS-1:0] heep_dma_addr_req;
  obi_resp_t [DMA_NUM_MASTER_PORTS-1:0] heep_dma_addr_rsp;

  // X-HEEP slave ports
  obi_req_t  [ExtXbarNmasterRnd-1:0] heep_slave_req;
  obi_resp_t [ExtXbarNmasterRnd-1:0] heep_slave_rsp;

  // External master ports
  obi_req_t  [ExtXbarNmasterRnd-1:0] cheep_master_req;
  obi_resp_t [ExtXbarNmasterRnd-1:0] cheep_master_resp;

  // X-HEEP external peripheral master ports
  reg_req_t heep_peripheral_req;
  reg_rsp_t heep_peripheral_rsp;

  // PLIC interrupt vector
  logic [core_v_mini_mcu_pkg::NEXT_INT-1:0] ext_int_vector;

  // Power Manager signals
  logic cpu_subsystem_powergate_switch_n;
  logic cpu_subsystem_powergate_switch_ack_n;
  logic peripheral_subsystem_powergate_switch_n;
  logic peripheral_subsystem_powergate_switch_ack_n;

  // External SPC interface signals
  logic [DMACHNum-1:0] dma_busy;

  // Pad controller
  reg_req_t pad_req;
  reg_rsp_t pad_rsp;
% if pads_attributes != None:
  logic [core_v_mini_mcu_pkg::NUM_PAD-1:0][${pads_attributes['bits']}] pad_attributes;
% endif
% if total_pad_muxed > 0:
  logic [core_v_mini_mcu_pkg::NUM_PAD-1:0][${max_total_pad_mux_bitlengh-1}:0] pad_muxes;
% endif

  // External power domains
  logic [ExtDomainsRnd-1:0] external_subsystem_powergate_switch_n;
  logic [ExtDomainsRnd-1:0] external_subsystem_powergate_switch_ack_n;
  logic [ExtDomainsRnd-1:0] external_subsystem_powergate_iso_n;

  // External RAM banks retentive mode control
  logic [ExtDomainsRnd-1:0] external_ram_banks_set_retentive_n;

  // External domains reset
  logic [ExtDomainsRnd-1:0] external_subsystem_rst_n;

  // External domains clock-gating
  logic [ExtDomainsRnd-1:0] external_subsystem_clkgate_en_n;

  // iDAC controller signals
  logic idac1_enable;
  logic [idac_pkg::IdacCurrentWidth-1:0] idac1_current;
  logic [idac_pkg::IdacCalibrationWidth-1:0] idac1_calibration;
  logic idac2_enable;
  logic [idac_pkg::IdacCurrentWidth-1:0] idac2_current;
  logic [idac_pkg::IdacCalibrationWidth-1:0] idac2_calibration;
  logic idac_refresh;
  logic idac_refresh_notif;
  reg_req_t idac_ctrl_req;
  reg_rsp_t idac_ctrl_rsp;

  // VCO decoder signals
  logic vcop_enable;
  logic vcon_enable;
  logic [vco_pkg::VcoCoarseWidth-1:0] vcop_coarse;
  logic [vco_pkg::VcoCoarseWidth-1:0] vcon_coarse;
  logic [vco_pkg::VcoFineWidth-1:0] vcop_fine;
  logic [vco_pkg::VcoFineWidth-1:0] vcon_fine;
  logic vco_refresh;
  logic vco_refresh_notif;
  reg_req_t vco_decoder_req;
  reg_rsp_t vco_decoder_rsp;


  // Aalog mux control signals
  reg_req_t amux_ctrl_req;
  reg_rsp_t amux_ctrl_rsp;
  logic [amux_pkg::AmuxSelWidth-1:0] amux_sel;

  // References control signals
  reg_req_t refs_ctrl_req;
  reg_rsp_t refs_ctrl_rsp;
  logic [iref_pkg::IrefCalibrationWidth-1:0] iref1_calibration;
  logic [iref_pkg::IrefCalibrationWidth-1:0] iref2_calibration;
  logic [vref_pkg::VrefCalibrationWidth-1:0] vref_calibration;

  // DSM decimation control signals
  logic dsm_decimation_refresh_notif;

  // CIC signals
  reg_req_t cic_req;
  reg_rsp_t cic_rsp;

  // SES filter signals
  reg_req_t ses_filter_req;
  reg_rsp_t ses_filter_rsp;

  // DMA control signals
  logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] ext_dma_slot_tx;
  logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] ext_dma_slot_rx;

  logic ext_debug_req;
  logic ext_debug_reset_n;

  // dLC done signal
  reg_req_t dlc_req;
  reg_rsp_t dlc_resp;

/* verilator lint_off UNUSED */
/* verilator lint_off UNDRIVEN */

  logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_done;
  fifo_pkg::fifo_req_t  [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_req;
  fifo_pkg::fifo_resp_t [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_resp;

  assign hw_fifo_resp [core_v_mini_mcu_pkg::DMA_CH_NUM-1:1] = '0;

/* verilator lint_on UNUSED */
/* verilator lint_on UNDRIVEN */

  // Tie the CV-X-IF coprocessor signals to a default value that will
  // receive petitions but reject all offloaded instructions
  // CV-X-IF is unused in core-v-mini-mcu as it has the cv32e40p CPU
  if_xif #() ext_if ();

  assign ext_if.compressed_ready = 1'b1;
  assign ext_if.compressed_resp  = '0;

  assign ext_if.issue_ready      = 1'b1;
  assign ext_if.issue_resp       = '0;

  assign ext_if.mem_valid        = 1'b0;
  assign ext_if.mem_req          = '0;

  assign ext_if.result_valid     = 1'b0;
  assign ext_if.result           = '0;

  // CORE-V-MINI-MCU input/output pins
% for pad in total_pad_list:
${pad.internal_signals}
% endfor

  // Drive to zero bypassed pins
% for pad in total_pad_list:
% if pad.pad_type == 'bypass_inout' or pad.pad_type == 'bypass_input':
% for i in range(len(pad.pad_type_drive)):
% if pad.driven_manually[i] == False:
  assign ${pad.in_internal_signals[i]} = 1'b0;
% endif
% endfor
% endif
% endfor

  // --------------
  // SYSTEM MODULES
  // --------------

  // Reset generator
  // ---------------
  rstgen u_rstgen (
    .clk_i      (clk_in_x),
    .rst_ni     (rst_nin_x),
    .test_mode_i(1'b0 ), // not implemented
    .rst_no     (rst_nin_sync),
    .init_no    () // unused
  );

  // CORE-V-MINI-MCU (microcontroller)
  // ---------------------------------
  core_v_mini_mcu #(
    .COREV_PULP      (CpuCorevPulp),
    .FPU             (CpuFpu),
    .ZFINX           (CpuRiscvZfinx),
    .EXT_XBAR_NMASTER(ExtXbarNMaster),
    .X_EXT           (CpuCorevXif),
    .EXT_HARTS       (1)
  ) u_core_v_mini_mcu (
    .rst_ni (rst_nin_sync),
    .clk_i  (system_clk),

    // MCU pads
% for pad in pad_list:
${pad.core_v_mini_mcu_bonding}
% endfor

`ifdef FPGA
    .spi_flash_cs_1_o (),
    .spi_flash_cs_1_i ('0),
    .spi_flash_cs_1_oe_o(),
`endif

    // CORE-V eXtension Interface
    .xif_compressed_if (ext_if.cpu_compressed),
    .xif_issue_if      (ext_if.cpu_issue),
    .xif_commit_if     (ext_if.cpu_commit),
    .xif_mem_if        (ext_if.cpu_mem),
    .xif_mem_result_if (ext_if.cpu_mem_result),
    .xif_result_if     (ext_if.cpu_result),

    // Pad controller interface
    .pad_req_o  (pad_req),
    .pad_resp_i (pad_rsp),

    // External slave ports
    .ext_xbar_master_req_i (heep_slave_req),
    .ext_xbar_master_resp_o (heep_slave_rsp),

    // External master ports
    .ext_core_instr_req_o (heep_core_instr_req),
    .ext_core_instr_resp_i (heep_core_instr_rsp),
    .ext_core_data_req_o (heep_core_data_req),
    .ext_core_data_resp_i (heep_core_data_rsp),
    .ext_debug_master_req_o (heep_debug_master_req),
    .ext_debug_master_resp_i (heep_debug_master_rsp),
    .ext_dma_read_req_o (heep_dma_read_req),
    .ext_dma_read_resp_i (heep_dma_read_rsp),
    .ext_dma_write_req_o (heep_dma_write_req),
    .ext_dma_write_resp_i (heep_dma_write_rsp),
    .ext_dma_addr_req_o (heep_dma_addr_req),
    .ext_dma_addr_resp_i (heep_dma_addr_rsp),

    // External peripherals slave ports
    .ext_peripheral_slave_req_o  (heep_peripheral_req),
    .ext_peripheral_slave_resp_i (heep_peripheral_rsp),

    // SPC signals
    .ext_ao_peripheral_slave_req_i('0),
    .ext_ao_peripheral_slave_resp_o(),

    // Power switches connected by the backend
    .cpu_subsystem_powergate_switch_no            (cpu_subsystem_powergate_switch_n),
    .cpu_subsystem_powergate_switch_ack_ni        (cpu_subsystem_powergate_switch_ack_n),
    .peripheral_subsystem_powergate_switch_no     (peripheral_subsystem_powergate_switch_n),
    .peripheral_subsystem_powergate_switch_ack_ni (peripheral_subsystem_powergate_switch_ack_n),

    .external_subsystem_powergate_switch_no(external_subsystem_powergate_switch_n),
    .external_subsystem_powergate_switch_ack_ni(external_subsystem_powergate_switch_ack_n),
    .external_subsystem_powergate_iso_no(external_subsystem_powergate_iso_n),

    // Control signals for external peripherals
    .external_subsystem_rst_no (external_subsystem_rst_n),
    .external_ram_banks_set_retentive_no (external_ram_banks_set_retentive_n),
    .external_subsystem_clkgate_en_no (external_subsystem_clkgate_en_n),

    // External interrupts
    .intr_vector_ext_i (ext_int_vector),
    .intr_ext_peripheral_i(lc_xing_out_x),

    .ext_dma_slot_tx_i(ext_dma_slot_tx),
    .ext_dma_slot_rx_i(ext_dma_slot_rx),

    .ext_debug_req_o(ext_debug_req),
    .ext_debug_reset_no(ext_debug_reset_n),
    .ext_cpu_subsystem_rst_no(),

    .ext_dma_stop_i('0),
    .dma_done_o(dma_busy),

    .hw_fifo_done_i(hw_fifo_done),
    .hw_fifo_req_o(hw_fifo_req),
    .hw_fifo_resp_i(hw_fifo_resp),

    .exit_value_o (exit_value)
  );

  assign cpu_subsystem_powergate_switch_ack_n = cpu_subsystem_powergate_switch_n;
  assign peripheral_subsystem_powergate_switch_ack_n = peripheral_subsystem_powergate_switch_n;

`ifdef SYNTHESIS
(* dont_touch = "true" *) wire idac1_iin_i;
(* dont_touch = "true" *) wire idac1_iout_o;
(* dont_touch = "true" *) wire idac2_iin_i;
(* dont_touch = "true" *) wire idac2_iout_o;
(* dont_touch = "true" *) wire vcop_vin_i;
(* dont_touch = "true" *) wire vcon_vin_i;
(* dont_touch = "true" *) wire vcop_vn0_o;
(* dont_touch = "true" *) wire vcon_vn0_o;
(* dont_touch = "true" *) wire iref1_iout_o;
(* dont_touch = "true" *) wire iref2_iout_o;
(* dont_touch = "true" *) wire vref_vout_o;
(* dont_touch = "true" *) wire ldo_vin_i;
(* dont_touch = "true" *) wire ldo_vout_o;
(* dont_touch = "true" *) wire amux_vout_o;
(* dont_touch = "true" *) wire amux_vdd_i;
(* dont_touch = "true" *) wire ldo_vbat_i;
(* dont_touch = "true" *) wire iref_vdd_i;
(* dont_touch = "true" *) wire vref_vdd_i;
(* dont_touch = "true" *) wire vco_vn0_vdd_i;
(* dont_touch = "true" *) wire vco_vdd_i;
`endif

  // Analog subsystem
  // ------------------------
  analog_subsystem u_analog_subsystem (
    .idac1_enable_i       (idac1_enable),
    .idac1_calibration_i  (idac1_calibration),
    .idac1_current_i      (idac1_current),
    .idac2_enable_i       (idac2_enable),
    .idac2_calibration_i  (idac2_calibration),
    .idac2_current_i      (idac2_current),
    .idac_refresh_i       (idac_refresh),
    .vcop_enable_i        (vcop_enable),
    .vcon_enable_i        (vcon_enable),
    .vcop_coarse_o        (vcop_coarse),
    .vcon_coarse_o        (vcon_coarse),
    .vcop_fine_o          (vcop_fine),
    .vcon_fine_o          (vcon_fine),
    .vco_refresh_i        (vco_refresh),
    .iref1_calibration_i  (iref1_calibration),
    .iref2_calibration_i  (iref2_calibration),
    .vref_calibration_i   (vref_calibration),
    .amux_sel_i           (amux_sel)

  );



  // External peripherals
  // --------------------
  cheep_peripherals u_cheep_peripherals(
    .ref_clk_i              (clk_in_x),
    .rst_ni                 (rst_nin_sync),
    .system_clk_o           (system_clk),
    .idac_ctrl_req_i        (idac_ctrl_req),
    .idac_ctrl_rsp_o        (idac_ctrl_rsp),
    .idac1_enable_o         (idac1_enable),
    .idac1_current_o        (idac1_current),
    .idac1_calibration_o    (idac1_calibration),
    .idac2_enable_o         (idac2_enable),
    .idac2_current_o        (idac2_current),
    .idac2_calibration_o    (idac2_calibration),
    .idac_refresh_o         (idac_refresh),
    .idac_refresh_notif_o   (idac_refresh_notif),
    .vco_decoder_req_i      (vco_decoder_req),
    .vco_decoder_rsp_o      (vco_decoder_rsp),
    .vcop_enable_o         (vcop_enable),
    .vcon_enable_o         (vcon_enable),
    .vcop_coarse_i         (vcop_coarse),
    .vcon_coarse_i         (vcon_coarse),
    .vcop_fine_i           (vcop_fine),
    .vcon_fine_i           (vcon_fine),
    .vco_counter_overflow_o (vco_counter_overflow_out_x),
    .vco_refresh_o          (vco_refresh),
    .vco_refresh_notif_o    (vco_refresh_notif),
    .amux_ctrl_req_i        (amux_ctrl_req),
    .amux_ctrl_rsp_o        (amux_ctrl_rsp),
    .amux_sel_o             (amux_sel),
    .refs_ctrl_req_i        (refs_ctrl_req),
    .refs_ctrl_rsp_o        (refs_ctrl_rsp),
    .iref1_calibration_o    (iref1_calibration),
    .iref2_calibration_o    (iref2_calibration),
    .vref_calibration_o     (vref_calibration),
    .dlc_xing_o             (lc_xing_out_x),
    .dlc_dir_o              (lc_dir_out_x),
    .dlc_req_i              (dlc_req),
    .dlc_resp_o             (dlc_resp),
    .dlc_done_o             (hw_fifo_done[0]),
    .hw_fifo_req_i          (hw_fifo_req[0]),
    .hw_fifo_resp_o         (hw_fifo_resp[0]),
    .dsm_decimation_refresh_notif_o (dsm_decimation_refresh_notif),

    .cic_req_i            (cic_req),
    .cic_rsp_o            (cic_rsp),

    .ses_filter_req_i     (ses_filter_req),
    .ses_filter_rsp_o     (ses_filter_rsp),

    .dsm_in_i             (dsm_in_in_x),
    .dsm_clk_o            (dsm_clk_out_x),
    .ext_int_vector_o     (ext_int_vector)
  );

  // DMA ADC ext slots
  assign ext_dma_slot_rx[0] = vco_refresh_notif || dsm_decimation_refresh_notif;
  assign ext_dma_slot_tx[0] = '0;

  // DMA DAC ext slots
  assign ext_dma_slot_rx[1] = '0;
  assign ext_dma_slot_tx[1] = idac_refresh_notif;

  // External peripherals bus
  // ------------------------
  cheep_bus u_cheep_bus (
    .clk_i                        (system_clk),
    .rst_ni                       (rst_nin_sync),

    .heep_core_instr_req_i        (heep_core_instr_req),
    .heep_core_instr_resp_o       (heep_core_instr_rsp),

    .heep_core_data_req_i         (heep_core_data_req),
    .heep_core_data_resp_o        (heep_core_data_rsp),

    .heep_debug_master_req_i      (heep_debug_master_req),
    .heep_debug_master_resp_o     (heep_debug_master_rsp),

    .heep_dma_read_req_i          (heep_dma_read_req),
    .heep_dma_read_resp_o         (heep_dma_read_rsp),
    .heep_dma_write_req_i         (heep_dma_write_req),
    .heep_dma_write_resp_o        (heep_dma_write_rsp),
    .heep_dma_addr_req_i          (heep_dma_addr_req),
    .heep_dma_addr_resp_o         (heep_dma_addr_rsp),

    .cheep_master_req_i           (cheep_master_req),
    .cheep_master_resp_o          (cheep_master_resp),

    .heep_slave_req_o             (heep_slave_req),
    .heep_slave_resp_i            (heep_slave_rsp),

    .heep_periph_req_i            (heep_peripheral_req),
    .heep_periph_resp_o           (heep_peripheral_rsp),

    .idac_ctrl_req_o              (idac_ctrl_req),
    .idac_ctrl_resp_i             (idac_ctrl_rsp),
    .vco_decoder_req_o            (vco_decoder_req),
    .vco_decoder_resp_i           (vco_decoder_rsp),
    .amux_ctrl_req_o              (amux_ctrl_req),
    .amux_ctrl_resp_i             (amux_ctrl_rsp),
    .refs_ctrl_req_o              (refs_ctrl_req),
    .refs_ctrl_resp_i             (refs_ctrl_rsp),
    .dlc_req_o                    (dlc_req),
    .dlc_resp_i                   (dlc_resp),
    .cic_req_o                    (cic_req),
    .cic_resp_i                   (cic_rsp),
    .ses_filter_req_o             (ses_filter_req),
    .ses_filter_resp_i            (ses_filter_rsp)
  );


  // Pad ring
  // --------
  assign exit_value_out_x = exit_value[0];
  pad_ring u_pad_ring (
% for pad in total_pad_list:
${pad.pad_ring_bonding_bonding}
% endfor

   // Pad attributes
% if pads_attributes != None:
    .pad_attributes_i(pad_attributes)
% else:
    .pad_attributes_i('0)
% endif

  );

  // Constant pad signals
${pad_constant_driver_assign}

  // Shared pads multiplexing
${pad_mux_process}

  // Pad control
  // -----------
  pad_control #(
    .reg_req_t (reg_req_t),
    .reg_rsp_t (reg_rsp_t),
    .NUM_PAD   (NUM_PAD)
  ) u_pad_control (
    .clk_i            (system_clk),
    .rst_ni           (rst_nin_sync),
    .reg_req_i        (pad_req),
    .reg_rsp_o        (pad_rsp)
% if total_pad_muxed > 0 or pads_attributes != None:
      ,
% endif
% if pads_attributes != None:
      .pad_attributes_o(pad_attributes)
% if total_pad_muxed > 0:
      ,
% endif
% endif
% if total_pad_muxed > 0:
      .pad_muxes_o(pad_muxes)
% endif
  );


endmodule // cheep_top
