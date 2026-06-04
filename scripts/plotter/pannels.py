# Copyright 2026 EPFL contributors
# SPDX-License-Identifier: Apache-2.0
#
# File: pannels.py
# Author: Ismail Essaidi
# Date: 08/04/2026
# Description: Matplotlib visualization panels for VCO model exploration

import numpy as np
from matplotlib.colors import LogNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

def _digital_power_from_processing_model(fs_Hz, N):
    N = max(int(N), 1)
    D_compute = fs_Hz * (8.543e-3 + 8.2e-3 / N)
    D_compute = float(np.clip(D_compute, 0.0, 1.0))
    P_digital_uW = D_compute * 19 + (1 - D_compute) * 8
    return D_compute, P_digital_uW


def plot_forward_vco_point(ax, model, result):
    vin_plot = model.params.vin_range
    fosc_plot = model.fosc_from_vin(vin_plot)

    ax.scatter(
        model.vin_data, model.fosc_data,
        s=40, color="black", alpha=0.6,
        label="Measured data", zorder=4
    )
    ax.plot(vin_plot, fosc_plot, linewidth=2.5, label="VCO model")

    ax.plot(
        result.intermediate.vin_mV,
        result.intermediate.f_osc_kHz,
        'r*',
        markersize=18,
        label="Operating point",
        zorder=5
    )
    ax.axvline(result.intermediate.vin_mV, color='r', linestyle='--', alpha=0.5)

    ax.set_xlabel("V_in (mV)")
    ax.set_ylabel("f_osc (kHz)")
    ax.set_title("VCO transfer curve")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9)

def plot_forward_summary(ax, result, model=None):
    ax.axis("off")

    txt = (
        f"G:        {result.input.G_uS:.2f} μS\n"
        f"i_dc:     {result.input.i_dc_uA:.2f} μA\n"
        f"f_s:      {result.input.fs_Hz:.1f} Hz\n"
        f"D:        {result.input.D * 100:.0f}%\n"
        f"N:        {result.input.N:.0f} samples\n"
        f"D_compute:{result.intermediate.D_compute * 100:.2f}%\n\n"
        f"V_in:     {result.intermediate.vin_mV:.4f} mV\n"
        f"ΔV_in:    {result.intermediate.dVin_mV*1000:.4f} μV\n"
        f"f_osc:    {result.intermediate.f_osc_kHz:.4f} kHz\n"
        f"Δf_osc:   {result.intermediate.df_osc_Hz:.4f} Hz\n"
        f"K_VCO:    {result.intermediate.kvco_kHz_per_mV:.6f} kHz/mV\n"
    )
    
    # Add constraint if model is provided
    if model is not None and hasattr(model.params, 'v_dd') and hasattr(model.params, 'vin_range'):
        v_dd = model.params.v_dd
        v_min = model.params.vin_range[0]
        i_dc_max = result.input.G_uS * (v_dd - v_min) / 1000
        txt += f"\n─────────────\n"
        txt += f"i_dc_max: {i_dc_max:.4f} μA\nG×(V_dd-V_min)"

    ax.text(
        0.5, 0.5, txt,
        transform=ax.transAxes,
        ha='center', va='center',
        fontsize=12,
        family='monospace',
        bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.25)
    )
    ax.set_title("Inputs and operating point", fontweight='bold')

def plot_forward_df_components(ax, result):
    labels = [
        r'$\Delta f_{samp}$',
        r'$\Delta f_{adev}$',
        r'$\Delta f_{osc}$ = max'
    ]
    values = [
        result.intermediate.df_osc_sampling_Hz,
        result.intermediate.df_osc_adev_Hz,
        result.intermediate.df_osc_Hz
    ]
    
    # Use different colors to highlight that df_osc is the max
    colors = ['steelblue', 'steelblue', 'coral']
    
    bars = ax.bar(labels, values, color=colors, alpha=0.7)
    
    # Add annotation on the max bar showing it's the maximum
    max_bar = bars[2]
    height = max_bar.get_height()
    ax.text(max_bar.get_x() + max_bar.get_width()/2., height,
            'max(sampling, adev)',
            ha='center', va='bottom', fontsize=9, style='italic', color='coral')
    
    ax.set_ylabel("Hz")
    ax.set_title("Frequency error contributions")
    ax.grid(True, axis='y', alpha=0.3)

