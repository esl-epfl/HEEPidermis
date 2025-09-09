// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: cheep_pkg.sv
// Author: Michele Caon, Luigi Giuffrida
// Date: 29/04/2024
// Description: Package containing memory map and other definitions.

package cheep_pkg;
  import addr_map_rule_pkg::*;
  import core_v_mini_mcu_pkg::*;

  // ---------------
  // CORE-V-MINI-MCU
  // ---------------

  // CPU
  localparam int unsigned CpuCorevPulp = 32'd${cpu_corev_pulp};
  localparam int unsigned CpuCorevXif = 32'd${cpu_corev_xif};
  localparam int unsigned CpuFpu = 32'd${cpu_fpu};
  localparam int unsigned CpuRiscvZfinx = 32'd${cpu_riscv_zfinx};

  // DMA
  localparam int unsigned DMAMasterPortsNum = DMA_NUM_MASTER_PORTS;
  localparam int unsigned DMACHNum = DMA_CH_NUM;

  // --------------------
  // CV-X-IF COPROCESSORS
  // --------------------


  // ----------------
  // EXTERNAL OBI BUS
  // ----------------

  // Number of masters and slaves
  localparam int unsigned ExtXbarNMaster = 32'd${xbar_nmasters};
  localparam int unsigned ExtXbarNSlave = 32'd${xbar_nslaves};
  localparam int unsigned LogExtXbarNMaster = ExtXbarNMaster > 32'd1 ? $clog2(ExtXbarNMaster) : 32'd1;
  localparam int unsigned LogExtXbarNSlave = ExtXbarNSlave > 32'd1 ? $clog2(ExtXbarNSlave) : 32'd1;

  // External slaves address map
  localparam addr_map_rule_t [ExtXbarNSlave-1:0] ExtSlaveAddrRules = '{default: '0};

  localparam int unsigned ExtSlaveDefaultIdx = 32'd0;


  // --------------------
  // EXTERNAL PERIPHERALS
  // --------------------

  // Number of external peripherals
  localparam int unsigned ExtPeriphNSlave = 32'd${periph_nslaves};
  localparam int unsigned LogExtPeriphNSlave = ExtPeriphNSlave > 32'd1 ? $clog2(ExtPeriphNSlave) : 32'd1;

  // Memory map
  // ----------

  // iDAC controller
  localparam int unsigned CheepiDACCtrlIdx = 32'd0;
  localparam logic [31:0] CheepiDACCtrlStartAddr = EXT_PERIPHERAL_START_ADDRESS + 32'h${iDAC_ctrl_start_address};
  localparam logic [31:0] CheepiDACCtrlEndAddr = CheepiDACCtrlStartAddr + 32'h${iDAC_ctrl_size};

  // VCO decoder
  localparam int unsigned CheepVCODecoderIdx = 32'd1;
  localparam logic [31:0] CheepVCODecoderStartAddr = EXT_PERIPHERAL_START_ADDRESS + 32'h${VCO_decoder_start_address};
  localparam logic [31:0] CheepVCODecoderEndAddr = CheepVCODecoderStartAddr + 32'h${VCO_decoder_size};

  // SES filter
  localparam int unsigned CheepSESFilterIdx = 32'd2;
  localparam logic [31:0] CheepSESFilterStartAddr = EXT_PERIPHERAL_START_ADDRESS + 32'h${SES_filter_start_address};
  localparam logic [31:0] CheepSESFilterEndAddr = CheepSESFilterStartAddr + 32'h${SES_filter_size};

  // REFs controller
  localparam int unsigned CheepREFsCtrlIdx = 32'd3;
  localparam logic [31:0] CheepREFsCtrlStartAddr = EXT_PERIPHERAL_START_ADDRESS + 32'h${REFs_ctrl_start_address};
  localparam logic [31:0] CheepREFsCtrlEndAddr = CheepREFsCtrlStartAddr + 32'h${REFs_ctrl_size};

  // aMUX controller
  localparam int unsigned CheepaMUXCtrlIdx = 32'd4;
  localparam logic [31:0] CheepaMUXCtrlStartAddr = EXT_PERIPHERAL_START_ADDRESS + 32'h${aMUX_ctrl_start_address};
  localparam logic [31:0] CheepaMUXCtrlEndAddr = CheepaMUXCtrlStartAddr + 32'h${aMUX_ctrl_size};

  // dLC controller
  localparam int unsigned CheepdLCIdx = 32'd5;
  localparam logic [31:0] CheepdLCStartAddr = EXT_PERIPHERAL_START_ADDRESS + 32'h${dLC_start_address};
  localparam logic [31:0] CheepdLCEndAddr = CheepdLCStartAddr + 32'h${dLC_size};

  // CIC
  localparam int unsigned CheepCICIdx = 32'd6;
  localparam logic [31:0] CheepCICStartAddr = EXT_PERIPHERAL_START_ADDRESS + 32'h${CIC_start_address};
  localparam logic [31:0] CheepCICEndAddr = CheepCICStartAddr + 32'h${CIC_size};

  // External peripherals address map
  localparam addr_map_rule_t [ExtPeriphNSlave-1:0] ExtPeriphAddrRules = '{
    '{idx: CheepiDACCtrlIdx, start_addr: CheepiDACCtrlStartAddr, end_addr: CheepiDACCtrlEndAddr},
    '{idx: CheepVCODecoderIdx, start_addr: CheepVCODecoderStartAddr, end_addr: CheepVCODecoderEndAddr},
    '{idx: CheepSESFilterIdx, start_addr: CheepSESFilterStartAddr, end_addr: CheepSESFilterEndAddr},
    '{idx: CheepREFsCtrlIdx, start_addr: CheepREFsCtrlStartAddr, end_addr: CheepREFsCtrlEndAddr},
    '{idx: CheepaMUXCtrlIdx, start_addr: CheepaMUXCtrlStartAddr, end_addr: CheepaMUXCtrlEndAddr},
    '{idx: CheepdLCIdx, start_addr: CheepdLCStartAddr, end_addr: CheepdLCEndAddr},
    '{idx: CheepCICIdx, start_addr: CheepCICStartAddr, end_addr: CheepCICEndAddr}
  };
endpackage
