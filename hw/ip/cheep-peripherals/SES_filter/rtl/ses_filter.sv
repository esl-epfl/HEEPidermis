// Copyright 2025 EPFL
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Authors: Jérémie Moullet <jeremie.moullet@epfl.ch>, EPFL, STI-SEL
//
// Date: 06.2025
//
// Description: Top-level Simple Exponential Smoothing (SES) filter.
//              Converts 1-bit DSM input into decimated, smoothed PCM output
//              using a configurable number of SES stages.
//
// Compile-Time Parameters:
//   - MAXIMUM_WIDTH  : Bit-width of internal datapath (default: 32).
//   - FIFO_DEPTH     : Depth of the CDC FIFO buffer.
//
// Runtime Configuration (via registers):
//   - ses_window_size          : Common window size (Ww) for all stages.
//   - ses_decim_factor         : Output decimation factor.
//   - ses_sysclk_division      : Division factor for generating clk_fs_o.
//   - ses_activated_stages     : Thermometric bitmask to enable SES stages (contiguous '1's, right-aligned).
//   - ses_gain_stage           : Per-stage input gain (WgX).
//
// Ports:
//   - clk_sys_i, rst_ni        : System clock and active-low reset.
//   - dsm_i                    : 1-bit delta-sigma modulated input signal.
//   - clk_fs_o                 : Sampling clock derived from clk_sys_i.
//   - req_i, rsp_o             : Register bus interface.
//   - SES_activated            : Indicates whether SES filtering is active.
//   - SES_dataValid            : High when filtered PCM output is ready.
//
// Control and status:
//   - ses_control              : Enables/disables the SES filter.
//   - ses_status               : Status register indicating filter state (PCM data Valid, activated).
//
// Output:
//   - rx_data (via FIFO window)
//
// Notes:
//   - Internally instantiates multiple `ses_stage` modules.
//   - clk_fs_o is derived by dividing clk_sys_i by ses_sysclk_division.
//   - Applies SES filtering, then decimates the result.
//   - Output is buffered across clock domains using a CDC FIFO.
//   - Results are accessed via the register-mapped window interface.
//   - If the DEBUG section is uncommented, it will print filtered output values directly to the console.

