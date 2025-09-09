// iDAC Module
// SystemVerilog Model
// Filippo Quadri - 2025

// =============================================================
//  SystemVerilog DAC Module - Overview and Port Description
// =============================================================
//
//  Description:
//  - Implements an 8-bit Digital-to-Analog Converter (DAC) with
//    optional 5-bit calibration capability.
//  - Two behavioral models available:
//      • Polynomial model: fitted to SPICE data (DAC_IN = 127)
//      • Linear model: ideal theoretical behavior
//  - Fixed input current (DAC_IIN_PARAM_nA) is set to 400nA.
//  - To evaluate calibration effects, use the polynomial model
//    or modify DAC_IIN_PARAM_nA to introduce variability.
//  - The values of the current are saved in a file
//
//  ----------------------------------------------------------------
//  TO USE THE POLYNOMIAL MODEL:
//  - Set `parameter poly = 1`
//
//  TO USE THE LINEAR MODEL:
//  - Set `parameter poly = 1`
//
//  ----------------------------------------------------------------
//
//  Port Summary:
//  ----------------------------------------------------------------
//  Signal Name     | Direction | Description
//  ----------------------------------------------------------------
//  DAC_EN          |   input   | Enable signal (active high)
//  DAC_REFRESH     |   input   | Updates DAC_IN on rising edge
//  DAC_CAL[4:0]    |   input   | Calibration control (5-bit)
//  DAC_IN[7:0]     |   input   | Digital input code (8-bit)
//  DAC_IIN_PARAM_nA   |   param   | Fixed input current (400nA)
//  ----------------------------------------------------------------
//  DAC_IOUT_int_nA |   output  | Analog output current
//  ----------------------------------------------------------------
//
//  Notes:
//  - DAC_EN enables the DAC output current.
//  - DAC_REFRESH latches the input value on its rising edge.
//  - DAC_CAL adjusts the output current via calibration logic.
//  - DAC_IN defines the desired digital input and sets the
//    corresponding theoretical output current.
//  - DAC_IOUT_int_nA provides the resulting analog output current.
//  - The DAC_IIN and DAC_IOUT signals are defined only for synthesis
//    and thus they are left unconnected in simulation.
//
// =============================================================

module iDAC #(
    parameter int DAC_IIN_PARAM_nA = 400,                // Fixed input current (400nA)
    parameter string FILENAME = "iDAC_results.txt" // File name for results
) (
    input   logic       DAC_EN,                     // Enable signal (active high)
    input   logic       DAC_REFRESH,                // Updates DAC_IN on rising edge
    input   logic [4:0] DAC_CAL,                    // Calibration control (5-bit)
    input   logic [7:0] DAC_IN,                      // Digital input code (8-bit)
`ifdef SYNTHESIS
    inout   wire        DAC_IIN,
    inout   wire        DAC_IOUT,
`endif
`ifdef USE_PG_PIN
    inout   wire        DAC_VSS,
    inout   wire        DAC_VDD,
`endif
    output  integer     DAC_IOUT_int_nA
);

    logic [7:0] in_r;
    logic reset_in_r;
    int res_file;

`ifdef USE_PG_PIN
    always_comb begin
        if (DAC_VDD === 1'bx || DAC_VDD === 1'bz)
            $fatal("Warning: Unknown value for DAC_VDD in iDAC at %0t", DAC_VDD, $time);
        if (DAC_VSS === 1'bx || DAC_VSS === 1'bz)
            $fatal("Warning: Unknown value for DAC_VSS in iDAC at %0t", DAC_VSS, $time);
    end
`endif

    initial begin
        // Open file for writing results
        res_file = $fopen(FILENAME, "w");
        if (res_file == 0) begin
            $display("Error: Unable to open file %s", FILENAME);
        end
        $fwrite(res_file, "==== iDAC Simulation Started ====\n\n");
    end


    always_ff @(posedge DAC_REFRESH, negedge reset_in_r) begin
        if (reset_in_r == 0) begin
            in_r <= '0;
        end
        else begin
            in_r <= DAC_IN;
        end
    end

    always_comb begin
        if (DAC_EN) begin
            reset_in_r = 1;
            DAC_IOUT_int_nA = (32'sd8
                  * $signed(DAC_IIN_PARAM_nA)
                  * $signed({24'd0, in_r}))
                 / (32'sd95 - $signed({27'd0, DAC_CAL}));
        end else begin
            // Disable output current when DAC_EN is low
            reset_in_r = 0;
            DAC_IOUT_int_nA = 0;
        end
    end

    // Save the output
    always_comb begin
        $fwrite(res_file, "\n==================================\n");
        $fwrite(res_file, "Time    : %3t\n", $time);
        $fwrite(res_file, "Enable  : %3b\n", DAC_EN);
        $fwrite(res_file, "In      : %3d\n", in_r);
        $fwrite(res_file, "Cal     : %3d\n", DAC_CAL);
        $fwrite(res_file, "Refresh : %3b\n", DAC_REFRESH);
        $fwrite(res_file, "----------------------------------\n");
        $fwrite(res_file, "Iout  : %.2f nA (%.2f uA)\n", DAC_IOUT_int_nA, DAC_IOUT_int_nA * 1e3);
        $fwrite(res_file, "==================================\n");
    end

endmodule

