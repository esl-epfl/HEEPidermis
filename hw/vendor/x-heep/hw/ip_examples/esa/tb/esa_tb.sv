// Standalone wrapper for ESA's register and DMA HW FIFO interfaces.
module esa_tb (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic [ 2:0] phase_i,
    input  logic        push_i,
    input  logic        pop_i,
    input  logic [31:0] data_i,
    input  logic        reg_valid_i,
    input  logic        reg_write_i,
    input  logic [31:0] reg_addr_i,
    input  logic [31:0] reg_wdata_i,
    output logic        empty_o,
    output logic        full_o,
    output logic        alm_full_o,
    output logic [31:0] data_o,
    output logic        done_o,
    output logic        reg_ready_o,
    output logic        reg_error_o,
    output logic [31:0] reg_rdata_o
);
  reg_pkg::reg_req_t reg_req;
  reg_pkg::reg_rsp_t reg_rsp;
  fifo_pkg::fifo_req_t hw_fifo_req;
  fifo_pkg::fifo_resp_t hw_fifo_resp;

  assign reg_req.valid = reg_valid_i;
  assign reg_req.write = reg_write_i;
  assign reg_req.addr = reg_addr_i;
  assign reg_req.wdata = reg_wdata_i;
  assign reg_req.wstrb = '1;
  assign reg_ready_o = reg_rsp.ready;
  assign reg_error_o = reg_rsp.error;
  assign reg_rdata_o = reg_rsp.rdata;

  assign hw_fifo_req.push = push_i;
  assign hw_fifo_req.pop = pop_i;
  assign hw_fifo_req.flush = 1'b0;
  assign hw_fifo_req.data = data_i;

  assign empty_o = hw_fifo_resp.empty;
  assign full_o = hw_fifo_resp.full;
  assign alm_full_o = hw_fifo_resp.alm_full;
  assign data_o = hw_fifo_resp.data;

  esa dut (
      .clk_i,
      .rst_ni,
      .reg_req_i(reg_req),
      .reg_rsp_o(reg_rsp),
      .hw_fifo_req_i(hw_fifo_req),
      .hw_fifo_resp_o(hw_fifo_resp),
      .esa_done_o(done_o)
  );
endmodule