def plot_forward_duty_tradeoff(ax, model, result, variance=1, avg_window=1, D_min=0.1, D_max=1.0, n_D=80):
    G_uS = result.input.G_uS
    i_dc_uA = result.input.i_dc_uA
    fs_Hz = result.input.fs_Hz

    D_vals = np.linspace(D_min, D_max, n_D)
    deltaG_vals_nS = []
    ptot_vals = []
    f_int_vals = []

    vin_mV = model.vin_from_G(G_uS, i_dc_uA)

    for D in D_vals:
        f_int_Hz = fs_Hz / D
        f_int_vals.append(f_int_Hz)

        try:
            deltaG_vals_nS.append(
                model.delta_G_uS(
                    G_uS=G_uS,
                    vin_mV=vin_mV,
                    i_dc_uA=i_dc_uA,
                    f_int_Hz=f_int_Hz,
                    variance=variance,
                    avg_window=avg_window
                ) * 1000
            )
        except Exception:
            deltaG_vals_nS.append(np.nan)

        p_idc = model.idc_power_uW(vin_mV, i_dc_uA, D)
        p_vco = model.pvco_from_vin(vin_mV, D)
        p_cnt = model.pcnt_from_vin(vin_mV, D)
        ptot_vals.append(p_idc + p_vco + p_cnt + result.output.P_digital_uW)

    D_vals = np.asarray(D_vals, dtype=float)
    deltaG_vals_nS = np.asarray(deltaG_vals_nS, dtype=float)
    ptot_vals = np.asarray(ptot_vals, dtype=float)
    f_int_vals = np.asarray(f_int_vals, dtype=float)

    D_current = result.input.D
    f_int_current = fs_Hz / D_current
    deltaG_current_nS = result.output.delta_G_uS * 1000

    ax.plot(
        D_vals,
        deltaG_vals_nS,
        linewidth=2.5,
        color='steelblue',
        label=r'$\Delta G$'
    )
    ax.plot(D_current, deltaG_current_nS, 'ko', markersize=6, zorder=5)
    ax.axvline(D_current, color='black', linestyle='--', alpha=0.5)

    ax.set_xlabel(r'$D$ (duty cycle)')
    ax.set_ylabel(r'$\Delta G$ (nS)', color='steelblue')
    ax.tick_params(axis='y', labelcolor='steelblue')
    ax.set_title(r'Tradeoff vs $D$')
    ax.grid(True, alpha=0.3)

    ax2 = ax.twinx()
    ax2.plot(
        D_vals,
        ptot_vals,
        linewidth=2.5,
        color='coral',
        label=r'$P_{TOT}$'
    )
    ax2.plot(D_current, result.output.P_tot_uW, 'ko', markersize=6, zorder=5)
    ax2.set_ylabel(r'$P_{TOT}$ (μW)', color='coral')
    ax2.tick_params(axis='y', labelcolor='coral')

    ax.text(
        0.5,
        0.92,
        rf"$f_{{\mathrm{{int}}}}=f_s/D$" "\n"
        rf"current: {f_int_current:.2f} Hz",
        transform=ax.transAxes,
        ha='right',
        va='top',
        fontsize=10,
        bbox=dict(boxstyle='round,pad=0.35', facecolor='white', edgecolor='0.7', alpha=0.9)
    )

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, fontsize=9, loc='best')

def plot_forward_outputs(ax, result):
    # Delta_G in nS (convert from μS)
    delta_G_nS = result.output.delta_G_uS * 1000
    
    # Power metrics in μW
    power_labels = [r'$P_{iDC}$', r'$P_{VCO}$', r'$P_{CNT}$', r'$P_{DIGITAL}$', r'$P_{TOT}$']
    power_values = [
        result.output.P_idc_uW,
        result.output.P_vco_uW,
        result.output.P_cnt_uW,
        result.output.P_digital_uW,
        result.output.P_tot_uW
    ]
    
    # Plot Delta_G on primary axis
    ax.bar(r'$\Delta G$', delta_G_nS, color='steelblue', alpha=0.7, width=0.4, label=r'$\Delta G$')
    ax.set_ylabel(r'$\Delta G$ (nS)', color='steelblue')
    ax.tick_params(axis='y', labelcolor='steelblue')
    
    # Create secondary axis for power metrics
    ax2 = ax.twinx()
    x_positions = np.arange(1, len(power_labels) + 1)
    ax2.bar(x_positions, power_values, color='coral', alpha=0.7, width=0.6, label='Power')
    ax2.set_xticks(np.concatenate([[0], x_positions]))
    ax2.set_xticklabels([r'$\Delta G$'] + power_labels)
    ax2.set_ylabel('Power (μW)', color='coral')
    ax2.tick_params(axis='y', labelcolor='coral')
    
    ax.set_title("Output metrics")
    ax.grid(True, axis='y', alpha=0.3)

