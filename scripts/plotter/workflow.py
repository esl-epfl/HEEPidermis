# Copyright 2026 EPFL contributors
# SPDX-License-Identifier: Apache-2.0
#
# File: workflow.py
# Author: Ismail Essaidi
# Date: 08/04/2026
# Description: Forward and reverse computation workflows for PoI optimization

import numpy as np
from dataclasses import dataclass
from typing import Tuple, Optional

# Dataclasses definition
@dataclass
class forward_input:
    G_uS: float
    i_dc_uA: float
    fs_Hz: float
    D: float
    N: int

@dataclass
class forward_output:
    delta_G_uS: float

    P_idc_uW: float
    P_vco_uW: float
    P_cnt_uW: float
    P_digital_uW: float
    P_tot_uW: float

@dataclass
class forward_intermediate_variables:
    vin_mV: float
    f_osc_kHz: float
    kvco_kHz_per_mV: float

    df_osc_sampling_Hz: float
    df_osc_adev_Hz: float
    df_osc_Hz: float
    dVin_mV: float

    D_compute: float

    delta_G_range_nS: Tuple[float, float]

@dataclass
class ForwardPointResult:
    input: forward_input
    intermediate: forward_intermediate_variables
    output: forward_output

@dataclass
class reverse_input:
    G_uS: float
    fs_Hz: float
    D: float
    N: int
    delta_G_target_nS: float
    P_tot_max_uW: float


@dataclass
class reverse_output:
    feasible: bool
    i_dc_power_opt_uA: Optional[float]
    i_dc_delta_G_opt_uA: Optional[float]
    delta_G_opt_uS: Optional[float]
    P_tot_opt_uW: Optional[float]
    reason: Optional[str] = None

@dataclass
class ReverseResult:
    input: reverse_input
    output: reverse_output
    i_dc_grid_uA: np.ndarray
    delta_G_grid_uS: np.ndarray
    P_tot_grid_uW: np.ndarray
    feasible_mask: np.ndarray


def digital_power_from_processing_model(fs_Hz: float, N: int):
    """Return computation duty cycle and digital power from the fitted CPU model."""
    N = max(int(N), 1)
    D_compute = fs_Hz * (8.543e-3 + 8.2e-3 / N)
    D_compute = float(np.clip(D_compute, 0.0, 1.0))
    P_digital_uW = D_compute * 19 + (1 - D_compute) * 8
    return D_compute, P_digital_uW


def forward_compute(model, input: forward_input, variance=1):

        vin_mV = model.vin_from_G(input.G_uS, input.i_dc_uA)
        f_osc_kHz = model.fosc_from_vin(vin_mV)
        kvco_kHz_per_mV = model.kvco_kHz_per_mV(vin_mV)

        f_int_Hz = input.fs_Hz / input.D

        df_osc_sampling_Hz = model.df_osc_sampling_Hz(f_int_Hz=f_int_Hz)
        df_osc_adev_Hz = variance * model.df_osc_adev_Hz(vin_mV=vin_mV, f_int_Hz=f_int_Hz)
        df_osc_Hz = model.df_osc_Hz(vin_mV=vin_mV, f_int_Hz=f_int_Hz, variance=variance)
        dVin_mV = model.dVin_mV(df_osc_Hz=df_osc_Hz, vin_mV=vin_mV)

        min_delta_G_nS, max_delta_G_nS = model.compute_delta_G_range_nS(
                                            G_uS=input.G_uS,
                                            f_int_Hz=f_int_Hz,
                                            variance=variance)
        delta_G_uS = model.delta_G_uS(G_uS=input.G_uS, vin_mV=vin_mV, i_dc_uA=input.i_dc_uA, f_int_Hz=f_int_Hz,
                                    variance=variance)

        P_idc_uW = model.idc_power_uW(vin_mV, input.i_dc_uA, input.D)
        P_vco_uW = model.pvco_from_vin(vin_mV, input.D)
        P_cnt_uW = model.pcnt_from_vin(vin_mV, input.D)
        D_compute, P_digital_uW = digital_power_from_processing_model(input.fs_Hz, input.N)
        P_tot_uW = P_idc_uW + P_vco_uW + P_cnt_uW + P_digital_uW

        return ForwardPointResult(

            input=input,
            intermediate=forward_intermediate_variables(
                vin_mV=vin_mV,
                f_osc_kHz=f_osc_kHz,
                kvco_kHz_per_mV=kvco_kHz_per_mV,
                df_osc_sampling_Hz=df_osc_sampling_Hz,
                df_osc_adev_Hz=df_osc_adev_Hz,
                df_osc_Hz=df_osc_Hz,
                D_compute=D_compute,
                dVin_mV=dVin_mV,
                delta_G_range_nS=(min_delta_G_nS,max_delta_G_nS)
            ),
            output=forward_output(
                delta_G_uS=delta_G_uS,
                P_idc_uW=P_idc_uW,
                P_vco_uW=P_vco_uW,
                P_cnt_uW=P_cnt_uW,
                P_digital_uW=P_digital_uW,
                P_tot_uW=P_tot_uW
            )
        )

