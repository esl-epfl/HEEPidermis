#!/usr/bin/env python3
"""
Compare ground-truth FE output against simulation FE output.

Ground truth (gt_fe_output.csv) — produced by gsr_eval native build:
  Header:  # signal_length=N line_start=500000 line_end=600000 sample_step=S units=nS
  Columns: idx, signal_nS, tonic, phasic
  idx 0 = conductance.txt line 500000 (1-indexed, as per resistor.sv line_start)
  sample_step defaults to 1 for older GT CSVs that do not include it.

Simulation (sim_fe_output.csv) — produced by test_reconstruction sim:
  Header:  # SAMPLING_FREQ=F GT_LINE_START=L GT_CHANGE_RATE_HZ=R
  Columns: k, g_nS, tonic, phasic
  Sample k maps to GT line: GT_LINE_START + k * (GT_CHANGE_RATE_HZ / (SAMPLING_FREQ * 100))

Usage:
  python3 compare.py gt_fe_output.csv sim_fe_output.csv [--plot] [--save-plot[=PATH]]
  --gt-skip N        skip the first N GT samples (shifts GT window forward to
                     compensate for sim init overhead); default 0
  --trim N           drop the last N sim samples before computing NRMSE/plot
                     (removes end-of-signal boundary artefacts); default 0
  --align MODE       nominal, fit, or auto. auto keeps nominal alignment unless
                     a fitted effective GT step is much better; default auto
  --gt-step X        override the GT rows per sim sample directly
  --save-plot[=PATH] save the plot to PATH (default: compare_plot.png);
                     implies --plot
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np

# ------------------------------------------------------------------ #
def parse_header(path):
    """Return dict of key=value pairs from the first # comment line."""
    params = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('#'):
                for token in line[1:].split():
                    if '=' in token:
                        k, v = token.split('=', 1)
                        try:
                            params[k] = int(v)
                        except ValueError:
                            params[k] = v
            elif line and not line.startswith('#'):
                break
    return params

def load_csv(path):
    """Load CSV, skipping the # metadata line and the column-names row."""
    return np.atleast_2d(np.loadtxt(path, delimiter=',', comments='#', skiprows=2, dtype=int))

def nrmse(predicted, reference):
    """NRMSE = RMSE / (max(ref) - min(ref)); returns inf if range is 0."""
    rmse = np.sqrt(np.mean((predicted.astype(float) - reference.astype(float)) ** 2))
    span = float(np.max(reference)) - float(np.min(reference))
    return rmse / span if span != 0.0 else float('inf')

def nrmse_db(nr):
    """Convert NRMSE ratio to dB using 20*log10(NRMSE).

    Lower/more negative is better. Returns -inf for perfect zero error.
    """
    if nr == 0:
        return float('-inf')
    if not np.isfinite(nr):
        return float('inf')
    return 20.0 * np.log10(nr)

def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare ground-truth FE output against simulation FE output."
    )
    parser.add_argument("gt_path")
    parser.add_argument("sim_path")
    parser.add_argument("--plot", action="store_true")
    parser.add_argument(
        "--save-plot",
        nargs="?",
        const="compare_plot.png",
        default=None,
        metavar="PATH",
        help="Save the plot to PATH; default PATH is compare_plot.png.",
    )
    parser.add_argument(
        "--gt-skip",
        type=float,
        default=0.0,
        help="Shift the GT window forward by N GT samples.",
    )
    parser.add_argument(
        "--trim",
        type=int,
        default=0,
        help="Drop the last N sim samples before computing metrics.",
    )
    parser.add_argument(
        "--sampling-freq",
        type=float,
        default=None,
        help="Override SAMPLING_FREQ when the sim CSV header does not contain it.",
    )
    parser.add_argument(
        "--gt-change-rate-hz",
        type=float,
        default=None,
        help="Override GT_CHANGE_RATE_HZ when the sim CSV header does not contain it.",
    )
    parser.add_argument(
        "--sim-rate-multiplier",
        type=float,
        default=100.0,
        help="Simulation refresh multiplier used in TARGET_SIM; default 100.",
    )
    parser.add_argument(
        "--gt-step",
        type=float,
        default=None,
        help="Override GT rows per sim sample directly.",
    )
    parser.add_argument(
        "--align",
        choices=("auto", "nominal", "fit"),
        default="auto",
        help="Use nominal metadata alignment, fitted effective step, or auto.",
    )
    parser.add_argument(
        "--fit-offset",
        action="store_true",
        help="Allow fitted alignment to adjust GT offset as well as GT step.",
    )
    return parser.parse_args()

