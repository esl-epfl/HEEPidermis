
module vco_computation (
    //Inputs
    input logic rstn_i,  //this would be the enable, check!   (VCO_EN)
    input logic clk_i,  //this should come after each refresh (VCO_REFRECH)
    input logic [vco_pkg::VcoFineWidth-1:0] phasesS2,  //phase of VCO (VCO_F_OUT)
    input logic [vco_pkg::VcoCoarseWidth-1:0] coarsecAS,  //counter of VCO (VCO_C_OUT)
    //Outputs
    output logic [vco_pkg::VcoDataWidth-1:0] digdata_o
);

  //constants
  const logic [vco_pkg::VcoFineBinWidth-1:0] number_phases = 62;
  //local variables and signals
  logic [vco_pkg::VcoCoarseWidth-1:0] coarseCountS, coarseCount_previous, coarse1stdiff;
  logic [vco_pkg::VcoFineBinWidth-1:0] binaryph, finephS, fineph_previous;
  int k;

  //COARSE PART after counters
  //sampling and muliplexing
  always_ff @(posedge clk_i, negedge rstn_i) begin
    if (!rstn_i) begin
      coarseCountS <= 0;
      coarseCount_previous <= 0;
    end else begin
      coarseCountS <= coarsecAS;
      coarseCount_previous <= coarseCountS;
    end
  end

  //FINE PART
  //phases selection and thermometer to bynary conversion
  always_comb begin
    binaryph = 0;
    for (k = 1; k <= vco_pkg::VcoFineWidth - 1; k = k + 1) begin
      /* verilator lint_off WIDTH */
      binaryph = binaryph + phasesS2[k];
      /* verilator lint_on WIDTH */
    end
  end

  //sampling for first difference
  always_ff @(posedge clk_i, negedge rstn_i) begin
    if (!rstn_i) begin
      finephS <= 0;
      fineph_previous <= 0;
    end else begin  //add an enable signal?
      finephS <= phasesS2[0] ? binaryph : (61 - binaryph);
      fineph_previous <= finephS;
    end
  end

  //Digital output data
  always_comb begin
    coarse1stdiff = coarseCountS - coarseCount_previous;
    /* verilator lint_off WIDTH */
    digdata_o = number_phases * coarse1stdiff + (finephS - fineph_previous);
    /* verilator lint_on WIDTH */
  end

endmodule