def reverse_compute(model, input: reverse_input, variance=1):
    max_i_dc = model.i_dc_max(input.G_uS)

    i_vals = model.params.i_dc_range
    i_vals = i_vals[i_vals <= max_i_dc]

    delta_G_vals = []
    P_tot_vals = []
    valid_mask = []

    min_delta_G = np.inf
    min_power = np.inf
    idx_power_opti = 0
    idx_delta_G_opti = 0
    for i, i_dc in enumerate(i_vals):
        vin_mV = model.vin_from_G(input.G_uS, i_dc)

        # basic validity
        if np.isnan(vin_mV):
            delta_G_vals.append(np.nan)
            P_tot_vals.append(np.nan)
            valid_mask.append(False)
            continue

        fwd_in = forward_input(G_uS=input.G_uS, i_dc_uA=i_dc, fs_Hz=input.fs_Hz, D=input.D, N=input.N)
        result = forward_compute(model, fwd_in, variance=variance)

        dG = result.output.delta_G_uS
        Ptot = result.output.P_tot_uW
        if (dG < min_delta_G):
            min_delta_G = dG
            idx_delta_G_opti = i


        if (Ptot < min_power):
            min_power = Ptot
            idx_power_opti = i

        valid = np.isfinite(dG) and np.isfinite(Ptot) 

        delta_G_vals.append(dG)
        P_tot_vals.append(Ptot)
        valid_mask.append(valid)

    delta_G_vals = np.asarray(delta_G_vals, dtype=float)
    P_tot_vals = np.asarray(P_tot_vals, dtype=float)
    valid_mask = np.asarray(valid_mask, dtype=bool)

    target_dG_uS = input.delta_G_target_nS * 1e-3

    feasible_mask = (
        valid_mask &
        (delta_G_vals <= target_dG_uS) &
        (P_tot_vals <= input.P_tot_max_uW)
    )

    if not np.any(feasible_mask):
        return ReverseResult(
            input=input,
            output=reverse_output(
                feasible=False,
                i_dc_power_opt_uA=None,
                i_dc_delta_G_opt_uA=None,
                delta_G_opt_uS=None,
                P_tot_opt_uW=None,
                reason="No i_dc satisfies both ΔG target and power limit."
            ),
            i_dc_grid_uA=i_vals,
            delta_G_grid_uS=delta_G_vals,
            P_tot_grid_uW=P_tot_vals,
            feasible_mask=feasible_mask
        )

    # #NOTE VERY IMPORTANT: This is the minimal power constraint applied (Since higher i_dc gives lower P_tot)
    # idx = np.where(feasible_mask)[0][-1]

    return ReverseResult(
        input=input,
        output=reverse_output(
            feasible=True,
            i_dc_power_opt_uA=float(i_vals[idx_power_opti]),
            i_dc_delta_G_opt_uA=float(i_vals[idx_delta_G_opti]),
            P_tot_opt_uW=float(P_tot_vals[idx_power_opti]),
            delta_G_opt_uS=float(delta_G_vals[idx_delta_G_opti]),
            reason=None
        ),
        i_dc_grid_uA=i_vals,
        delta_G_grid_uS=delta_G_vals,
        P_tot_grid_uW=P_tot_vals,
        feasible_mask=feasible_mask
    )
