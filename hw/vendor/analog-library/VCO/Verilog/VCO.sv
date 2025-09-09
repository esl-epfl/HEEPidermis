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
`ifdef USE_PG_PIN
    inout   wire        VCO_VSS,
    inout   wire        DCORE_VDD,
`endif
    input  logic [31:0] VIN_int_uV,
    input  logic        REFRESH,
    input  logic        EN,
    output logic        VN0,
    output logic [vco_pkg::VcoCoarseWidth - 1:0] COARSE_OUT,
    output logic [vco_pkg::VcoFineWidth - 1:0] FINE_OUT
);

`ifndef VERILATOR
    // Polynomial Coefficients (integer-scaled)
    logic [5:0] F_index = 0;
    logic [vco_pkg::VcoFineWidth - 1:0] phase_lut [0:61];

    logic   clk_f_osc = 1'b0;
    real    T_ns = '1;
    real    T_last_ns = '1;
    real    T_prev_ns = '1;
    real    f_osc_Hz = '1;

    real    last_f_rising = 0;
    real    fine_ns = 0;
    logic [vco_pkg::VcoCoarseWidth - 1:0] local_counter = 0;

    // --------------------------------------------
    // --- RING OSCILLATOR FREQUENCY GENERATION ---
    // --------------------------------------------
    initial begin
        $readmemh("phase_lut.hex", phase_lut);
        forever begin
            if (EN && T_ns > 0) begin
                #(T_ns/2) clk_f_osc = ~clk_f_osc;
            end else begin
                clk_f_osc = 1'b0;
                // Small delay to prevent tight looping
                #1ns;
            end
        end
    end

    assign VN0 = clk_f_osc;

    always @(T_ns or posedge REFRESH) begin
        T_last_ns = T_prev_ns;
    end

    initial forever begin
        #1ns;
        // Compute the frequency of the ring oscillator
        if (VIN_int_uV < 550e+3) begin
            f_osc_Hz <= 100e+3;
            T_ns <= ((1e+9/f_osc_Hz)) / vco_pkg::VcoBhvFreqGain;
        end else begin
            f_osc_Hz <= (VcoBhvCoef1_Hz + (VcoBhvCoef0_Hz * VIN_int_uV));
            T_ns <=  ((1e+9/f_osc_Hz)) / vco_pkg::VcoBhvFreqGain;
        end
    end

    // --------------------------------------------
    // -----   VCO COUNTER AND FINE OUTPUT    -----
    // --------------------------------------------
    always_ff @(posedge REFRESH) begin
        if (EN) begin
            COARSE_OUT <= local_counter;
            T_prev_ns <= T_ns;
            fine_ns <= $realtime - last_f_rising;
            F_index <= 6'($rtoi(((61 * fine_ns)/(T_last_ns))));
            FINE_OUT <= phase_lut[F_index];
        end else begin
            COARSE_OUT <= '0;
            FINE_OUT <= '0;
        end
    end

    // Track the frequencies changes
    always_ff @(posedge clk_f_osc) begin
        if (!EN) begin
            local_counter <= 0;
        end else begin
            local_counter <= local_counter + 1;
            last_f_rising <= $realtime;
        end
    end

`else
    // Assumes `timescale 1ns/1ps` (or timeunit 1ns/timeprecision 1ps) in your compile.
    // logic [5:0] F_index = '0;
    logic [vco_pkg::VcoFineWidth - 1:0] phase_lut [0:61];

    time    last_ref_ns = 0;
    integer T_ns        = 1;
    integer T_prev_ns   = 1;
    integer T_last_ns   = 1;
    integer f_osc_Hz    = 100000;

    logic [vco_pkg::VcoCoarseWidth - 1:0] local_counter = '0;

    initial begin
        $readmemh("phase_lut.hex", phase_lut);
        VN0 = 1'b0;
    end

    function automatic integer freq_from_vin(input logic [31:0] vin_uV);
        if (vin_uV < 32'd550_000) return 100000; // 100 kHz floor
        return VcoBhvCoef1_Hz + VcoBhvCoef0_Hz * vin_uV;
    endfunction

        always_ff @(posedge REFRESH) begin
            time    now_ns;
            time    dt_ns;
            integer cycles;
            integer rem_ns;
            integer base_T;
            integer fi;

        if (!EN) begin
            COARSE_OUT    <= '0;
            FINE_OUT      <= '0;
            local_counter <= '0;
            last_ref_ns   <= $time;
            T_last_ns     <= (T_prev_ns == 0) ? 1 : T_prev_ns;
            VN0           <= 1'b0;
        end else begin
            // Compute period (ns) from VIN, scaled
            f_osc_Hz = freq_from_vin(VIN_int_uV);
            if (f_osc_Hz < 1) f_osc_Hz = 1;
            T_ns = (1_000_000_000 / f_osc_Hz) / vco_pkg::VcoBhvFreqGain;
            if (T_ns < 1) T_ns = 1;

            // Elapsed time since last REFRESH
            now_ns = $time;
            dt_ns  = now_ns - last_ref_ns;
            last_ref_ns <= now_ns;

            // Count cycles and remainder
            cycles = (dt_ns > 0) ? (dt_ns / T_ns) : 0;
            rem_ns = (dt_ns > 0) ? (dt_ns % T_ns) : 0;

            local_counter <= local_counter + cycles;
            COARSE_OUT    <= local_counter + cycles;

            T_last_ns <= (T_prev_ns == 0) ? T_ns : T_prev_ns;
            T_prev_ns <= T_ns;

            // Fine phase index
            base_T = (T_last_ns != 0) ? T_last_ns : T_ns;
            fi = (base_T != 0) ? ((61 * rem_ns) / base_T) : 0;
            if (fi < 0) fi = 0;
            else if (fi > 61) fi = 61;

            // F_index  <= fi[5:0];
            FINE_OUT <= phase_lut[fi];

            VN0 <= (rem_ns < (T_ns >> 1));
        end
    end
`endif
endmodule

