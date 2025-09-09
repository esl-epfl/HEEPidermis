// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: tb_system.sv
// Author: Michele Caon
// Date: 15/06/2023
// Description: cheep testbench system

module tb_system #(
    parameter int unsigned CLK_FREQ = 32'd100_000  // kHz
) (
    inout wire ref_clk_i,
    inout wire rst_ni,

    // Static configuration
    inout wire boot_select_i,
    inout wire execute_from_flash_i,

    inout wire jtag_tck_i,
    inout wire jtag_tms_i,
    inout wire jtag_trst_ni,
    inout wire jtag_tdi_i,
    inout wire jtag_tdo_o,

    // Exit signals
    inout wire        exit_valid_o,
    inout wire [31:0] exit_value_o
);
  // Include testbench utils
  `include "tb_util.svh"

  // INTERNAL SIGNALS
  // ----------------

  // UART
  wire cheep_uart_tx;
  wire cheep_uart_rx;

  // GPIO
  wire gpio;

`ifndef VERILATOR
  // Pull-up/down are needed in Questasim
  // This feature will be supported in Verilator 5.
  // For Verilator 4 and below, this is actually not needed.
  // SPI flash
  tri0 spi_flash_sck;
  tri1 spi_flash_cs_0;
  tri1 spi_flash_cs_1;
  tri0 spi_flash_sd_0;
  tri0 spi_flash_sd_1;

`else
  wire spi_flash_sck;
  wire spi_flash_cs_0;
  wire spi_flash_cs_1;
  wire spi_flash_sd_0;
  wire spi_flash_sd_1;

`endif

  // GPIO
  wire clk_div;

  wire lc_xing, lc_dir;

  wire dsm_clk, dsm_in;

  wire vco_counter_overflow;

  // UART DPI emulator
  uartdpi #(
      .BAUD('d256000),
      .FREQ(CLK_FREQ * 1000),  // Hz
      .NAME("uart")
  ) u_uartdpi (
      .clk_i (u_cheep_top.u_cheep_peripherals.system_clk_o),
      .rst_ni(rst_ni),
      .tx_o  (cheep_uart_rx),
      .rx_i  (cheep_uart_tx)
  );

  // SPI flash emulator
`ifndef VERILATOR
  spiflash u_flash_boot (
      .csb(spi_flash_cs_0),
      .clk(spi_flash_sck),
      .io0(spi_flash_sd_0),
      .io1(spi_flash_sd_1),
      .io2(),
      .io3()
  );

`endif  /* VERILATOR */


`ifdef USE_PG_PIN
  supply1 VDD;
  supply0 VSS;
`endif

`ifdef RTL_SIMULATION

  localparam int unsigned SwitchAckLatency = 45;

  //pretending to be SWITCH CELLs that delay by SwitchAckLatency cycles the ACK signal
  // This is done only for HEEP memories as CPU and Peripherals ACKs are hardwired in CHEEP top level
  // and Carus and CGRAs memories simulation models delay the ACK signal by 1 cycles

  logic [core_v_mini_mcu_pkg::NUM_BANKS-1:0] tb_memory_subsystem_banks_powergate_switch_ack_n[SwitchAckLatency+1];
  logic [core_v_mini_mcu_pkg::NUM_BANKS-1:0] delayed_tb_memory_subsystem_banks_powergate_switch_ack_n;

  always_ff @(negedge u_cheep_top.u_cheep_peripherals.system_clk_o) begin
    tb_memory_subsystem_banks_powergate_switch_ack_n[0] <= u_cheep_top.u_core_v_mini_mcu.memory_subsystem_banks_powergate_switch_n;
    for (int i = 0; i < SwitchAckLatency; i++) begin
      tb_memory_subsystem_banks_powergate_switch_ack_n[i+1] <= tb_memory_subsystem_banks_powergate_switch_ack_n[i];
    end
  end

  assign delayed_tb_memory_subsystem_banks_powergate_switch_ack_n = tb_memory_subsystem_banks_powergate_switch_ack_n[SwitchAckLatency];

  always_comb begin
`ifndef VERILATOR
    force u_cheep_top.u_core_v_mini_mcu.memory_subsystem_banks_powergate_switch_ack_n = delayed_tb_memory_subsystem_banks_powergate_switch_ack_n;
`else
    u_cheep_top.u_core_v_mini_mcu.memory_subsystem_banks_powergate_switch_ack_n = delayed_tb_memory_subsystem_banks_powergate_switch_ack_n;
`endif
  end

`endif

`ifdef POSTLAYOUT
`ifdef LOAD_SDF
  initial begin
    $sdf_annotate("../../../implementation/pnr/symlinks/common_pnr_build/cheep.sdf.gz", tb_top.u_tb_system.u_cheep_top,, "sdf.log", "TYPICAL",,);
  end
`endif
`endif

  // DUT
  // ---
  cheep_top u_cheep_top (
`ifdef USE_PG_PIN
      .VSS,
      .VDD,
`endif
      .rst_ni              (rst_ni),
      .boot_select_i       (boot_select_i),
      .execute_from_flash_i(execute_from_flash_i),
      .jtag_tck_i          (jtag_tck_i),
      .jtag_tms_i          (jtag_tms_i),
      .jtag_trst_ni        (jtag_trst_ni),
      .jtag_tdi_i          (jtag_tdi_i),
      .jtag_tdo_o          (jtag_tdo_o),
      .uart_rx_i           (cheep_uart_rx),
      .uart_tx_o           (cheep_uart_tx),
      .exit_valid_o        (exit_valid_o),
      .gpio_0_io           (gpio),

      .spi_slave_sck_i(spi_flash_sck),
      .spi_slave_cs_i(spi_flash_cs_1),
      .spi_slave_mosi_i(spi_flash_sd_0),
      .spi_slave_miso_io(spi_flash_sd_1),

      .spi_flash_sck_o  (spi_flash_sck),
      .spi_flash_cs_0_o (spi_flash_cs_0),
      .spi_flash_cs_1_io(spi_flash_cs_1),
      .spi_flash_sd_0_io(spi_flash_sd_0),
      .spi_flash_sd_1_io(spi_flash_sd_1),

      .vco_counter_overflow_io(vco_counter_overflow),
      .lc_xing_io             (lc_xing),
      .lc_dir_io              (lc_dir),
      .dsm_clk_io             (dsm_clk),
      .dsm_in_io              (dsm_in),
      .clk_i                  (ref_clk_i),
      .exit_value_o           (exit_value_o[0])
  );

  pdm2pcm_dummy #(
      .filepath("../../../hw/vendor/x-heep/hw/ip/pdm2pcm/tb/signals/pdm.txt")
  ) pdm2pcm_dummy_i (
      .clk_i     (ref_clk_i),
      .rst_ni    (rst_ni),
      .pdm_data_o(dsm_in),
      .pdm_clk_i (dsm_clk)
  );

  // Exit value
  assign exit_value_o[31:1] = u_cheep_top.u_core_v_mini_mcu.exit_value_o[31:1];
endmodule
