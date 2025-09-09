# Copyright 2024 EPFL and Universidad Complutense de Madrid
# Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

# Author: David Mallasén
# Date: 14/05/2024
# Adapted from https://github.com/lowRISC/opentitan/blob/810fcea0509bc81d91750f854754366e06345548/hw/top_earlgrey/util/vivado_setup_hooks.tcl

# Setup hook scripts, to be called at various stages during the build process
# See Xilinx UG 894 ("Using Tcl Scripting") of the Vivado User Guide for documentation.

# fusesoc-generated workroot containing the Vivado project file (.xpr)
set workroot [pwd]

# Pre opt design hook
set_property STEPS.OPT_DESIGN.TCL.PRE "${workroot}/vivado_hook_opt_design_pre.tcl" [get_runs impl_1]
