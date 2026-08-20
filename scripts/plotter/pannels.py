# Copyright 2026 EPFL contributors
# SPDX-License-Identifier: Apache-2.0
#
# File: pannels.py
# Author: Ismail Essaidi
# Date: 08/04/2026
# Description: Matplotlib visualization panels for VCO model exploration

import numpy as np

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
        f"f_s:      {result.input.fs_Hz:.0f} Hz\n"
        f"D:        {result.input.D * 100:.0f}%\n\n"
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

def plot_forward_outputs(ax, result):
    # Delta_G in nS (convert from μS)
    delta_G_nS = result.output.delta_G_uS * 1000
    
    # Power metrics in μW
    power_labels = [r'$P_{iDC}$', r'$P_{VCO}$', r'$P_{CNT}$', r'$P_{TOT}$']
    power_values = [
        result.output.P_idc_uW,
        result.output.P_vco_uW,
        result.output.P_cnt_uW,
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
        ptot_vals.append(p_idc + p_vco + p_cnt)

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
        f"─────────────\n"
        f"i_dc range: [0, {max_i_dc:.4f}] μA"
        f"\nG range: [{min_G_uS:.4f}, +∞] μS"
        f"\nΔG range: [{result.intermediate.delta_G_range_nS[0]:.4f}, {result.intermediate.delta_G_range_nS[1]:.4f}] nS"
    )
    if reverse_result is not None:
        txt += f"\n─────────────\nOptimal i_dc search...\n"
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
    ax.set_ylabel(r'$P_{tot}$ (μW)')
    ax.set_title(r'region breakdown: $P_{vco+cnt}$ vs $P_{idc}$ dominance')
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
    valid_i_vals = []
    
    for i_dc in i_vals:
        vin_mV = model.vin_from_G(G_uS, i_dc)
        p_idc = model.idc_power_uW(vin_mV, i_dc, D)
        p_vco = model.pvco_from_vin(vin_mV, D)
        p_cnt = model.pcnt_from_vin(vin_mV, D)
        p_idc_vals.append(p_idc)
        p_vco_vals.append(p_vco)
        p_cnt_vals.append(p_cnt)
        valid_i_vals.append(i_dc)

    if valid_i_vals:
        ax.stackplot(valid_i_vals, p_idc_vals, p_vco_vals, p_cnt_vals,
                     labels=[r'$P_{idc}$', r'$P_{vco}$', r'$P_{cnt}$'],
                     colors=['#1f77b4', '#2ca02c', '#ff7f0e'], alpha=0.7)
        
        # Mark current operating point
        vin_current = model.vin_from_G(G_uS, result.input.i_dc_uA)
        p_idc_current = model.idc_power_uW(vin_current, result.input.i_dc_uA, D)
        p_vco_current = model.pvco_from_vin(vin_current, D)
        p_cnt_current = model.pcnt_from_vin(vin_current, D)
        p_tot_current = p_idc_current + p_vco_current + p_cnt_current
        
        ax.plot(result.input.i_dc_uA, p_tot_current, 'r*', markersize=15, zorder=5, label='Operating point')

    ax.set_xlabel(r'$i_{dc}$ (μA)')
    ax.set_ylabel(r'Power (μW)')
    ax.set_title(r'Power contributions vs $i_{dc}$ (stacked)')
    ax.grid(True, alpha=0.3, axis='y')
    ax.legend(loc='upper left', fontsize=9)