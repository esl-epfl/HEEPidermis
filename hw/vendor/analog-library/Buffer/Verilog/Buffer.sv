// Buffer dummy behavioral model
// SystemVerilog Model
// Juan Sapriza - 2025

module Buffer (
    input   logic       SEL,                             // Enable signal (active high)
    inout   wire        AVDD,
    inout   wire        IN,
    inout   wire        OUT
);

    `ifndef VERILATOR

    integer     VOUT_int_mV;                      // The output voltage to be observed on the Buffer output

    initial forever begin
        #1ns;
        if (!SEL)  VOUT_int_mV = 0;
        else begin
            VOUT_int_mV = int'(500.0 + 100.0 * $sin(6.283185307179586 * 100e3 * $realtime * 1e-9));
        end
    end
    `else
    initial begin
        $display("============= The Buffer model will not work on Verilator ===========");
    end
    `endif

endmodule