def parse_number(value, default):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

def infer_sampling_freq(sim_path, sim_params, override):
    if override is not None:
        return override

    header_value = parse_number(sim_params.get('SAMPLING_FREQ'), None)
    if header_value is not None:
        return header_value

    match = re.search(r'(\d+(?:\.\d+)?)\s*Hz', Path(sim_path).name, re.IGNORECASE)
    if match:
        return float(match.group(1))

    return 10.0

def interpolate_rows(data, indices, columns):
    """Linearly interpolate data columns at possibly fractional FE indices."""
    x = data[:, 0].astype(float)
    values = []
    for col in columns:
        values.append(np.interp(indices, x, data[:, col].astype(float)))
    return values

def rmse_for_alignment(gt_data, sim_data, gt_offset, gt_step):
    sim_k = sim_data[:, 0].astype(float)
    gt_indices = gt_offset + sim_k * gt_step
    gt_x = gt_data[:, 0].astype(float)
    if gt_indices[0] < gt_x[0] or gt_indices[-1] > gt_x[-1]:
        return float('inf')

    gt_signal, = interpolate_rows(gt_data, gt_indices, [1])
    sim_signal = sim_data[:, 1].astype(float)
    return float(np.sqrt(np.mean((sim_signal - gt_signal) ** 2)))

def fit_alignment(gt_data, sim_data, nominal_offset, nominal_step, fit_offset=False):
    """Fit an effective GT step. Offset stays fixed unless requested."""
    sim_k = sim_data[:, 0].astype(float)
    sim_signal = sim_data[:, 1].astype(float)
    gt_x = gt_data[:, 0].astype(float)
    gt_signal = gt_data[:, 1].astype(float)

    if len(sim_data) < 4 or sim_k[-1] == sim_k[0]:
        return nominal_step, nominal_offset, float('inf')

    sim_peak_pos = int(np.argmax(sim_signal))
    gt_peak_pos = int(np.argmax(gt_signal))
    step_guesses = [nominal_step]
    if sim_k[sim_peak_pos] != 0:
        peak_step = (gt_x[gt_peak_pos] - nominal_offset) / sim_k[sim_peak_pos]
        if peak_step > 0:
            step_guesses.append(float(peak_step))

    best_rmse = float('inf')
    best_step = nominal_step
    best_offset = nominal_offset

    def update_best(step, offset):
        nonlocal best_rmse, best_step, best_offset
        rmse = rmse_for_alignment(gt_data, sim_data, offset, step)
        if rmse < best_rmse:
            best_rmse = rmse
            best_step = float(step)
            best_offset = float(offset)

    def offset_candidates(step):
        if not fit_offset:
            return np.array([nominal_offset], dtype=float)

        width = max(10.0, abs(step) * 5.0)
        centers = [nominal_offset]
        centers.append(gt_x[gt_peak_pos] - sim_k[sim_peak_pos] * step)
        offsets = []
        for center in centers:
            offsets.extend(np.linspace(center - width, center + width, 41))
        return np.array(offsets, dtype=float)

    for guess in step_guesses:
        if guess <= 0:
            continue
        lower = max(1e-9, guess * 0.5)
        upper = guess * 1.5
        for step in np.linspace(lower, upper, 121):
            for offset in offset_candidates(step):
                update_best(step, offset)

    if not np.isfinite(best_rmse):
        return nominal_step, nominal_offset, best_rmse

    fine_step_width = max(best_step * 0.02, 1e-9)
    fine_steps = np.linspace(best_step - fine_step_width, best_step + fine_step_width, 81)
    if fit_offset:
        fine_offset_width = max(2.0, abs(best_step))
        fine_offsets = np.linspace(best_offset - fine_offset_width,
                                   best_offset + fine_offset_width,
                                   81)
    else:
        fine_offsets = np.array([nominal_offset], dtype=float)

    for step in fine_steps:
        if step <= 0:
            continue
        for offset in fine_offsets:
            update_best(step, offset)

    return best_step, best_offset, best_rmse

