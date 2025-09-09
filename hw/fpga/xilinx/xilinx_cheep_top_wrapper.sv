// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: xilinx_cheep_top_wrapper.sv
// Author: Luigi Giuffrida
// Date: 01/02/2024
// Description: cheep FPGA top-level module

module xilinx_cheep_top_wrapper #(
  parameter int CLK_LED_COUNT_LENGTH = 27
) (

`ifdef FPGA_ZCU104
  inout logic clk_300mhz_n,
  inout logic clk_300mhz_p,
`else
  inout logic clk_i,
`endif
  inout logic rst_i,

  //visibility signals
  output logic rst_led_o,
  output logic clk_led_o,

  inout logic boot_select_i,
  inout logic execute_from_flash_i,

  inout logic jtag_tck_i,
  inout logic jtag_tms_i,
  inout logic jtag_trst_ni,
  inout logic jtag_tdi_i,
  inout logic jtag_tdo_o,

  inout logic uart_rx_i,
  inout logic uart_tx_o,

  inout logic [17:0] gpio_io,

  output logic exit_value_o,
  inout  logic exit_valid_o,

  inout logic [3:0] spi_flash_sd_io,
  inout logic       spi_flash_csb_o,
  inout logic       spi_flash_sck_o,

  inout logic [3:0] spi_sd_io,
  inout logic       spi_csb_o,
  inout logic       spi_sck_o,

  inout logic [3:0] spi2_sd_io,
  inout logic [1:0] spi2_csb_o,
  inout logic       spi2_sck_o,

  inout logic i2c_scl_io,
  inout logic i2c_sda_io,

  inout logic pdm2pcm_clk_io,
  inout logic pdm2pcm_pdm_io,

  inout logic i2s_sck_io,
  inout logic i2s_ws_io,
  inout logic i2s_sd_io

);

  wire                               clk_gen;
  logic [                      31:0] exit_value;
  wire                               rst_n;
  logic [CLK_LED_COUNT_LENGTH - 1:0] clk_count;
  wire                               zero_value;

  // low active reset
`ifdef FPGA_NEXYS
  assign rst_n = rst_i;
`else
  assign rst_n = !rst_i;
`endif

  // reset LED for debugging
  assign rst_led_o = rst_n;

  // counter to blink an LED
  assign clk_led_o = clk_count[CLK_LED_COUNT_LENGTH-1];

  always_ff @(posedge clk_gen or negedge rst_n) begin : clk_count_process
    if (!rst_n) begin
      clk_count <= '0;
    end else begin
      clk_count <= clk_count + 1;
    end
  end

`ifdef FPGA_ZCU104
  xilinx_clk_wizard_wrapper xilinx_clk_wizard_wrapper_i (
    .CLK_IN1_D_0_clk_n(clk_300mhz_n),
    .CLK_IN1_D_0_clk_p(clk_300mhz_p),
    .clk_out1_0       (clk_gen)
  );
`elsif FPGA_NEXYS
  xilinx_clk_wizard_wrapper xilinx_clk_wizard_wrapper_i (
    .clk_100MHz(clk_i),
    .clk_out1_0(clk_gen)
  );
`else  // FPGA PYNQ-Z2
  xilinx_clk_wizard_wrapper xilinx_clk_wizard_wrapper_i (
    .clk_125MHz(clk_i),
    .clk_out1_0(clk_gen)
  );
`endif

  // Instantiate the cheep top-level module
  cheep_top i_cheep_top (
    .rst_ni   (rst_n),
    .ref_clk_i(clk_gen),

    .boot_select_i,
    .execute_from_flash_i,
    .jtag_tck_i,
    .jtag_tms_i,
    .jtag_trst_ni,
    .jtag_tdi_i,
    .jtag_tdo_o,
    .uart_rx_i,
    .uart_tx_o,
    .exit_valid_o,
    .gpio_0_io        (gpio_io[0]),
    .gpio_1_io        (gpio_io[1]),
    .gpio_2_io        (gpio_io[2]),
    .gpio_3_io        (gpio_io[3]),
    .gpio_4_io        (gpio_io[4]),
    .gpio_5_io        (gpio_io[5]),
    .gpio_6_io        (gpio_io[6]),
    .gpio_7_io        (gpio_io[7]),
    .gpio_8_io        (gpio_io[8]),
    .gpio_9_io        (gpio_io[9]),
    .gpio_10_io       (gpio_io[10]),
    .gpio_11_io       (gpio_io[11]),
    .gpio_12_io       (gpio_io[12]),
    .gpio_13_io       (gpio_io[13]),
    .gpio_14_io       (gpio_io[14]),
    .gpio_15_io       (gpio_io[15]),
    .gpio_16_io       (gpio_io[16]),
    .gpio_17_io       (gpio_io[17]),
    .spi_flash_sck_io (spi_flash_sck_o),
    .spi_flash_cs_0_io(spi_flash_csb_o),
    .spi_flash_sd_0_io(spi_flash_sd_io[0]),
    .spi_flash_sd_1_io(spi_flash_sd_io[1]),
    .spi_flash_sd_2_io(spi_flash_sd_io[2]),
    .spi_flash_sd_3_io(spi_flash_sd_io[3]),

    .spi_sck_io (spi_sck_o),
    .spi_cs_0_io(spi_csb_o),
    .spi_cs_1_io(),
    .spi_sd_0_io(spi_sd_io[0]),
    .spi_sd_1_io(spi_sd_io[1]),
    .spi_sd_2_io(spi_sd_io[2]),
    .spi_sd_3_io(spi_sd_io[3]),

    .i2s_sck_io,
    .i2s_ws_io,
    .i2s_sd_io,
    .exit_value_o
  );


endmodule  // cheep_top
