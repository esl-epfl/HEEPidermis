// vREF dummy behavioral model
// SystemVerilog Model
// Juan Sapriza - 2025

module vREF #(
    parameter real VNOMINAL_mV = 800,               // Nominal output voltage
    parameter real VLSB_mV = 8                      // How much each LSB of calibration changes the output voltage
)(
`ifdef USE_PG_PIN
    inout   wire    VREF_VSS,
`endif
    input   logic [vref_pkg::VrefCalibrationWidth-1:0] CAL, // Calibration bits (5-bit)
    output  integer     VOUT_int_mV                 // The output voltage to be observed on the output
);

    always_comb begin
        VOUT_int_mV = $rtoi(VNOMINAL_mV - (2**(vref_pkg::VrefCalibrationWidth-1))*VLSB_mV + CAL*VLSB_mV);
    end

    
endmodule