def plot_forward_tradeoff(ax, model, result, D, variance=1, avg_window=1, reverse_result=None):
    G_uS = result.input.G_uS
    fs_Hz = result.input.fs_Hz
    max_i_dc = model.i_dc_max(result.input.G_uS)
    f_int_Hz = fs_Hz / D

    i_vals = model.params.i_dc_range
    i_vals = i_vals[i_vals <= max_i_dc] 

    deltaG_vals_uS = []
    ptot_vals = []

    for i_dc in i_vals:
        vin_mV = model.vin_from_G(G_uS, i_dc)
        deltaG_vals_uS.append(
            model.delta_G_uS(
                G_uS=G_uS,
                vin_mV=vin_mV,
                i_dc_uA=i_dc,
                f_int_Hz=f_int_Hz,
                variance=variance,
                avg_window=avg_window
            )
        )

        p_idc = model.idc_power_uW(vin_mV, i_dc, D)
        p_vco = model.pvco_from_vin(vin_mV, D)
        p_cnt = model.pcnt_from_vin(vin_mV, D)
        ptot_vals.append(p_idc + p_vco + p_cnt + result.output.P_digital_uW)

    deltaG_vals_nS = np.asarray(deltaG_vals_uS, dtype=float) * 1000  # Convert to nS
    ptot_vals = np.asarray(ptot_vals, dtype=float)

    # Plot Delta_G on primary axis (steelblue)
    ax.plot(i_vals, deltaG_vals_nS, linewidth=2.5, label=r'$\Delta G$', color='steelblue')
    ax.plot(result.input.i_dc_uA, result.output.delta_G_uS*1000, 'ko', markersize=6, zorder=5)

    ax.set_xlabel(r'$i_{dc}$ (μA)')
    ax.set_ylabel(r'$\Delta G$ (nS)', color='steelblue')
    ax.tick_params(axis='y', labelcolor='steelblue')
    ax.set_title(r'Tradeoff vs $i_{dc}$')
    ax.grid(True, alpha=0.3)

    # Plot Power on secondary axis (coral)
    ax2 = ax.twinx()
    ax2.plot(i_vals, ptot_vals,  linewidth=2.5, label=r'$P_{TOT}$', color='coral')
    ax2.plot(result.input.i_dc_uA, result.output.P_tot_uW, 'ko', markersize=6, zorder=5)
    ax2.axvline(result.input.i_dc_uA, color='black', linestyle='--', alpha=0.5, label='current $i_{dc}$')
    ax2.set_ylabel(r'$P_{TOT}$ (μW)', color='coral')
    ax2.tick_params(axis='y', labelcolor='coral')
    if reverse_result is not None and reverse_result.output.feasible:
        i_grid = reverse_result.i_dc_grid_uA
        feasible = reverse_result.feasible_mask

        if len(i_grid) == len(feasible) and np.any(feasible):
            ax.fill_between(
                i_grid,
                0,
                np.nanmax(deltaG_vals_nS[:len(i_vals)]) * 1.05,
                where=feasible[:len(i_grid)],
                alpha=0.12,
                color='green',
                label='feasible region'
            )
        i_delta_G_opt = reverse_result.output.i_dc_delta_G_opt_uA
        i_power_opt = reverse_result.output.i_dc_power_opt_uA
        dG_opt_nS = reverse_result.output.delta_G_opt_uS * 1000
        P_opt = reverse_result.output.P_tot_opt_uW

        ax.plot(i_delta_G_opt, dG_opt_nS, 'g*', markersize=8, zorder=6)
        ax2.axvline(i_delta_G_opt, color='green', linestyle='--', alpha=0.5, label='optimal delta_G $i_{dc}$')

        ax2.plot(i_power_opt, P_opt, 'b*', markersize=8, zorder=6)
        ax2.axvline(i_power_opt, color='blue', linestyle='--', alpha=0.5, label='optimal power $i_{dc}$')

    elif reverse_result is not None and not reverse_result.output.feasible:
        ax.text(
            0.03, 0.95,
            "No feasible $i_{dc}$",
            transform=ax.transAxes,
            ha='left', va='top',
            fontsize=10,
            color='crimson',
            bbox=dict(boxstyle='round', facecolor='mistyrose', alpha=0.8)
        )

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, fontsize=9, loc='best')

