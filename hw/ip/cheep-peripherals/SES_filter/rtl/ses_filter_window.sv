// Copyright lowRISC contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
//
// Module to manage TX FIFO window for Serial Peripheral Interface (SPI) host IP.
//

module ses_filter_window #(
    parameter type reg_req_t = logic,
    parameter type reg_rsp_t = logic,
    parameter integer MAXIMUM_WIDTH = 32
) (
    input  reg_req_t                     rx_win_i,
    output reg_rsp_t                     rx_win_o,
    input            [MAXIMUM_WIDTH-1:0] rx_data_i,
    output logic                         rx_ready_o
);
  logic [ses_filter_reg_pkg::BlockAw-1:0] rx_addr;
  logic rx_win_error;

  assign rx_win_error = (rx_win_i.write == 1'b1) && (rx_addr != ses_filter_reg_pkg::SES_FILTER_RX_DATA_OFFSET);
  assign rx_ready_o = rx_win_i.valid & ~rx_win_i.write;
  assign rx_win_o.rdata = rx_data_i;
  assign rx_win_o.error = rx_win_error;
  assign rx_win_o.ready = 1'b1;
  assign rx_addr = rx_win_i.addr;

endmodule : ses_filter_window
