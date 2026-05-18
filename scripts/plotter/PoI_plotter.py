# Copyright 2026 EPFL contributors
# SPDX-License-Identifier: Apache-2.0
#
# File: PoI_plotter.py
# Author: Ismail Essaidi
# Date: 08/04/2026
# Description: Interactive VCO model plotter with forward/reverse optimization

from ipywidgets import FloatSlider, VBox, HBox, Layout, HTML, interactive_output, ToggleButton
from IPython.display import display
import matplotlib.pyplot as plt
from pannels import *
from workflow import *

def PoI_plotter(model, variance=1, avg_window=1):
    G_init = 20.0
    G_slider = FloatSlider(
        value=G_init, min=0.5, max=25.0, step=0.5,
        description='G (μS):',
        continuous_update=False,
        layout=Layout(width='300px')
    )

    i_dc_slider = FloatSlider(
        value=1.0, min=0.1, max=model.i_dc_max(G_init), step=0.04,
        description='i_dc (μA):',
        continuous_update=False,
        layout=Layout(width='300px')
    )

    fs_slider = FloatSlider(
        value=1.0, min=0.5, max=20.0, step=0.5,
        description='fs (Hz):',
        continuous_update=False,
        layout=Layout(width='300px')
    )

    delta_G_slider = FloatSlider(
        value=0.10, min=0.0, max=1.0, step=0.01,
        description='ΔG tgt (nS):',
        continuous_update=False,
        layout=Layout(width='300px')
    )

    P_tot_max_slider = FloatSlider(
        value=10.0, min=0.1, max=20.0, step=0.1,
        description='Pmax (μW):',
        continuous_update=False,
        layout=Layout(width='300px')
    )

    variance_on = ToggleButton(
        description='avar OFF',
        value=False,
        layout=Layout(width='80px', height='30px')
    )

    # Cache for expensive computations
    _computation_cache = {
        'last_G': G_init,
        'last_fs': 1.0,
        'last_variance': False
    }

    def update_variance_description(change):
        variance_on.description = 'avar ON' if change['new'] else 'avar OFF'

    variance_on.observe(update_variance_description, names='value')

    def update_i_dc_max(change):
        """Only update i_dc max when G changes"""
        G_value = change['new']
        max_i_dc = model.i_dc_max(G_value)
        i_dc_slider.max = max_i_dc
        if i_dc_slider.value > max_i_dc:
            i_dc_slider.value = max_i_dc

    def update_delta_G_range(change=None):
        """Update ΔG slider range when G, fs, or variance changes"""
        G_value = G_slider.value
        fs_value = fs_slider.value
        active_variance = variance if variance_on.value else 0

        # Skip redundant computations
        if (_computation_cache['last_G'] == G_value and 
            _computation_cache['last_fs'] == fs_value and 
            _computation_cache['last_variance'] == variance_on.value):
            return

        _computation_cache['last_G'] = G_value
        _computation_cache['last_fs'] = fs_value
        _computation_cache['last_variance'] = variance_on.value

        min_deltaG, max_deltaG = model.compute_delta_G_range_nS(
            G_value,
            fs_value,
            variance=active_variance,
            avg_window=avg_window
        )
        print(f"Computed ΔG range: [{min_deltaG:.4f}, {max_deltaG:.4f}] nS")

        if np.isfinite(min_deltaG) and np.isfinite(max_deltaG):
            delta_G_slider.max = max_deltaG * 1.1
            delta_G_slider.min = max(0.0, min_deltaG * 0.9)

            if delta_G_slider.value > delta_G_slider.max:
                delta_G_slider.value = delta_G_slider.max
            elif delta_G_slider.value < delta_G_slider.min:
                delta_G_slider.value = delta_G_slider.min

    def on_G_change(change):
        """Single consolidated callback for G slider changes"""
        update_i_dc_max(change)
        update_delta_G_range(change)

    def on_control_change(change):
        """Callback for fs and variance changes"""
        update_delta_G_range(change)

    # Single observer per slider to avoid redundant callbacks
    G_slider.observe(on_G_change, names='value')
    fs_slider.observe(on_control_change, names='value')
    variance_on.observe(on_control_change, names='value')

    forward_controls = VBox(
        [
            HTML("<h4>Forward design controls</h4>"),
            G_slider,
            i_dc_slider,
            fs_slider,
            HBox([variance_on], layout=Layout(justify_content='center', width='320px')),

        ],
        layout=Layout(
            width='320px',
            padding='10px',
            border='1px solid #ddd'
        )
    )

    reverse_controls = VBox(
        [
            HTML("<h4>Target variables</h4>"),
            delta_G_slider,
            P_tot_max_slider,
        ],
        layout=Layout(
            width='320px',
            padding='10px',
            border='1px solid #ddd'
        )
    )

    controls_wrapper = VBox(
        [forward_controls, reverse_controls],
        layout=Layout(
            width='340px',
            min_width='340px',
            height='950px',
            justify_content='center'
        )
    )

    def _plot(G_uS, i_dc_uA, fs_Hz, delta_G_target_nS, P_tot_max_uW, variance_enabled):
        active_variance = variance if variance_enabled else 0

        fwd_in = forward_input(G_uS=G_uS, i_dc_uA=i_dc_uA, fs_Hz=fs_Hz)
        result = forward_compute(
            model=model,
            input=fwd_in,
            variance=active_variance,
            avg_window=avg_window
        )

        rev_in = reverse_input(
            G_uS=G_uS,
            fs_Hz=fs_Hz,
            delta_G_target_nS=delta_G_target_nS,
            P_tot_max_uW=P_tot_max_uW
        )
        reverse_result = reverse_compute(
            model=model,
            input=rev_in,
            variance=active_variance,
            avg_window=avg_window
        )

        fig = plt.figure(figsize=(13, 10), constrained_layout=True)
        gs = fig.add_gridspec(3, 2)

        plot_forward_vco_point(fig.add_subplot(gs[0, 0]), model, result)
        plot_forward_summary(fig.add_subplot(gs[0, 1]), result, model)
        plot_forward_df_components(fig.add_subplot(gs[1, 0]), result)
        plot_forward_outputs(fig.add_subplot(gs[1, 1]), result)
        plot_forward_tradeoff(
            fig.add_subplot(gs[2, 0]),
            model,
            result,
            variance=active_variance,
            avg_window=avg_window,
            reverse_result=reverse_result
        )
        plot_summary(
            fig.add_subplot(gs[2, 1]),
            result,
            model,
            reverse_result=reverse_result
        )

        fig.suptitle(
            r'Forward/reverse workflow: $(G, i_{dc}, f_s)\leftrightarrow(\Delta G, P)$',
            fontsize=14,
            fontweight='bold'
        )
        plt.show()
        plt.close(fig)

    ui = interactive_output(
        _plot,
        {
            'G_uS': G_slider,
            'i_dc_uA': i_dc_slider,
            'fs_Hz': fs_slider,
            'delta_G_target_nS': delta_G_slider,
            'P_tot_max_uW': P_tot_max_slider,
            'variance_enabled': variance_on
        }
    )

    display(HBox([controls_wrapper, ui], layout=Layout(align_items='center')))