def plot_summary(ax, result, model, variance=1, avg_window=1,reverse_result=None):
    ax.axis("off")
    max_i_dc = model.i_dc_max(result.input.G_uS)
    min_G_uS = model.conductance(model.params.vin_min_mV, result.input.i_dc_uA)
    txt = (
        f"ΔG:        {result.output.delta_G_uS * 1000:.4f} nS\n"
        f"P_TOT:     {result.output.P_tot_uW:.4f} μW\n"
        f"ΔV: {result.intermediate.dVin_mV * 1000:.2f} μV\n"
        f"─────────────\n"
        f"i_dc range: [0, {max_i_dc:.4f}] μA"
        f"\nG range: [{min_G_uS:.4f}, +∞] μS"
        f"\nΔG range: [{result.intermediate.delta_G_range_nS[0]:.4f}, {result.intermediate.delta_G_range_nS[1]:.4f}] nS\n"
    )
    if reverse_result is not None:
        if reverse_result.output.feasible:
            txt += (
                f"i_delta_G_opt:    {reverse_result.output.i_dc_delta_G_opt_uA:.4f} μA\n"
                f"i_power_opt:    {reverse_result.output.i_dc_power_opt_uA:.4f} μA\n"
                f"ΔG_opt:      {reverse_result.output.delta_G_opt_uS * 1000:.4f} nS\n"
                f"P_opt:       {reverse_result.output.P_tot_opt_uW:.4f} μW\n"
            )
        else:
            txt += "No feasible solution\n"

    ax.text(
        0.5, 0.5, txt,
        transform=ax.transAxes,
        ha='center', va='center',
        fontsize=12,
        family='monospace',
        bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.25)
    )
    ax.set_title("Summary", fontweight='bold')


def plot_power_decomposition(ax, model, result, D=1.0):

    G_uS = result.input.G_uS
    max_i_dc = model.i_dc_max(result.input.G_uS)

    i_vals = np.asarray(model.params.i_dc_range, dtype=float)
    i_vals = i_vals[(i_vals > 0.0) & (i_vals <= max_i_dc)]

    p_tot_vals = []
    valid_i_vals = []
    for i_dc in i_vals:
        vin_mV = model.vin_from_G(G_uS, i_dc)

        p_idc = model.idc_power_uW(vin_mV, i_dc, D)
        p_vco = model.pvco_from_vin(vin_mV, D)
        p_cnt = model.pcnt_from_vin(vin_mV, D)
        p_tot_vals.append(p_idc + p_vco + p_cnt)
        valid_i_vals.append(i_dc)

    if valid_i_vals:
        ax.plot(valid_i_vals, p_tot_vals, marker='o', markersize=3, linewidth=1.5, alpha=0.75, zorder=3)
        
        # Find minimum P_tot and add shaded regions
        min_idx = np.argmin(p_tot_vals)
        min_i_dc = valid_i_vals[min_idx]
        
        # Add shaded regions separated by minimum
        ax.axvspan(min(valid_i_vals), min_i_dc, alpha=0.12, color='blue', label='P_vco + P_cnt dominated', zorder=1)
        ax.axvspan(min_i_dc, max(valid_i_vals), alpha=0.12, color='orange', label='P_idc dominated', zorder=1)
        ax.axvline(min_i_dc, color='gray', linestyle=':', linewidth=1.5, alpha=0.5, zorder=2)

    ax.set_xlabel(r'$i_{dc}$ (μA)')
    ax.set_ylabel(r'$P_{AFE}$ (μW)')
    ax.set_title(r'$P_{AFE}$ decomposition: $P_{idc}$ vs $P_{vco+cnt}$ dominance')
    ax.grid(True, alpha=0.3, zorder=0)
    ax.legend(title='region description', ncol=2)

