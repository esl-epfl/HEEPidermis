// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: pad_cell_input_sva.svh
// Author: Michele Caon
// Date: 14/05/2023
// Description: SVA assertions for the input pad cell

// Check that the input pad is never driven
assert (pad_oe_i != 1'b0) 
else $error("Attempt to drive input pad (pad_oe_i = 1)");
