// VCO Module
// Verilog Model
// Filippo Quadri - 2025

// =============================================================
//                       VCO Model Summary
// -------------------------------------------------------------
// This module simulates a digital Voltage-Controlled Oscillator
// (VCO) behavior.
//
// ------------------------- Ports -----------------------------
//   VIN_int_uV : integer, input voltage in microvolts
//   EN         : Active-high enable signal
//   REFRESH    : Rising edge triggers output refresh
//   COARSE_OUT : 26-bit counter output, accumulates VCO ticks
//   FINE_OUT   : 31-bit frequency output from a LUT
//
// ----------------------- Parameters --------------------------
//   VcoBhvCoef0_Hz : Proportionality constant of linear model
//   VcoBhvCoef1_Hz : constant term of linear model
// =============================================================

module VCO #(
    // Very simple linear approximation of the VCO behavior
    // Linear model derived in the characterization spreadsheet:
    // HEEPidermis drive/Blocks/VCO-ADC/VCO-transfer function
    parameter int signed VcoBhvCoef0_Hz = $rtoi(2),
    parameter int signed VcoBhvCoef1_Hz = $rtoi(-1e+6)
)(
    input  logic [31:0] VIN_int_uV,
    input  logic        REFRESH,
    input  logic        EN,
    output logic [vco_pkg::VcoCoarseWidth - 1:0] COARSE_OUT,
    output logic [vco_pkg::VcoFineWidth - 1:0] FINE_OUT
);

    time    last_ref_ns = 0;
    integer T_ps        = 1;
    integer f_osc_Hz    = 100_000;

    logic [vco_pkg::VcoCoarseWidth - 1:0] local_counter = '0;

    // Remove the fine computation from the simulation
    assign FINE_OUT = '0;
    // Make the coarse simply the counter
    assign COARSE_OUT = local_counter;

    // function automatic int transfer_uV_to_Hz(input logic [31:0] vin_uV);
    //     longint v_mV;
    //     longint f_hz_raw;

    //     // Safety check for starting voltage
    //     if (vin_uV < 350_000) return 1; // Return 1kHz min to avoid div-by-zero

    //     v_mV = longint'(vin_uV) / 1000;

    //     // We calculate in Hz first using longint to prevent overflow.
    //     // C2 (5.35) is handled as 535/100
    //     // C3 (0.0065) is handled as 65/10000
    //     f_hz_raw = ( - 180_000
    //                 + (1650 * v_mV)
    //                 - ((535 * v_mV * v_mV) / 100)
    //                 + ((65 * v_mV * v_mV * v_mV) / 10000)
    //             );

    //     // Apply Gain
    //     return int'(f_hz_raw *vco_pkg::VcoBhvFreqGain);
    // endfunction

    function automatic int transfer_uV_to_Hz(input logic [31:0] vin_uV);
        longint x_uV;
        longint acc;
        longint f_hz_raw;

        // x in uV = x in mV * 1000
        x_uV = longint'(vin_uV);

        if (vin_uV < 350_000) return 1;

        acc = 643;                      // 0.00643 * 1e5
        acc = (acc * x_uV) / 1000;      // multiply by x_mV
        acc = acc - 519_000;            // -5.19 * 1e5

        acc = (acc * x_uV) / 1000;      // multiply by x_mV
        acc = acc + 156_000_000;        // 1560 * 1e5

        acc = (acc * x_uV) / 1000;      // multiply by x_mV

        f_hz_raw = (acc / 100_000) - 165_000;



        return int'(f_hz_raw * vco_pkg::VcoBhvFreqGain);
    endfunction

    always_ff @(posedge REFRESH) begin
            time    now_ns;
            time    dt_ns;
            integer cycles;
            integer random;

        if (!EN) begin
            local_counter <= '0;
            last_ref_ns   <= $time;
        end else begin
            // Compute period (ns) from VIN, scaled
            f_osc_Hz = transfer_uV_to_Hz(VIN_int_uV);

            // It was computed that the phase noise for most integration times
            // is very close to the quantization noise of sampling at 100 Hz.
            random = int'($urandom_range(0, 200)) - 100;
            f_osc_Hz = f_osc_Hz + random;
            if (f_osc_Hz < 1) f_osc_Hz = 1;



            T_ps = int'( (64'd1_000_000_000_000) / longint'(f_osc_Hz) );
            if (T_ps < 1) T_ps = 1;

            // Elapsed time since last REFRESH
            now_ns = $time;
            dt_ns  = now_ns - last_ref_ns;
            last_ref_ns <= now_ns;

            // Count cycles and remainder
            cycles = (dt_ns > 0) ? (dt_ns*1000 / T_ps) : 0;

            local_counter <= local_counter + cycles;
        end
    end

endmodule