def plot_power_breakdown_stacked(ax, model, result, D=1.0):
    """Stacked area plot showing P_idc, P_vco, P_cnt contributions vs i_dc"""
    
    G_uS = result.input.G_uS
    max_i_dc = model.i_dc_max(result.input.G_uS)

    i_vals = np.asarray(model.params.i_dc_range, dtype=float)
    i_vals = i_vals[(i_vals > 0.0) & (i_vals <= max_i_dc)]

    p_idc_vals = []
    p_vco_vals = []
    p_cnt_vals = []
    p_digital_vals = []
    valid_i_vals = []
    
    for i_dc in i_vals:
        vin_mV = model.vin_from_G(G_uS, i_dc)
        p_idc = model.idc_power_uW(vin_mV, i_dc, D)
        p_vco = model.pvco_from_vin(vin_mV, D)
        p_cnt = model.pcnt_from_vin(vin_mV, D)
        p_idc_vals.append(p_idc)
        p_vco_vals.append(p_vco)
        p_cnt_vals.append(p_cnt)
        p_digital_vals.append(result.output.P_digital_uW)
        valid_i_vals.append(i_dc)

    if valid_i_vals:
        ax.stackplot(valid_i_vals, p_idc_vals, p_vco_vals, p_cnt_vals, p_digital_vals,
                     labels=[r'$P_{idc}$', r'$P_{vco}$', r'$P_{cnt}$', r'$P_{digital}$'],
                     colors=['#1f77b4', '#2ca02c', '#ff7f0e', '#9467bd'], alpha=0.7)
        
        # Mark current operating point
        ax.plot(result.input.i_dc_uA, result.output.P_tot_uW, 'r*', markersize=15, zorder=5, label='Operating point')

    ax.set_xlabel(r'$i_{dc}$ (μA)')
    ax.set_ylabel(r'Total Power (μW)')
    ax.set_title(r'Power contributions vs $i_{dc}$ (stacked)')
    ax.grid(True, alpha=0.3, axis='y')
    ax.legend(loc='upper left', fontsize=9)


def plot_digital_power_vs_fs(ax, result, fs_min=0.1, fs_max=None, n_fs=160):
    N = result.input.N
    fs_current = result.input.fs_Hz
    if fs_max is None:
        fs_max = max(20.0, fs_current * 1.5)

    fs_vals = np.linspace(fs_min, fs_max, n_fs)
    D_compute_vals = []
    P_digital_vals = []

    for fs_Hz in fs_vals:
        D_compute, P_digital = _digital_power_from_processing_model(fs_Hz, N)
        D_compute_vals.append(D_compute)
        P_digital_vals.append(P_digital)

    P_digital_vals = np.asarray(P_digital_vals, dtype=float)
    D_compute_vals = np.asarray(D_compute_vals, dtype=float)

    ax.plot(fs_vals, P_digital_vals, linewidth=2.5, color='#9467bd', label=r'$P_{digital}$')
    ax.plot(fs_current, result.output.P_digital_uW, 'ko', markersize=6, zorder=5, label='Operating point')
    ax.axvline(fs_current, color='black', linestyle='--', alpha=0.45)

    saturated = D_compute_vals >= 1.0
    if np.any(saturated):
        ax.fill_between(
            fs_vals,
            np.nanmin(P_digital_vals),
            np.nanmax(P_digital_vals),
            where=saturated,
            color='tab:red',
            alpha=0.10,
            label=r'$D_{compute}=1$',
        )

    ax.text(
        0.04,
        0.94,
        rf"$N={N:.0f}$ samples" "\n"
        r"$P_{digital}=D_{compute}P_{active}+(1-D_{compute})P_{sleep}$",
        transform=ax.transAxes,
        ha='left',
        va='top',
        fontsize=9,
        bbox=dict(boxstyle='round,pad=0.35', facecolor='white', edgecolor='0.7', alpha=0.9),
    )

    ax.text(
        0.96,
        0.94,
        rf"$f_s={fs_Hz:.2f}$ Hz" "\n"
        r"$D_{compute}=f_s\left(a+\dfrac{b}{N}\right)$",
        transform=ax.transAxes,
        ha='right',
        va='top',
        fontsize=10,
        bbox=dict(boxstyle='round,pad=0.35', facecolor='white', edgecolor='0.7', alpha=0.9),
    )
    ax.set_xlabel(r'$f_s$ (Hz)')
    ax.set_ylabel(r'$P_{digital}$ (μW)')
    ax.set_title(r'Digital power vs $f_s$')
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=9, loc='best')


def plot_digital_power_vs_N(ax, result, N_min=1, N_max=100, n_N=100):
    fs_Hz = result.input.fs_Hz
    N_current = result.input.N

    N_vals = np.linspace(N_min, N_max, n_N)
    D_compute_vals = []
    P_digital_vals = []

    for N in N_vals:
        D_compute, P_digital = _digital_power_from_processing_model(fs_Hz, N)
        D_compute_vals.append(D_compute)
        P_digital_vals.append(P_digital)

    P_digital_vals = np.asarray(P_digital_vals, dtype=float)
    D_compute_vals = np.asarray(D_compute_vals, dtype=float)

    ax.plot(N_vals, P_digital_vals, linewidth=2.5, color='#9467bd', label=r'$P_{digital}$')
    ax.plot(N_current, result.output.P_digital_uW, 'ko', markersize=6, zorder=5, label='Operating point')
    ax.axvline(N_current, color='black', linestyle='--', alpha=0.45)

    saturated = D_compute_vals >= 1.0
    if np.any(saturated):
        ax.fill_between(
            N_vals,
            np.nanmin(P_digital_vals),
            np.nanmax(P_digital_vals),
            where=saturated,
            color='tab:red',
            alpha=0.10,
            label=r'$D_{compute}=1$',
        )

    ax.set_xlabel(r'$N$ (samples per DMA window)')
    ax.set_ylabel(r'$P_{digital}$ (μW)')
    ax.set_title(r'Digital power vs $N$')
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=9, loc='best')


