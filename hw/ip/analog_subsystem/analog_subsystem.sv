// Copyright 2025 EPFL and contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: analog_subsystem.sv
// Author: Juan Sapriza
// Date: 25/04/2025
// Description: A subsystem where the analog blocks will be instantiated.
// In the case of RTL-only simulations, the spice models are replaced for behavioral models.

module analog_subsystem #(
    parameter real resistance_kO = 30,
    parameter real supply_uV = 800_000
) (
    input logic                                      idac1_enable_i,       // Enable signal (active high)
    input logic [idac_pkg::IdacCalibrationWidth-1:0] idac1_calibration_i,  // Calibration control (5-bit)
    input logic [    idac_pkg::IdacCurrentWidth-1:0] idac1_current_i,      // Digital input code (8-bit)
    input logic                                      idac2_enable_i,       // Enable signal (active high)
    input logic [idac_pkg::IdacCalibrationWidth-1:0] idac2_calibration_i,  // Calibration control (5-bit)
    input logic [    idac_pkg::IdacCurrentWidth-1:0] idac2_current_i,      // Digital input code (8-bit)
    input logic                                      idac_refresh_i,       // Updates idac_current_i on rising edge

    input  logic                               vcop_enable_i,  // Enable signal (active high)
    input  logic                               vcon_enable_i,  // Enable signal (active high)
    output logic [vco_pkg::VcoCoarseWidth-1:0] vcop_coarse_o,  // Coarse output of the VCOp
    output logic [vco_pkg::VcoCoarseWidth-1:0] vcon_coarse_o,  // Coarse output of the VCOn
    output logic [  vco_pkg::VcoFineWidth-1:0] vcop_fine_o,    // Fine output of the VCOp
    output logic [  vco_pkg::VcoFineWidth-1:0] vcon_fine_o,    // Fine output of the VCOn
    input  logic                               vco_refresh_i,  // Updates VCO signals on rising edge

    input logic [amux_pkg::AmuxSelWidth-1:0] amux_sel_i,  // Select the mux's output

    input logic [iref_pkg::IrefCalibrationWidth-1:0] iref1_calibration_i,  // Calibration control
    input logic [iref_pkg::IrefCalibrationWidth-1:0] iref2_calibration_i,  // Calibration control
    input logic [vref_pkg::VrefCalibrationWidth-1:0] vref_calibration_i    // Calibration control

);

  // VERILOG BEHAVIORAL MODELS
  // -------------------------

  /* verilator lint_off UNUSED */
  integer iDAC1_IOUT_int_nA;
  integer iDAC2_IOUT_int_nA;
  integer VCOp_VIN_int_uV;
  integer VCOn_VIN_int_uV;
  /* verilator lint_on UNUSED */

  // Analog blocks

  // VERILOG BEHAVIORAL MODELS
  // -------------------------

  // iDAC
  iDAC u_iDAC1 (
      .DAC_EN(idac1_enable_i),
      .DAC_IN(idac1_current_i),
      .DAC_CAL(idac1_calibration_i),
      .DAC_REFRESH(idac_refresh_i),
      .DAC_IOUT_int_nA(iDAC1_IOUT_int_nA)
  );

  iDAC u_iDAC2 (
      .DAC_EN(idac2_enable_i),
      .DAC_IN(idac2_current_i),
      .DAC_CAL(idac2_calibration_i),
      .DAC_REFRESH(idac_refresh_i),
      .DAC_IOUT_int_nA(iDAC2_IOUT_int_nA)
  );

  always_comb begin
    VCOp_VIN_int_uV = $rtoi(supply_uV - iDAC1_IOUT_int_nA * (resistance_kO / 1.01));
    VCOn_VIN_int_uV = $rtoi(supply_uV - iDAC2_IOUT_int_nA * resistance_kO);
  end

  VCO u_VCOp (
      .VIN_int_uV(VCOp_VIN_int_uV),
      .EN(vcop_enable_i),
      .REFRESH(vco_refresh_i),
      .VN0(),
      .COARSE_OUT(vcop_coarse_o),
      .FINE_OUT(vcop_fine_o)
  );

  VCO u_VCOn (
      .VIN_int_uV(VCOn_VIN_int_uV),
      .EN(vcon_enable_i),
      .REFRESH(vco_refresh_i),
      .VN0(),
      .COARSE_OUT(vcon_coarse_o),
      .FINE_OUT(vcon_fine_o)
  );

  aMUX u_aMUX (.SEL(amux_sel_i));

  iREF u_iREF1 (.CAL(iref1_calibration_i));

  iREF u_iREF2 (.CAL(iref2_calibration_i));

  vREF u_vREF (.CAL(vref_calibration_i));

endmodule
