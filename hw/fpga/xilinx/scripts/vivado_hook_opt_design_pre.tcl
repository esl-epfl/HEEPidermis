# Copyright 2024 EPFL and Universidad Complutense de Madrid
# Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

# Author: David Mallasén
# Date: 14/05/2024

# Set parameter to a higher value than default (1000) to fix segfault due to:
# WARNING: [Pwropt 34-321] HACOOException: Too many TFIs and TFOs in design, exiting pwropt. You can change this limit with the param pwropt.maxFaninFanoutToNetRatio
set_param pwropt.maxFaninFanoutToNetRatio 2000