DESIGN_SPACE_SPECS = {
    "delta_g": {
        "title": r"$\Delta G$",
        "ylabel": r"$\Delta G$ (nS)",
        "colorbar": r"$\Delta G$ (nS)",
        "selected_fmt": r"Selected point: $\Delta G$ = {value:.1f} nS",
        "formula": (
            r"$\Delta G = \hat{G}^{2}\cdot"
            r"\dfrac{\Delta f_{osc}}"
            r"{\Delta f_{osc}\,\hat{G}+K_{VCO|V_{in}}\,i_{dc}}$"
        ),
    },
    "delta_v": {
        "title": r"$\Delta V_{in}$",
        "ylabel": r"$\Delta V_{in}$ (mV)",
        "colorbar": r"$\Delta V_{in}$ (mV)",
        "selected_fmt": r"Selected point: $\Delta V_{{in}}$ = {value:.3f} mV",
        "formula": r"$\Delta V_{in}=\dfrac{\Delta f_{osc}}{K_{VCO|V_{in}}}$",
    },
}


def _delta_g_from_df_osc_nS(model, G_uS, i_dc_uA, vin_mV, df_osc_Hz):
    kvco_kHz_per_mV = model.kvco_kHz_per_mV(vin_mV)
    kvco_Hz_per_V = kvco_kHz_per_mV * 1e6
    delta_g_uS = np.abs(
        (df_osc_Hz * G_uS**2)
        / (kvco_Hz_per_V * i_dc_uA + df_osc_Hz * G_uS)
    )
    return delta_g_uS * 1000


def _delta_v_from_df_osc_mV(model, G_uS, i_dc_uA, vin_mV, df_osc_Hz):
    kvco_kHz_per_mV = model.kvco_kHz_per_mV(vin_mV)
    kvco_Hz_per_mV = kvco_kHz_per_mV * 1e3
    return np.abs(df_osc_Hz / kvco_Hz_per_mV)


def _design_space_metric_from_df(model, output, G_uS, i_dc_uA, vin_mV, df_osc_Hz):
    if output == "delta_g":
        return _delta_g_from_df_osc_nS(model, G_uS, i_dc_uA, vin_mV, df_osc_Hz)
    if output == "delta_v":
        return _delta_v_from_df_osc_mV(model, G_uS, i_dc_uA, vin_mV, df_osc_Hz)
    raise ValueError(f"Unsupported output '{output}'. Use 'delta_g' or 'delta_v'.")


def _df_osc_total_Hz(model, vin_mV, f_int_Hz, variance=3, avg_window=1):
    df_sampling = model.df_osc_sampling_Hz(
        f_int_Hz=f_int_Hz,
        avg_window=avg_window,
    )
    df_adev = variance * model.df_osc_adev_Hz(
        vin_mV=vin_mV,
        f_int_Hz=f_int_Hz,
    )
    return max(df_sampling, df_adev), df_sampling, df_adev


def _design_space_metric_at(model, output, G_uS, i_dc_uA, f_int_Hz, variance=3, avg_window=1):
    vin_mV = model.vin_from_G(G_uS, i_dc_uA)
    df_total, df_sampling, df_adev = _df_osc_total_Hz(
        model,
        vin_mV,
        f_int_Hz,
        variance=variance,
        avg_window=avg_window,
    )
    value = _design_space_metric_from_df(
        model,
        output,
        G_uS,
        i_dc_uA,
        vin_mV,
        df_total,
    )
    return value, df_total, df_sampling, df_adev, vin_mV


def _finite_positive(values):
    values = np.asarray(values, dtype=float)
    return values[np.isfinite(values) & (values > 0)]


def _plot_design_space_selected_point(ax, x, y, label):
    ax.scatter(
        [x],
        [y],
        color="tab:red",
        edgecolor="white",
        s=70,
        zorder=5,
        label=label,
    )