module ses_filter #(
    parameter type reg_req_t = logic,
    parameter type reg_rsp_t = logic,

    parameter integer MAXIMUM_WIDTH = 32,  // Maximum width of the internal signals. If bigger than 32, risk of truncation
    parameter integer FIFO_DEPTH = 4
) (
    input logic clk_sys_i,
    input logic rst_ni,

    // "Pad" interface
    input  logic dsm_i,
    output logic clk_fs_o,

    // Bus interface
    input  reg_pkg::reg_req_t req_i,
    output reg_pkg::reg_rsp_t rsp_o,

    // SES_active
    output logic SES_activated,  // actived
    output logic SES_dataValid   // filtered output
);
  // Hardware --> Registers
  ses_filter_reg_pkg::ses_filter_hw2reg_t hw2reg;

  // Registers --> hardware
  ses_filter_reg_pkg::ses_filter_reg2hw_t reg2hw;

  // CDC FIFO window interface
  reg_pkg::reg_req_t fifo_win_h2d;
  reg_pkg::reg_rsp_t fifo_win_d2h;

  // SES filter registers
  ses_filter_reg_top #(
      .reg_req_t(reg_pkg::reg_req_t),
      .reg_rsp_t(reg_pkg::reg_rsp_t)
  ) u_ses_filter_reg_top (
      .clk_i        (clk_sys_i),
      .rst_ni       (rst_ni),
      .reg_req_win_o(fifo_win_h2d),
      .reg_rsp_win_i(fifo_win_d2h),
      .reg_req_i    (req_i),
      .reg_rsp_o    (rsp_o),
      .reg2hw       (reg2hw),
      .hw2reg       (hw2reg),
      .devmode_i    (1'b0)
  );

  //--------------Parameters computed at compilation time--------------------
  localparam integer DecimWidth = $bits(reg2hw.ses_decim_factor.q);
  localparam integer SysclkDivWidth = $bits(reg2hw.ses_sysclk_division.q);
  localparam integer StatusWidth = $bits(hw2reg.ses_status.d);
  localparam integer windowSizeWidth = $bits(reg2hw.ses_window_size.q);
  localparam integer InputGainWidth = $bits(reg2hw.ses_gain_stage.gain_stg_0.q);
  localparam integer SesStageNumber = $bits(reg2hw.ses_activated_stages.q);
  localparam integer Log2SesStageNumber = $clog2(SesStageNumber);
  localparam integer Log2FifoDepth = $clog2(FIFO_DEPTH);


  //--------------Link between the register and the hardware-----------------
  logic control;
  logic [InputGainWidth-1:0] width_gain[SesStageNumber];
  logic [windowSizeWidth-1:0] window_size;
  logic [SesStageNumber-1:0] activates_stages;
  logic [DecimWidth-1:0] decim_factor;
  logic [SysclkDivWidth-1:0] sysclk_div;

  assign control = reg2hw.ses_control.q;

  generate
    genvar k;
    for (k = 0; k < SesStageNumber; k++) begin : gen_link_gain
      assign width_gain[SesStageNumber-k-1] = reg2hw.ses_gain_stage[InputGainWidth*(k+1)-1:InputGainWidth*k];
    end
  endgenerate

  assign window_size = reg2hw.ses_window_size.q;
  assign activates_stages = (control) ? reg2hw.ses_activated_stages.q : '0;
  assign decim_factor = reg2hw.ses_decim_factor.q;
  assign sysclk_div = reg2hw.ses_sysclk_division.q;

  //---------------Signal declaration---------------------------------------
  logic [     MAXIMUM_WIDTH-1:0] stages_outputs      [SesStageNumber+1];
  logic [Log2SesStageNumber-1:0] msb_index;

  logic [     MAXIMUM_WIDTH-1:0] mux_out;

  //-----------------Division of the system clk------------------------------
  logic [        DecimWidth-1:0] sysclk_div_counter;
  logic                          clock_fs;

  //-----------------Decimation----------------------------------------------
  logic [        DecimWidth-1:0] decim_counter;
  logic [     MAXIMUM_WIDTH-1:0] filtered_data;
  logic                          data_valid;

  //---------------Status register------------------------------------------
  logic [       StatusWidth-1:0] status;
  logic                          status_valid;

  //-----------------Output CDC FIFO stage----------------------------------
  logic                          cdc_fifo_src_ready;
  logic                          cdc_fifo_src_valid;

  logic [     MAXIMUM_WIDTH-1:0] cdc_fifo_dst_data_o;
  logic                          cdc_fifo_dst_valid;
  logic                          cdc_fifo_dst_ready;

  always_ff @(posedge clk_sys_i or negedge rst_ni) begin
    //reset
    if (!rst_ni) begin
      sysclk_div_counter <= '0;
      clock_fs <= '0;

      //activated
    end else if (control) begin
      if (sysclk_div_counter >= (sysclk_div >> 1) - 1) begin
        clock_fs <= ~clock_fs;
        sysclk_div_counter <= '0;
      end else begin
        clock_fs <= clock_fs;
        sysclk_div_counter <= sysclk_div_counter + 1;
      end

      //deactivated
    end else begin
      clock_fs <= '0;
      sysclk_div_counter <= '0;
    end
  end

  assign clk_fs_o = clock_fs;

  //---------------Generation of the SES stages-----------------------------
  generate
    for (k = 0; k < SesStageNumber; k++) begin : gen_ses_stage
      ses_stage #(
          .MAXIMUM_WIDTH(MAXIMUM_WIDTH),
          .WINDOW_SIZE_WIDTH(windowSizeWidth),
          .INPUT_GAIN_SIZE_WIDTH(InputGainWidth)
      ) u_ses_stage_inst (
          .rst_ni(rst_ni),
          .clk_i(clock_fs),
          .activated_i(activates_stages[k]),
          .data_i(stages_outputs[k]),
          .Ww(window_size),
          .Wg(width_gain[k]),
          .data_o(stages_outputs[k+1])
      );
    end
  endgenerate

  //-----------------MUX for the stages output-------------------------------
  assign stages_outputs[0] = {{(MAXIMUM_WIDTH - 1) {1'b0}}, dsm_i};

  always_comb begin
    msb_index = '0;
    for (int i = SesStageNumber - 1; i >= 0; i--) begin
      if (activates_stages[i]) begin
        msb_index = i[Log2SesStageNumber-1:0] + 1;
        break;
      end
    end
  end

  assign mux_out = stages_outputs[msb_index];


  always_ff @(posedge clock_fs or negedge rst_ni) begin
    if (!rst_ni) begin
      data_valid <= 1'b0;
      decim_counter <= '0;
      filtered_data <= '0;

    end else if (decim_counter >= decim_factor) begin
      data_valid <= 1'b1;
      filtered_data <= mux_out;
      decim_counter <= 1;

    end else begin
      data_valid <= 1'b0;
      filtered_data <= filtered_data;
      decim_counter <= decim_counter + 1;
    end
  end

  //---------------Status register------------------------------------------
  always_ff @(posedge clk_sys_i or negedge rst_ni) begin
    if (!rst_ni) begin
      status <= '0;
      status_valid <= 1'b0;

    end else begin
      status <= {cdc_fifo_dst_valid, control};
      status_valid <= ~status_valid;
    end
  end

  assign hw2reg.ses_status.d  = status;
  assign hw2reg.ses_status.de = status_valid;

  assign cdc_fifo_src_valid   = data_valid & cdc_fifo_src_ready;


  cdc_fifo_gray #(
      .T(logic [MAXIMUM_WIDTH-1:0]),
      .LOG_DEPTH(Log2FifoDepth)
  ) pdm2pcm_fifo_i (
      .src_clk_i  (clock_fs),
      .src_rst_ni (rst_ni),
      .src_ready_o(cdc_fifo_src_ready),
      .src_data_i (filtered_data),
      .src_valid_i(cdc_fifo_src_valid),

      .dst_rst_ni (rst_ni),
      .dst_clk_i  (clk_sys_i),
      .dst_data_o (cdc_fifo_dst_data_o),
      .dst_valid_o(cdc_fifo_dst_valid),
      .dst_ready_i(cdc_fifo_dst_ready)
  );

  ses_filter_window #(
      .reg_req_t(reg_req_t),
      .reg_rsp_t(reg_rsp_t),
      .MAXIMUM_WIDTH(MAXIMUM_WIDTH)
  ) u_window (
      .rx_win_i  (fifo_win_h2d),
      .rx_win_o  (fifo_win_d2h),
      .rx_data_i (cdc_fifo_dst_data_o),
      .rx_ready_o(cdc_fifo_dst_ready)
  );

  //-----------------sync with DSM stage------------------------------------
  assign SES_activated = control;
  assign SES_dataValid = cdc_fifo_dst_valid;

  //---------------DEBUG ONLY, do not push uncommented----------------------
  /*
  always @(posedge data_valid) begin
    $display("[Time %0t ps] filtered output = %x", $time, cdc_fifo_dst_data_o);
  end
  */

endmodule  // ses_filter
