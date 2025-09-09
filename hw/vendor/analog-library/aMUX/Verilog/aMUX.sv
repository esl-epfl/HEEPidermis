// aMUX dummy behavioral model
// SystemVerilog Model
// Juan Sapriza - 2025

module aMUX (
    input   logic [amux_pkg::AmuxSelWidth-1:0] SEL       // Selection bits (5-bit)
);

    integer     VOUT_int_mV;  // The output voltage to be observed on the aMUX output

    typedef enum logic [2:0] {
        CONST = 3'd0,
        SINE  = 3'd1,
        TRI   = 3'd2,
        SQR   = 3'd3,
        RAND  = 3'd4
    } sel_t;

    sel_t current_sel;

    always_comb begin
        current_sel = sel_t'(SEL);
    end

    `ifndef VERILATOR
    initial forever begin
        VOUT_int_mV = 0;
        #1ns;
        case (current_sel)
            CONST: VOUT_int_mV = 500;
            SINE : VOUT_int_mV = int'(500.0 + 100.0 * $sin(6.283185307179586 * 100e3 * $realtime * 1e-9));
            TRI  : VOUT_int_mV = 123;//int'( ($realtime*1e-6) - int'($realtime*1e-6) < 0.5 ? 1600*($realtime*1e-6) - int'($realtime*1e-6): 800 - 1600*(($realtime*1e-6) - int'($realtime*1e-6)-0.5) );
            SQR  : VOUT_int_mV = ((($realtime * 1e-9) - $rtoi($realtime * 25e3) / 25e3) < 20e-6) ? 800 : 0;
            RAND : VOUT_int_mV = 400 + $urandom_range(0,200);
            default: VOUT_int_mV = 0;
        endcase
    end
    `else
    initial begin
        $display("============= The aMUX model will not work on Verilator ===========");
    end
    `endif

endmodule