# ------------------------------------------------------------------ #
def main():
    args = parse_args()

    gt_path  = args.gt_path
    sim_path = args.sim_path
    do_plot  = args.plot or args.save_plot is not None
    save_path = args.save_plot
    gt_skip = args.gt_skip
    trim = args.trim

    # ---- Load files ------------------------------------------------
    gt_params  = parse_header(gt_path)
    sim_params = parse_header(sim_path)

    gt_data  = load_csv(gt_path)   # columns: idx, signal_nS, tonic, phasic
    sim_data = load_csv(sim_path)  # columns: k,   g_nS,      tonic, phasic

    # ---- Parse simulation parameters --------------------------------
    sampling_freq      = infer_sampling_freq(sim_path, sim_params, args.sampling_freq)
    gt_line_start      = parse_number(sim_params.get('GT_LINE_START'), None)
    if gt_line_start is None:
        gt_line_start = parse_number(gt_params.get('line_start'), 500000)
    gt_change_rate_hz  = args.gt_change_rate_hz
    if gt_change_rate_hz is None:
        gt_change_rate_hz = parse_number(sim_params.get('GT_CHANGE_RATE_HZ'), 20000)

    gt_sample_step = parse_number(gt_params.get('sample_step'), 1)
    if gt_sample_step <= 0:
        print("ERROR: GT sample_step must be > 0.")
        sys.exit(1)

    # Raw conductance.txt rows per simulation sample:
    #   sim fires at SAMPLING_FREQ * 100 Hz in simulation;
    #   conductance.txt updates at GT_CHANGE_RATE_HZ in simulation.
    sim_rate_sim_hz    = sampling_freq * args.sim_rate_multiplier
    nominal_raw_gt_step = gt_change_rate_hz / sim_rate_sim_hz
    # GT FE rows per simulation sample. For a high-rate GT FE CSV this is the
    # raw step. For a pre-FE downsampled GT CSV, divide by the GT sample step.
    nominal_gt_step    = nominal_raw_gt_step / gt_sample_step
    if args.gt_step is not None:
        nominal_gt_step = args.gt_step
        if args.align == "auto":
            args.align = "nominal"

    print(f"SAMPLING_FREQ          : {sampling_freq} Hz")
    print(f"GT_LINE_START          : {gt_line_start}")
    print(f"GT sample step         : {gt_sample_step:.6g} raw rows/GT FE row")
    print(f"Raw GT step/sample     : {nominal_raw_gt_step:.6g} raw rows/sim sample")
    print(f"Nominal GT step/sample : {nominal_gt_step:.6g} GT FE rows/sim sample")
    print(f"GT skip                : {gt_skip} GT FE rows")
    print(f"Trim (end)             : {trim} samples")
    print(f"Sim samples            : {len(sim_data)}")
    print()

    if trim < 0:
        print("ERROR: --trim must be >= 0.")
        sys.exit(1)
    if trim > 0:
        if trim >= len(sim_data):
            print("ERROR: --trim removes all sim samples.")
            sys.exit(1)
        sim_data = sim_data[:-trim]

    # ---- Map each sim sample k to a GT index -----------------------
    # GT FE idx 0 == the line_start recorded in the GT CSV.
    # GT FE idx j == GT line (gt_origin_line + j * gt_sample_step).
    # Sim sample k == GT line (gt_line_start + k * gt_step)
    #   => GT FE index = (gt_line_start - gt_origin_line) / gt_sample_step
    #                    + k * (gt_step / gt_sample_step)
    gt_origin_line = parse_number(gt_params.get('line_start'), 500000)
    gt_fe_offset = (gt_line_start - gt_origin_line) / gt_sample_step + gt_skip

    sim_k        = sim_data[:, 0]
    nominal_rmse = rmse_for_alignment(gt_data, sim_data, gt_fe_offset, nominal_gt_step)
    fitted_gt_step, fitted_offset, fitted_rmse = fit_alignment(
        gt_data,
        sim_data,
        gt_fe_offset,
        nominal_gt_step,
        fit_offset=args.fit_offset,
    )

    gt_step = nominal_gt_step
    gt_offset = gt_fe_offset
    align_used = "nominal"
    if args.align == "fit":
        gt_step = fitted_gt_step
        gt_offset = fitted_offset
        align_used = "fit"
    elif args.align == "auto" and np.isfinite(fitted_rmse):
        step_delta = abs(fitted_gt_step - nominal_gt_step) / max(abs(nominal_gt_step), 1e-12)
        improves = fitted_rmse < (nominal_rmse * 0.75)
        if step_delta > 0.05 and improves:
            gt_step = fitted_gt_step
            gt_offset = fitted_offset
            align_used = "auto-fit"

    print(f"Alignment              : {align_used}")
    print(f"Effective GT step      : {gt_step:.6g}")
    if args.fit_offset:
        print(f"Effective GT offset    : {gt_offset:.6g}")
    if np.isfinite(nominal_rmse) and np.isfinite(fitted_rmse):
        print(f"Signal RMSE nominal/fit: {nominal_rmse:.2f} / {fitted_rmse:.2f}")
    if align_used == "auto-fit":
        print("WARNING: effective GT step differs from nominal. The sim output is not one CSV row per nominal refresh.")
    print()

    gt_indices = gt_offset + sim_k.astype(float) * gt_step

    # Bounds check
    gt_x = gt_data[:, 0].astype(float)
    valid_mask = (gt_indices >= gt_x[0]) & (gt_indices <= gt_x[-1])
    if not np.all(valid_mask):
        n_bad = np.sum(~valid_mask)
        print(f"WARNING: {n_bad} sim sample(s) fall outside GT window — skipping them.")
    gt_indices = gt_indices[valid_mask]
    sim_data   = sim_data[valid_mask]

    n = len(sim_data)
    if n == 0:
        print("ERROR: no overlapping samples after GT alignment. Check parameters.")
        sys.exit(1)

    # ---- Extract paired values ------------------------------------
    gt_signal, gt_tonic, gt_phasic = interpolate_rows(gt_data, gt_indices, [1, 2, 3])

    sim_signal = sim_data[:, 1].astype(float)
    sim_tonic  = sim_data[:, 2].astype(float)
    sim_phasic = sim_data[:, 3].astype(float)

    # ---- NRMSE -----------------------------------------------------
    print(f"{'Metric':<20} {'NRMSE':>8}     {'NRMSE_db'} {'RMSE':>12}  {'GT range':>12}")
    print("-" * 60)
    for label, sim_v, gt_v in [
        ("Signal (g_nS)", sim_signal, gt_signal),
        ("Tonic",         sim_tonic,  gt_tonic),
        ("Phasic",        sim_phasic, gt_phasic),
    ]:
        rmse = np.sqrt(np.mean((sim_v - gt_v) ** 2))
        span = float(np.max(gt_v)) - float(np.min(gt_v))
        nr   = rmse / span if span != 0 else float('inf')
        nr_db = nrmse_db(nr)
        print(f"{label:<20} {nr:>8.4f} {nr_db:>10.2f} {rmse:>12.2f}  {span:>12.2f}")

    # ---- Optional plot ---------------------------------------------
    if do_plot:
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            print("\nmatplotlib not available — skipping plot.")
            return

        fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
        k_axis = sim_data[:, 0]

        for ax, (label, sim_v, gt_v) in zip(axes, [
            ("Signal (nS)", sim_signal, gt_signal),
            ("Tonic",       sim_tonic,  gt_tonic),
            ("Phasic",      sim_phasic, gt_phasic),
        ]):
            ax.plot(k_axis, gt_v,  label='GT',  linewidth=1.5)
            ax.plot(k_axis, sim_v, label='Sim', linewidth=1.5, linestyle='--')
            ax.set_ylabel(label)
            ax.legend(loc='upper right')
            ax.grid(True, alpha=0.3)

        axes[-1].set_xlabel(f'Sim sample k  (SAMPLING_FREQ={sampling_freq} Hz)')
        fig.suptitle(f'GT vs Simulation FE  |  SAMPLING_FREQ={sampling_freq} Hz')
        plt.tight_layout()
        if save_path:
            fig.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"\nPlot saved to: {save_path}")
        plt.show()


if __name__ == '__main__':
    main()