def plot_design_space_dashboard(
    fig,
    axes,
    model,
    output="delta_g",
    G_uS=5,
    i_dc_uA=1.0,
    fs_Hz=5,
    D=1.0,
    variance=3,
    avg_window=1,
    G_min=1,
    G_max=25,
    n_G=80,
    f_int_min=0.05,
    f_int_max=15.0,
    f_int_num=160,
    df_num=160,
    map_log_scale=False,
):
    if output not in DESIGN_SPACE_SPECS:
        raise ValueError(f"Unsupported output '{output}'. Use one of {list(DESIGN_SPACE_SPECS)}.")

    spec = DESIGN_SPACE_SPECS[output]
    F_INT_MIN = 0.2
    F_INT_MAX = 10.0
    OSR = fs_Hz / 2
    f_int_Hz = fs_Hz / D

    selected_value, df_total, df_sampling, df_adev, vin_mV_selected = _design_space_metric_at(
        model,
        output,
        G_uS,
        i_dc_uA,
        f_int_Hz,
        variance=variance,
        avg_window=avg_window,
    )
    selected_label = spec["selected_fmt"].format(value=float(selected_value))

    ax_df, ax_idc = axes[0, 0], axes[0, 1]
    ax_map, ax_fint = axes[1, 0], axes[1, 1]

    df_grid = np.linspace(0.1, max(df_total * 3, 10), df_num)
    output_df = _design_space_metric_from_df(
        model,
        output,
        G_uS,
        i_dc_uA,
        vin_mV_selected,
        df_grid,
    )

    ax_df.plot(df_grid, output_df, linewidth=2.5, color="#8E6AA5")
    _plot_design_space_selected_point(ax_df, df_total, selected_value, selected_label)
    ax_df.text(
        0.72,
        0.86,
        spec["formula"],
        transform=ax_df.transAxes,
        ha="right",
        va="top",
        fontsize=12,
        bbox=dict(facecolor="white", edgecolor="0.7", boxstyle="round,pad=0.35", alpha=0.9),
    )
    ax_df.set_xlabel(r"$\Delta f_{osc}$ (Hz)")
    ax_df.set_ylabel(spec["ylabel"])
    ax_df.set_title(rf"{spec['title']} vs $\Delta f_{{osc}}$")
    ax_df.grid(True, alpha=0.3)
    ax_df.legend(fontsize=9)

    max_i_dc = model.i_dc_max(G_uS)
    i_vals = np.asarray(model.params.i_dc_range, dtype=float)
    i_vals_valid = i_vals[i_vals <= max_i_dc]
    output_idc = []

    for i_dc in i_vals_valid:
        try:
            value_i, *_ = _design_space_metric_at(
                model,
                output,
                G_uS,
                i_dc,
                f_int_Hz,
                variance=variance,
                avg_window=avg_window,
            )
            output_idc.append(value_i)
        except Exception:
            output_idc.append(np.nan)

    output_idc = np.asarray(output_idc, dtype=float)
    ax_idc.plot(i_vals_valid, output_idc, linewidth=2.5, color="#8E6AA5")
    _plot_design_space_selected_point(ax_idc, i_dc_uA, selected_value, selected_label)
    ax_idc.set_xlabel(r"$i_{dc}$ ($\mu$A)")
    ax_idc.set_ylabel(spec["ylabel"])
    ax_idc.set_title(rf"{spec['title']} vs $i_{{dc}}$")
    ax_idc.grid(True, alpha=0.3)
    ax_idc.legend(fontsize=9)

    G_vals = np.linspace(G_min, G_max, n_G)
    i_grid_vals = np.asarray(model.params.i_dc_range, dtype=float)
    Z = np.full((len(G_vals), len(i_grid_vals)), np.nan)

    for gi, G in enumerate(G_vals):
        max_i = model.i_dc_max(G)
        for ii, i_dc in enumerate(i_grid_vals):
            if i_dc > max_i:
                continue
            try:
                value_grid, *_ = _design_space_metric_at(
                    model,
                    output,
                    G,
                    i_dc,
                    f_int_Hz,
                    variance=variance,
                    avg_window=avg_window,
                )
                Z[gi, ii] = value_grid
            except Exception:
                Z[gi, ii] = np.nan

    norm = None
    if map_log_scale:
        z_pos = _finite_positive(Z)
        if len(z_pos) > 0:
            norm = LogNorm(vmin=np.nanmin(z_pos), vmax=np.nanmax(z_pos))

    im = ax_map.pcolormesh(
        i_grid_vals,
        G_vals,
        Z,
        shading="auto",
        cmap="viridis",
        norm=norm,
    )
    _plot_design_space_selected_point(ax_map, i_dc_uA, G_uS, selected_label)
    ax_map.set_xlabel(r"$i_{dc}$ ($\mu$A)")
    ax_map.set_ylabel(r"$G$ ($\mu$S)")
    ax_map.set_title(rf"{spec['title']} over $(G, i_{{dc}})$")
    ax_map.legend(fontsize=9)

    cbar = fig.colorbar(im, ax=ax_map)
    cbar_label = spec["colorbar"]
    if map_log_scale:
        cbar_label += " (log scale)"
    cbar.set_label(cbar_label)

    f_int_vals = np.linspace(f_int_min, f_int_max, f_int_num)
    output_fint = []

    for f_int in f_int_vals:
        try:
            value_f, *_ = _design_space_metric_at(
                model,
                output,
                G_uS,
                i_dc_uA,
                f_int,
                variance=variance,
                avg_window=avg_window,
            )
            output_fint.append(value_f)
        except Exception:
            output_fint.append(np.nan)

    output_fint = np.asarray(output_fint, dtype=float)
    ax_fint.plot(
        f_int_vals,
        output_fint,
        linewidth=2.5,
        color="#8E6AA5",
        label=spec["title"],
    )
    _plot_design_space_selected_point(ax_fint, f_int_Hz, selected_value, selected_label)
    F_INT_QUANTIZATION = 100.0
    ax_fint.axvspan(f_int_vals[0], F_INT_MIN, alpha=0.12, color="tab:red")
    ax_fint.axvspan(F_INT_MAX, F_INT_QUANTIZATION, alpha=0.12, color="tab:red")
    ax_fint.axvline(F_INT_MIN, color="tab:red", linewidth=1.0, alpha=0.6)
    ax_fint.axvline(F_INT_MAX, color="tab:red", linewidth=1.0, alpha=0.6)


    if f_int_vals[-1] >= F_INT_QUANTIZATION:
        ax_fint.axvspan(
            max(F_INT_QUANTIZATION, f_int_vals[0]),
            f_int_vals[-1],
            alpha=0.10,
            color="tab:blue",
        )
        ax_fint.axvline(
            F_INT_QUANTIZATION,
            color="tab:blue",
            linewidth=1.0,
            alpha=0.7,
        )
    ax_fint.text(
        0.9,
        0.5,
        r"$f_{\mathrm{int}} = \dfrac{2\,OSR}{D}$",
        transform=ax_fint.transAxes,
        ha="right",
        va="top",
        fontsize=12,
        bbox=dict(facecolor="white", edgecolor="0.7", boxstyle="round,pad=0.35", alpha=0.9),
    )
    ax_fint.set_xlabel(r"$f_{\mathrm{int}}$ (Hz)")
    ax_fint.set_ylabel(spec["ylabel"])
    ax_fint.set_title(rf"{spec['title']} vs $f_{{\mathrm{{int}}}}$")
    ax_fint.grid(True, alpha=0.3)
    ax_fint.legend(
        handles=[
            Line2D(
                [0],
                [0],
                color="#8E6AA5",
                linewidth=2.5,
                label=spec["title"],
            ),
            Patch(
                facecolor="tab:red",
                alpha=0.12,
                edgecolor="tab:red",
                label="Allan Dev extrapolated region",
            ),
            Patch(
                facecolor="tab:blue",
                alpha=0.10,
                edgecolor="tab:blue",
                label="Counter quantization-error dominated region",
            ),
        ],
        fontsize=9,
        loc="best",
    )

    fig.suptitle(
        rf"{spec['title']} design-space exploration "
        rf"($G={G_uS}$ $\mu$S, $i_{{dc}}={i_dc_uA}$ $\mu$A, "
        rf"$OSR={OSR:g}$, $D={D:g}$)",
        fontsize=14,
        fontweight="bold",
    )

    return {
        "output": output,
        "selected_value": selected_value,
        "selected_label": selected_label,
        "vin_mV_selected": vin_mV_selected,
        "df_grid": df_grid,
        "output_df": output_df,
        "df_sampling": df_sampling,
        "df_adev": df_adev,
        "df_total": df_total,
        "i_vals": i_vals_valid,
        "output_idc": output_idc,
        "G_vals": G_vals,
        "i_grid_vals": i_grid_vals,
        "output_map": Z,
        "f_int_vals": f_int_vals,
        "output_fint": output_fint,
    }
