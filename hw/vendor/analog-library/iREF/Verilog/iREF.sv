// iREF dummy behavioral model
// SystemVerilog Model
// Juan Sapriza - 2025

module iREF #(
    parameter real INOMINAL_nA = 400,               // Nominal output current
    parameter real ILSB_nA = 4                      // How much each LSB of calibration changes the output current
)(
    input   logic [iref_pkg::IrefCalibrationWidth-1:0] CAL, // Calibration bits (5-bit)
`ifdef USE_PG_PIN
    inout   wire        IREF_VSS,
`endif
    output  integer     IOUT_int_nA                 // The output current to be observed on the output
);

    always_comb begin
        IOUT_int_nA = $rtoi(INOMINAL_nA - (2**(iref_pkg::IrefCalibrationWidth-1))*ILSB_nA + CAL*ILSB_nA);
    end

    
endmodule

