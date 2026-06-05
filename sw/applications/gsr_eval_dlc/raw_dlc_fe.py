#!/usr/bin/env python3
"""
Feature extraction directly on raw dLC events.

This script keeps the default FE input in the raw event domain:

    packed UART bytes -> (dt_ticks, dlvl) -> C FE on dlvl

No cumulative amplitude/conductance reconstruction is needed before feature
extraction. Post-FE conductance reconstruction is optional and intended only
for explicit comparison/debug exports.
"""

import argparse
import csv
import io
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np


_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_FEA_BINARY = _SCRIPT_DIR / "raw_dlc_fe_native"
_DEFAULT_GSR_EVAL_DIR = _SCRIPT_DIR.parent / "gsr_eval"
_DEFAULT_METHOD = "delta"

# VCO calibration tables from VCO_sdk.c.
_TABLE_VIN_UV = [
    330000, 340000, 360000, 380000, 400000,
    420000, 440000, 460000, 480000, 500000,
    520000, 540000, 560000, 580000, 600000,
    620000, 640000, 660000, 680000, 700000,
    720000, 740000, 760000, 780000, 800000,
]
_TABLE_FOSC_HZ = [
    24000, 26130, 31330, 37320, 45270,
    55150, 67270, 82680, 99870, 121190,
    146020, 175270, 208990, 247770, 291780,
    341260, 396650, 457900, 525140, 598560,
    677660, 762750, 853760, 950200, 1051710,
]
_VDD_UV = 800000
_VCO_PHASES = 62
_IDAC_LSB_NA = 40


def _parse_scalar(text, default=None):
    if text is None:
        return default
    try:
        value = float(text)
    except (TypeError, ValueError):
        return default
    if value.is_integer():
        return int(value)
    return value


def _parse_kv_line(line):
    params = {}
    for token in line.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        params[key] = value
    return params


def _parse_hash_header(path):
    params = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#"):
                params.update(_parse_kv_line(line[1:]))
            elif line:
                break
    return params


def _interp_vin_uV(freq_hz):
    if freq_hz <= _TABLE_FOSC_HZ[0]:
        return float(_TABLE_VIN_UV[0])
    if freq_hz >= _TABLE_FOSC_HZ[-1]:
        return float(_TABLE_VIN_UV[-1])

    hi = int(np.searchsorted(_TABLE_FOSC_HZ, freq_hz, side="left"))
    lo = hi - 1
    x0, x1 = _TABLE_FOSC_HZ[lo], _TABLE_FOSC_HZ[hi]
    y0, y1 = _TABLE_VIN_UV[lo], _TABLE_VIN_UV[hi]
    return y0 + (y1 - y0) * (freq_hz - x0) / (x1 - x0)


def _level_to_conductance_nS(level, level_width, vco_fs_hz, idac_nA):
    freq_hz = level * level_width * vco_fs_hz / _VCO_PHASES
    vin_uV = _interp_vin_uV(freq_hz)
    dv_uV = _VDD_UV - vin_uV
    return (idac_nA * 1e6 / dv_uV) if dv_uV > 0 else 0.0


def levels_to_conductance_nS(levels, level_width, vco_fs_hz, idac_nA):
    return np.array([
        _level_to_conductance_nS(level, level_width, vco_fs_hz, idac_nA)
        for level in levels
    ], dtype=float)


def read_event_bytes(path):
    event_bytes = []

    with open(path) as f:
        ses_cfg = f.readline().strip()
        dlc_cfg = f.readline().strip()

        for line in f:
            parts = line.strip().split()
            if not parts:
                continue

            if parts[0] == "HEX":
                text = "".join(parts[1:])
                if len(text) % 2 != 0:
                    raise ValueError(f"Odd number of hex digits: {line.rstrip()}")
                for i in range(0, len(text), 2):
                    event_bytes.append(int(text[i:i + 2], 16))
                continue

            if len(parts) >= 2:
                val = int(parts[1], 0)
                event_bytes.append(val & 0xFF)
                event_bytes.append((val >> 8) & 0xFF)

    return ses_cfg, dlc_cfg, event_bytes


def decode_raw_dlc(path, include_level=False):
    ses_cfg, dlc_cfg, event_bytes = read_event_bytes(path)
    params = _parse_kv_line(dlc_cfg)

    sample_rate_hz = float(params["SAMPLE_RATE_HZ"])
    level_width = 1 << int(params["LOG_LEVEL_WIDTH"])
    initial_level = int(params.get("INITIAL_LEVEL", 0))
    idac_code = int(params.get("IDAC_CODE", 7))
    vco_fs_hz = float(params["VCO_FS_HZ"])
    dlvl_bits = int(params.get("DLVL_BITS", 2))
    dt_bits = int(params.get("DT_BITS", 8 - dlvl_bits))
    sign_magnitude = params.get("FORMAT", "sign_magnitude") == "sign_magnitude"
    if dlvl_bits <= 0 or dt_bits <= 0 or dlvl_bits + dt_bits > 8:
        raise ValueError(f"Invalid dLC bit layout: DLVL_BITS={dlvl_bits} DT_BITS={dt_bits}")
    if sign_magnitude and dlvl_bits < 2:
        raise ValueError("Sign-magnitude dLC needs at least one sign bit and one magnitude bit")

    dlvl_mask = (1 << dlvl_bits) - 1
    dt_mask = (1 << dt_bits) - 1

    current_tick = 0
    current_level = float(initial_level) if include_level else None

    dt_ticks = []
    ticks = []
    times_s = []
    dlvl = []
    levels = []

    for byte in event_bytes:
        raw_dlvl = byte & dlvl_mask
        if sign_magnitude:
            sign_bit = raw_dlvl >> (dlvl_bits - 1)
            magnitude = raw_dlvl & ((1 << (dlvl_bits - 1)) - 1)
            delta_level = -magnitude if sign_bit else magnitude
        else:
            sign_bit = 1 << (dlvl_bits - 1)
            delta_level = raw_dlvl - (1 << dlvl_bits) if raw_dlvl & sign_bit else raw_dlvl
        if include_level:
            current_level += delta_level

        delta_ticks = (byte >> dlvl_bits) & dt_mask
        if delta_ticks == 0:
            if dlvl:
                dlvl[-1] += delta_level
                if include_level:
                    levels[-1] = current_level
            continue

        current_tick += delta_ticks
        dt_ticks.append(delta_ticks)
        ticks.append(current_tick)
        times_s.append(current_tick / sample_rate_hz)
        dlvl.append(delta_level)
        if include_level:
            levels.append(current_level)

    if not dlvl:
        raise ValueError("No raw dLC events found after decoding")

    decoded = {
        "ses_cfg": ses_cfg,
        "dlc_cfg": dlc_cfg,
        "params": params,
        "sample_rate_hz": sample_rate_hz,
        "level_width": level_width,
        "initial_level": initial_level,
        "idac_nA": idac_code * _IDAC_LSB_NA,
        "vco_fs_hz": vco_fs_hz,
        "dt_ticks": np.asarray(dt_ticks, dtype=np.int64),
        "ticks": np.asarray(ticks, dtype=np.int64),
        "time_s": np.asarray(times_s, dtype=float),
        "dlvl": np.asarray(dlvl, dtype=float),
    }
    if include_level:
        decoded["level"] = np.asarray(levels, dtype=float)

    return decoded


def parse_crop_events(value, event_count):
    text = str(value).strip().lower()
    if text == "auto":
        return min(50, max(0, (event_count - 3) // 4))
    crop = int(value)
    if crop < 0:
        raise ValueError("--crop-events must be >= 0 or 'auto'")
    return crop


def crop_events(decoded, crop_events):
    n = len(decoded["dlvl"])
    crop_n = parse_crop_events(crop_events, n)
    if crop_n == 0:
        return decoded, 0
    if 2 * crop_n >= n:
        raise ValueError(f"Crop of {crop_n} events removes all {n} events")

    cropped = dict(decoded)
    end = -crop_n
    for key in ("dt_ticks", "ticks", "time_s", "dlvl", "level"):
        if key in decoded:
            cropped[key] = decoded[key][crop_n:end]
    return cropped, crop_n


def _whittaker_banded_diagonals(n, lambda_weight):
    if n < 3:
        return None

    lam2 = lambda_weight * lambda_weight

    d0 = np.full(n, 1.0 + (6.0 * lam2), dtype=float)
    d0[0] = 1.0 + lam2
    d0[1] = 1.0 + (5.0 * lam2)
    d0[-2] = 1.0 + (5.0 * lam2)
    d0[-1] = 1.0 + lam2

    d1 = np.full(n - 1, -4.0 * lam2, dtype=float)
    d1[0] = -2.0 * lam2
    d1[-1] = -2.0 * lam2

    d2 = np.full(n - 2, lam2, dtype=float)
    return d0, d1, d2


def smooth_like_gsr_eval(values, lambda_weight=1.0):
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n < 3:
        return values.copy()

    diagonals = _whittaker_banded_diagonals(n, lambda_weight)
    d0, d1, d2 = diagonals

    try:
        from scipy.linalg import solve_banded

        ab = np.zeros((5, n), dtype=float)
        ab[0, 2:] = d2
        ab[1, 1:] = d1
        ab[2, :] = d0
        ab[3, :-1] = d1
        ab[4, :-2] = d2
        return solve_banded((2, 2), ab, values)
    except ImportError:
        a = np.diag(d0)
        a += np.diag(d1, k=1) + np.diag(d1, k=-1)
        a += np.diag(d2, k=2) + np.diag(d2, k=-2)
        return np.linalg.solve(a, values)


def _metadata_from_csv_text(text):
    metadata = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            metadata.update(_parse_kv_line(line[1:]))
        elif line:
            break
    return metadata


def _load_named_csv_text(text):
    skip_header = 0
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            skip_header += 1
            continue
        break

    data = np.genfromtxt(
        io.StringIO(text),
        delimiter=",",
        comments="#",
        names=True,
        dtype=float,
        skip_header=skip_header,
    )
    if data.shape == ():
        data = np.array([data], dtype=data.dtype)
    return data


def _binary_needs_rebuild(binary_path):
    sources = (_SCRIPT_DIR / "raw_dlc_fe_native.c", _SCRIPT_DIR / "Makefile")
    if not binary_path.exists():
        return True
    binary_mtime = binary_path.stat().st_mtime
    return any(source.exists() and source.stat().st_mtime > binary_mtime for source in sources)


def ensure_raw_dlc_fe_binary(binary_path):
    binary_path = Path(binary_path)
    if binary_path != _DEFAULT_FEA_BINARY:
        if not binary_path.exists():
            raise FileNotFoundError(f"Raw dLC C FE binary not found: {binary_path}")
        return binary_path

    if not _binary_needs_rebuild(binary_path):
        return binary_path

    try:
        subprocess.run(
            ["make", "-C", str(_SCRIPT_DIR), binary_path.name],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"Failed to build C raw dLC FE binary: {details}") from exc

    return binary_path


def run_raw_dlc_fe(
    events,
    input_path,
    crop_events,
    method,
    lambda_weight,
    fea_binary,
    include_post_fe_reconstruction=False,
):
    binary_path = ensure_raw_dlc_fe_binary(fea_binary)
    cmd = [
        str(binary_path),
        "--input", str(input_path),
        "--method", method,
        "--lambda-weight", f"{lambda_weight:.17g}",
        "--crop-events", str(crop_events),
    ]
    if include_post_fe_reconstruction:
        cmd.append("--include-post-fe-reconstruction")

    try:
        completed = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"C raw dLC FE failed: {details}") from exc

    metadata = _metadata_from_csv_text(completed.stdout)
    data = _load_named_csv_text(completed.stdout)
    names = data.dtype.names or ()
    fe = {"_metadata": metadata}

    integer_columns = {"k", "ticks", "dt_ticks"}
    for name in names:
        values = np.asarray(data[name])
        if name in integer_columns:
            fe[name] = np.rint(values).astype(np.int64)
        else:
            fe[name] = values.astype(float)

    fe["sample_rate_hz"] = float(
        _parse_scalar(metadata.get("SAMPLE_RATE_HZ"), events["sample_rate_hz"])
    )

    return fe


def reconstruct_fe_to_uniform_grid(fe):
    event_ticks = np.asarray(fe["ticks"], dtype=np.int64)
    if len(event_ticks) == 0:
        raise ValueError("No FE event ticks available for fixed-rate reconstruction")

    sample_rate_hz = float(fe["sample_rate_hz"])
    if sample_rate_hz <= 0.0:
        raise ValueError("sample_rate_hz must be > 0")

    uniform_ticks = np.arange(event_ticks[0], event_ticks[-1] + 1, dtype=np.int64)
    event_indices = np.searchsorted(event_ticks, uniform_ticks, side="right") - 1
    event_indices = np.clip(event_indices, 0, len(event_ticks) - 1)

    return {
        "k": np.arange(len(uniform_ticks), dtype=np.int64),
        "ticks": uniform_ticks,
        "event_k": fe["k"][event_indices],
        "time_s": uniform_ticks.astype(float) / sample_rate_hz,
        "g_nS": fe["g_nS"][event_indices],
        "tonic": fe["tonic"][event_indices],
        "phasic": fe["phasic"][event_indices],
    }


def _csv_value(value):
    if isinstance(value, (np.integer, int)):
        return str(int(value))
    if isinstance(value, (np.floating, float)):
        if not np.isfinite(value):
            return str(value)
        return f"{float(value):.10g}"
    return str(value)


def write_csv(path, metadata, columns, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        if metadata:
            meta_text = " ".join(f"{key}={value}" for key, value in metadata.items())
            f.write(f"# {meta_text}\n")
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(columns)
        row_count = len(data[columns[0]])
        for i in range(row_count):
            writer.writerow([_csv_value(data[column][i]) for column in columns])


def load_named_csv(path):
    skip_header = 0
    with open(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                skip_header += 1
                continue
            break

    data = np.genfromtxt(
        path,
        delimiter=",",
        comments="#",
        names=True,
        dtype=float,
        skip_header=skip_header,
    )
    if data.shape == ():
        data = np.array([data], dtype=data.dtype)
    return data


def get_column(data, candidates):
    names = data.dtype.names or ()
    for name in candidates:
        if name in names:
            return data[name]
    raise ValueError(f"None of these columns are present: {', '.join(candidates)}")


def nrmse_summary(sim, ref):
    sim = np.asarray(sim, dtype=float)
    ref = np.asarray(ref, dtype=float)
    rmse = float(np.sqrt(np.mean((sim - ref) ** 2)))
    span = float(np.max(ref) - np.min(ref))
    nr = rmse / span if span != 0.0 else float("inf")
    nr_db = 20.0 * math.log10(nr) if nr > 0.0 and math.isfinite(nr) else nr
    if nr == 0.0:
        nr_db = float("-inf")
    return nr, nr_db, rmse, span


def print_metric_table(title, rows):
    print(title)
    print(f"{'Metric':<20} {'NRMSE':>10} {'NRMSE_dB':>10} {'RMSE':>12} {'Ref range':>12}")
    print("-" * 70)
    for label, sim, ref in rows:
        nr, nr_db, rmse, span = nrmse_summary(sim, ref)
        print(f"{label:<20} {nr:>10.4f} {nr_db:>10.2f} {rmse:>12.2f} {span:>12.2f}")
    print()


def _zscore(values):
    values = np.asarray(values, dtype=float)
    std = np.std(values)
    if std == 0.0:
        return values - np.mean(values)
    return (values - np.mean(values)) / std


def _alignment_error(sim_time, sim_signal, gt_idx, gt_signal, offset, step_per_s):
    mapped_idx = offset + (sim_time * step_per_s)
    valid = (mapped_idx >= gt_idx[0]) & (mapped_idx <= gt_idx[-1])
    if np.count_nonzero(valid) < max(8, len(sim_time) // 4):
        return float("inf")

    ref_signal = np.interp(mapped_idx[valid], gt_idx, gt_signal)
    sim_norm = _zscore(sim_signal[valid])
    ref_norm = _zscore(ref_signal)
    return float(np.sqrt(np.mean((sim_norm - ref_norm) ** 2)))


def fit_gt_alignment(
    sim_time,
    sim_signal,
    gt_idx,
    gt_signal,
    nominal_offset,
    nominal_step_per_s,
    mode,
    offset_search,
    step_search_frac,
):
    if mode == "nominal":
        return nominal_offset, nominal_step_per_s, "nominal", None

    step_candidates = np.array([nominal_step_per_s], dtype=float)
    if mode == "fit-affine":
        step_candidates = np.linspace(
            nominal_step_per_s * (1.0 - step_search_frac),
            nominal_step_per_s * (1.0 + step_search_frac),
            61,
        )
        step_candidates = step_candidates[step_candidates > 0.0]

    offset_candidates = np.linspace(
        nominal_offset - offset_search,
        nominal_offset + offset_search,
        401,
    )

    best_error = float("inf")
    best_offset = nominal_offset
    best_step = nominal_step_per_s

    for step in step_candidates:
        for offset in offset_candidates:
            err = _alignment_error(sim_time, sim_signal, gt_idx, gt_signal, offset, step)
            if err < best_error:
                best_error = err
                best_offset = float(offset)
                best_step = float(step)

    fine_offset_width = max(1.0, offset_search / 100.0)
    fine_offsets = np.linspace(best_offset - fine_offset_width, best_offset + fine_offset_width, 101)
    if mode == "fit-affine":
        fine_step_width = max(1e-9, nominal_step_per_s * step_search_frac / 100.0)
        fine_steps = np.linspace(best_step - fine_step_width, best_step + fine_step_width, 81)
        fine_steps = fine_steps[fine_steps > 0.0]
    else:
        fine_steps = np.array([nominal_step_per_s], dtype=float)

    for step in fine_steps:
        for offset in fine_offsets:
            err = _alignment_error(sim_time, sim_signal, gt_idx, gt_signal, offset, step)
            if err < best_error:
                best_error = err
                best_offset = float(offset)
                best_step = float(step)

    return best_offset, best_step, mode, best_error


def plot_comparison(title, x, rows, output_path=None, show_plot=False):
    if output_path is None and not show_plot:
        return

    try:
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        if not show_plot:
            import matplotlib
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available; skipping comparison plot.")
        return

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

    for ax, (label, sim_label, sim_v, ref_label, ref_v) in zip(axes, rows):
        _, nr_db, _, _ = nrmse_summary(sim_v, ref_v)
        ax.plot(x, ref_v, label=ref_label, linewidth=1.5)
        ax.plot(x, sim_v, label=sim_label, linewidth=1.3, linestyle="--")
        ax.set_ylabel(label)
        ax.set_title(f"{label} NRMSE: {nr_db:.2f} dB", fontsize=10)
        ax.legend(loc="upper right")
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel("Time (s)")
    fig.suptitle(title)
    plt.tight_layout()

    if output_path is not None:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Plot saved             : {output_path}")

    if show_plot:
        plt.show()
    else:
        plt.close(fig)


def plot_feature_output(fe, method, output_path=None, show_plot=False):
    if output_path is None and not show_plot:
        return

    try:
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        if not show_plot:
            import matplotlib
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available; skipping raw feature plot.")
        return

    if method == "level":
        rows = (
            ("Signal level", fe["fe_input"]),
            ("Tonic level", fe["tonic_fe_input"]),
            ("Phasic level", fe["phasic_fe_input"]),
        )
        title = "Raw dLC FE on cumulative level"
    elif method == "rate":
        rows = (
            ("Signal dlvl/tick", fe["fe_input"]),
            ("Tonic dlvl/tick", fe["tonic_fe_input"]),
            ("Phasic dlvl/tick", fe["phasic_fe_input"]),
        )
        title = "Raw dLC FE on dlvl rate"
    else:
        rows = (
            ("Signal dlvl", fe["dlvl"]),
            ("Tonic dlvl", fe["tonic_dlvl"]),
            ("Phasic dlvl", fe["phasic_dlvl"]),
        )
        title = "Raw dLC FE on dlvl"

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

    for ax, (label, values) in zip(axes, rows):
        ax.step(fe["time_s"], values, where="post", linewidth=1.2)
        ax.set_ylabel(label)
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel("Time (s)")
    fig.suptitle(title)
    plt.tight_layout()

    if output_path is not None:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Plot saved             : {output_path}")

    if show_plot:
        plt.show()
    else:
        plt.close(fig)


def normalize_phasic_baseline(paired, traces, baseline_frac):
    if baseline_frac <= 0.0 or baseline_frac > 1.0:
        raise ValueError("phasic baseline fraction must be in (0, 1]")

    if not traces:
        return {}, 0

    row_count = len(paired[traces[0][1]])
    if row_count == 0:
        return {}, 0

    baseline_count = max(1, int(math.ceil(row_count * baseline_frac)))
    offsets = {}

    for label, phasic_key, tonic_key in traces:
        offset = float(np.median(paired[phasic_key][:baseline_count]))
        paired[phasic_key] = paired[phasic_key] - offset
        paired[tonic_key] = paired[tonic_key] + offset
        offsets[f"{label}_phasic_baseline_offset_nS"] = offset

    return offsets, baseline_count


def print_phasic_baseline_offsets(offsets, baseline_count):
    if not offsets:
        return

    print(
        f"Phasic baseline norm   : median of first {baseline_count} paired samples"
    )
    for key, value in offsets.items():
        print(f"  {key:<31}: {value:.6g} nS")
    print()


def compare_with_gt(
    fe,
    gt_path,
    gt_line_start,
    gt_change_rate_hz,
    output_path,
    align_mode,
    offset_search,
    step_search_frac,
    compare_grid,
    normalize_phasic_baseline_flag=False,
    phasic_baseline_frac=0.1,
    plot_path=None,
    show_plot=False,
):
    if compare_grid == "fixed":
        sim = reconstruct_fe_to_uniform_grid(fe)
        sim_grid_name = "fixed_rate_hold"
    elif compare_grid == "event":
        sim = fe
        sim_grid_name = "event"
    else:
        raise ValueError(f"Unsupported GT compare grid: {compare_grid}")

    gt_params = _parse_hash_header(gt_path)
    gt_data = load_named_csv(gt_path)

    gt_idx = get_column(gt_data, ("idx", "k"))
    gt_signal = get_column(gt_data, ("signal_nS", "g_nS", "conductance_nS"))
    gt_tonic = get_column(gt_data, ("tonic",))
    gt_phasic = get_column(gt_data, ("phasic",))

    gt_origin_line = float(_parse_scalar(gt_params.get("line_start"), gt_line_start))
    gt_sample_step = float(_parse_scalar(gt_params.get("sample_step"), 1.0))

    nominal_offset = (gt_line_start - gt_origin_line) / gt_sample_step
    nominal_step_per_s = gt_change_rate_hz / gt_sample_step
    gt_offset, gt_step_per_s, align_used, align_error = fit_gt_alignment(
        sim["time_s"],
        sim["g_nS"],
        gt_idx,
        gt_signal,
        nominal_offset,
        nominal_step_per_s,
        align_mode,
        offset_search,
        step_search_frac,
    )

    gt_fe_idx = gt_offset + (sim["time_s"] * gt_step_per_s)
    valid = (gt_fe_idx >= gt_idx[0]) & (gt_fe_idx <= gt_idx[-1])
    if not np.any(valid):
        raise ValueError("No raw dLC FE samples overlap the GT FE CSV")

    paired = {
        "k": sim["k"][valid],
        "time_s": sim["time_s"][valid],
        "tick": sim["ticks"][valid],
        "gt_fe_idx": gt_fe_idx[valid],
        "sim_g_nS": sim["g_nS"][valid],
        "gt_g_nS": np.interp(gt_fe_idx[valid], gt_idx, gt_signal),
        "sim_tonic": sim["tonic"][valid],
        "gt_tonic": np.interp(gt_fe_idx[valid], gt_idx, gt_tonic),
        "sim_phasic": sim["phasic"][valid],
        "gt_phasic": np.interp(gt_fe_idx[valid], gt_idx, gt_phasic),
    }
    if compare_grid == "fixed":
        paired["event_k"] = sim["event_k"][valid]

    phasic_offsets, phasic_baseline_count = ({}, 0)
    if normalize_phasic_baseline_flag:
        phasic_offsets, phasic_baseline_count = normalize_phasic_baseline(
            paired,
            (
                ("sim", "sim_phasic", "sim_tonic"),
                ("gt", "gt_phasic", "gt_tonic"),
            ),
            phasic_baseline_frac,
        )

    metadata = {
        "GT": gt_path,
        "GT_COMPARE_GRID": sim_grid_name,
        "GT_LINE_START": _csv_value(gt_line_start),
        "GT_CHANGE_RATE_HZ": _csv_value(gt_change_rate_hz),
        "GT_SAMPLE_STEP": _csv_value(gt_sample_step),
        "GT_ALIGNMENT": align_used,
        "NOMINAL_GT_OFFSET": _csv_value(nominal_offset),
        "NOMINAL_GT_STEP_PER_S": _csv_value(nominal_step_per_s),
        "EFFECTIVE_GT_OFFSET": _csv_value(gt_offset),
        "EFFECTIVE_GT_STEP_PER_S": _csv_value(gt_step_per_s),
        "PAIRED_SAMPLES": len(paired["k"]),
        "PHASIC_BASELINE_NORMALIZED": int(normalize_phasic_baseline_flag),
        "PHASIC_BASELINE_FRAC": _csv_value(phasic_baseline_frac),
        "PHASIC_BASELINE_SAMPLES": phasic_baseline_count,
    }
    metadata.update({
        key.upper(): _csv_value(value)
        for key, value in phasic_offsets.items()
    })
    write_csv(
        output_path,
        metadata,
        (
            ("k", "time_s", "tick", "event_k", "gt_fe_idx")
            if compare_grid == "fixed"
            else ("k", "time_s", "tick", "gt_fe_idx")
        ) + (
            "sim_g_nS", "gt_g_nS",
            "sim_tonic", "gt_tonic",
            "sim_phasic", "gt_phasic",
        ),
        paired,
    )

    print(f"GT compare grid        : {sim_grid_name}")
    print(f"GT compare samples     : {len(paired['k'])}")
    print(f"GT alignment           : {align_used}")
    print(f"Nominal offset/step    : {nominal_offset:.6g} / {nominal_step_per_s:.6g} GT rows/s")
    print(f"Effective offset/step  : {gt_offset:.6g} / {gt_step_per_s:.6g} GT rows/s")
    if nominal_step_per_s > 0:
        print(f"Effective rate ratio   : {gt_step_per_s / nominal_step_per_s:.4g}x nominal")
        if align_mode == "fit-affine" and step_search_frac > 0:
            step_delta = abs(gt_step_per_s - nominal_step_per_s) / nominal_step_per_s
            if step_delta > 0.95 * step_search_frac:
                print("WARNING: fitted GT rate is at the search edge; widen --gt-step-search-frac.")
    if align_error is not None:
        print(f"Alignment shape error  : {align_error:.4f}")
    print()
    print_phasic_baseline_offsets(phasic_offsets, phasic_baseline_count)

    print_metric_table(
        f"Raw dLC FE vs GT ({gt_path})",
        (
            ("Signal (g_nS)", paired["sim_g_nS"], paired["gt_g_nS"]),
            ("Tonic", paired["sim_tonic"], paired["gt_tonic"]),
            ("Phasic", paired["sim_phasic"], paired["gt_phasic"]),
        ),
    )

    plot_comparison(
        (
            "Raw dLC FE vs Ground Truth"
            + (" (phasic baseline normalized)" if normalize_phasic_baseline_flag else "")
        ),
        paired["time_s"],
        (
            ("Signal (nS)", "Raw dLC FE", paired["sim_g_nS"], "GT", paired["gt_g_nS"]),
            ("Tonic (nS)", "Raw dLC FE", paired["sim_tonic"], "GT", paired["gt_tonic"]),
            ("Phasic (nS)", "Raw dLC FE", paired["sim_phasic"], "GT", paired["gt_phasic"]),
        ),
        output_path=plot_path,
        show_plot=show_plot,
    )


def compare_with_old_fe(
    fe,
    old_path,
    old_sample_rate,
    old_time_start,
    output_path,
    normalize_phasic_baseline_flag=False,
    phasic_baseline_frac=0.1,
    plot_path=None,
    show_plot=False,
):
    old = load_named_csv(old_path)
    names = old.dtype.names or ()

    if "time_s" in names:
        old_time = old["time_s"]
    else:
        old_idx = get_column(old, ("k", "idx"))
        old_time = old_time_start + (old_idx / old_sample_rate)

    old_signal = get_column(old, ("g_nS", "signal_nS", "conductance_nS"))
    old_tonic = get_column(old, ("tonic",))
    old_phasic = get_column(old, ("phasic",))

    valid = (fe["time_s"] >= old_time[0]) & (fe["time_s"] <= old_time[-1])
    if not np.any(valid):
        raise ValueError("No raw dLC FE samples overlap the old FE CSV")

    paired = {
        "k": fe["k"][valid],
        "time_s": fe["time_s"][valid],
        "raw_g_nS": fe["g_nS"][valid],
        "old_g_nS": np.interp(fe["time_s"][valid], old_time, old_signal),
        "raw_tonic": fe["tonic"][valid],
        "old_tonic": np.interp(fe["time_s"][valid], old_time, old_tonic),
        "raw_phasic": fe["phasic"][valid],
        "old_phasic": np.interp(fe["time_s"][valid], old_time, old_phasic),
    }

    phasic_offsets, phasic_baseline_count = ({}, 0)
    if normalize_phasic_baseline_flag:
        phasic_offsets, phasic_baseline_count = normalize_phasic_baseline(
            paired,
            (
                ("raw", "raw_phasic", "raw_tonic"),
                ("old", "old_phasic", "old_tonic"),
            ),
            phasic_baseline_frac,
        )

    metadata = {
        "OLD_FE": old_path,
        "OLD_SAMPLE_RATE_HZ": _csv_value(old_sample_rate),
        "OLD_TIME_START_S": _csv_value(old_time_start),
        "PAIRED_SAMPLES": len(paired["k"]),
        "PHASIC_BASELINE_NORMALIZED": int(normalize_phasic_baseline_flag),
        "PHASIC_BASELINE_FRAC": _csv_value(phasic_baseline_frac),
        "PHASIC_BASELINE_SAMPLES": phasic_baseline_count,
    }
    metadata.update({
        key.upper(): _csv_value(value)
        for key, value in phasic_offsets.items()
    })
    write_csv(
        output_path,
        metadata,
        (
            "k", "time_s",
            "raw_g_nS", "old_g_nS",
            "raw_tonic", "old_tonic",
            "raw_phasic", "old_phasic",
        ),
        paired,
    )

    print_metric_table(
        f"Raw dLC FE vs old FE ({old_path})",
        (
            ("Signal (g_nS)", paired["raw_g_nS"], paired["old_g_nS"]),
            ("Tonic", paired["raw_tonic"], paired["old_tonic"]),
            ("Phasic", paired["raw_phasic"], paired["old_phasic"]),
        ),
    )
    print_phasic_baseline_offsets(phasic_offsets, phasic_baseline_count)

    plot_comparison(
        (
            "Raw dLC FE vs Old Reconstructed-Flow FE"
            + (" (phasic baseline normalized)" if normalize_phasic_baseline_flag else "")
        ),
        paired["time_s"],
        (
            ("Signal (nS)", "Raw dLC FE", paired["raw_g_nS"], "Old FE", paired["old_g_nS"]),
            ("Tonic (nS)", "Raw dLC FE", paired["raw_tonic"], "Old FE", paired["old_tonic"]),
            ("Phasic (nS)", "Raw dLC FE", paired["raw_phasic"], "Old FE", paired["old_phasic"]),
        ),
        output_path=plot_path,
        show_plot=show_plot,
    )


def _format_gsr_eval_input_signal(values, line_start, line_end, sample_step=1):
    values = [int(round(value)) for value in values]
    lines = [
        "#ifndef INPUT_SIGNAL_H",
        "#define INPUT_SIGNAL_H",
        "",
        f"#define signal_length {len(values)}",
        f"#define signal_line_start {int(line_start)}",
        f"#define signal_line_end {int(line_end)}",
        f"#define signal_sample_step {int(sample_step)}",
        "",
        "static int signal[signal_length] =",
        "{",
    ]

    values_per_line = 10
    for i in range(0, len(values), values_per_line):
        chunk = values[i:i + values_per_line]
        suffix = "," if i + values_per_line < len(values) else ""
        lines.append("    " + ", ".join(str(value) for value in chunk) + suffix)

    lines.extend([
        "};",
        "",
        "#endif /* INPUT_SIGNAL_H */",
        "",
    ])
    return "\n".join(lines)


def _run_gsr_eval_native(signal_values, uniform_ticks, sample_rate_hz, gsr_eval_dir):
    gsr_eval_dir = Path(gsr_eval_dir)
    required = ("main.c", "descompv5.c", "descompv5.h")
    missing = [name for name in required if not (gsr_eval_dir / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing gsr_eval source file(s) in {gsr_eval_dir}: {', '.join(missing)}"
        )

    with tempfile.TemporaryDirectory(prefix="raw_dlc_gsr_eval_") as tmp_name:
        tmp_dir = Path(tmp_name)
        for name in required:
            shutil.copy2(gsr_eval_dir / name, tmp_dir / name)

        (tmp_dir / "input_signal.h").write_text(
            _format_gsr_eval_input_signal(
                signal_values,
                line_start=int(uniform_ticks[0]),
                line_end=int(uniform_ticks[-1]),
                sample_step=1,
            )
        )

        exe_path = tmp_dir / "gsr_eval_native"
        compile_cmd = [
            "gcc",
            "-O2",
            "-Wall",
            "-Wno-unused-result",
            "-Wno-unused-variable",
            "-o",
            str(exe_path),
            "main.c",
            "descompv5.c",
            "-lm",
        ]
        try:
            subprocess.run(
                compile_cmd,
                cwd=tmp_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            completed = subprocess.run(
                [str(exe_path)],
                cwd=tmp_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            details = exc.stderr.strip() or exc.stdout.strip()
            raise RuntimeError(f"gsr_eval native baseline failed: {details}") from exc

    data = _load_named_csv_text(completed.stdout)
    metadata = _metadata_from_csv_text(completed.stdout)
    idx = get_column(data, ("idx", "k"))

    return {
        "_metadata": metadata,
        "_source_dir": str(gsr_eval_dir),
        "time_s": (uniform_ticks[0] + idx) / sample_rate_hz,
        "g_nS": get_column(data, ("signal_nS", "g_nS", "conductance_nS")),
        "tonic": get_column(data, ("tonic",)),
        "phasic": get_column(data, ("phasic",)),
    }


def build_reconstructed_flow_baseline(events, gsr_eval_dir):
    uniform_ticks = np.arange(events["ticks"][0], events["ticks"][-1] + 1, dtype=np.int64)
    event_indices = np.searchsorted(events["ticks"], uniform_ticks, side="right") - 1
    event_indices = np.clip(event_indices, 0, len(events["level"]) - 1)

    uniform_level = events["level"][event_indices]
    uniform_g_nS = levels_to_conductance_nS(
        uniform_level,
        events["level_width"],
        events["vco_fs_hz"],
        events["idac_nA"],
    )

    # The C FE consumes integer nS samples from input_signal.h.
    baseline_signal = np.rint(uniform_g_nS).astype(float)
    return _run_gsr_eval_native(
        baseline_signal,
        uniform_ticks,
        events["sample_rate_hz"],
        gsr_eval_dir,
    )


def compare_with_reconstructed_flow_baseline(
    fe,
    baseline,
    output_path,
    trim_end_samples=5,
    normalize_phasic_baseline_flag=False,
    phasic_baseline_frac=0.1,
    plot_path=None,
    show_plot=False,
):
    if trim_end_samples < 0:
        raise ValueError("trim_end_samples must be >= 0")

    baseline_len = len(baseline["time_s"])
    if trim_end_samples >= baseline_len:
        raise ValueError(
            f"Cannot trim {trim_end_samples} reconstructed baseline samples "
            f"from only {baseline_len} samples"
        )

    baseline_slice = slice(None, -trim_end_samples) if trim_end_samples else slice(None)
    baseline_time = baseline["time_s"][baseline_slice]
    baseline_g_nS = baseline["g_nS"][baseline_slice]
    baseline_tonic = baseline["tonic"][baseline_slice]
    baseline_phasic = baseline["phasic"][baseline_slice]

    valid = (
        (fe["time_s"] >= baseline_time[0])
        & (fe["time_s"] <= baseline_time[-1])
    )
    if not np.any(valid):
        raise ValueError("No raw dLC FE samples overlap reconstructed-flow baseline")

    paired = {
        "k": fe["k"][valid],
        "time_s": fe["time_s"][valid],
        "raw_g_nS": fe["g_nS"][valid],
        "gsr_eval_g_nS": np.interp(fe["time_s"][valid], baseline_time, baseline_g_nS),
        "raw_tonic": fe["tonic"][valid],
        "gsr_eval_tonic": np.interp(fe["time_s"][valid], baseline_time, baseline_tonic),
        "raw_phasic": fe["phasic"][valid],
        "gsr_eval_phasic": np.interp(fe["time_s"][valid], baseline_time, baseline_phasic),
    }

    phasic_offsets, phasic_baseline_count = ({}, 0)
    if normalize_phasic_baseline_flag:
        phasic_offsets, phasic_baseline_count = normalize_phasic_baseline(
            paired,
            (
                ("raw", "raw_phasic", "raw_tonic"),
                ("gsr_eval", "gsr_eval_phasic", "gsr_eval_tonic"),
            ),
            phasic_baseline_frac,
        )

    metadata = {
        "BASELINE": "reconstruct_then_gsr_eval_c",
        "GSR_EVAL_DIR": baseline.get("_source_dir", ""),
        "GSR_EVAL_SIGNAL_LENGTH": baseline.get("_metadata", {}).get("signal_length", ""),
        "GSR_EVAL_TRIM_END_SAMPLES": trim_end_samples,
        "PAIRED_SAMPLES": len(paired["k"]),
        "PHASIC_BASELINE_NORMALIZED": int(normalize_phasic_baseline_flag),
        "PHASIC_BASELINE_FRAC": _csv_value(phasic_baseline_frac),
        "PHASIC_BASELINE_SAMPLES": phasic_baseline_count,
    }
    metadata.update({
        key.upper(): _csv_value(value)
        for key, value in phasic_offsets.items()
    })
    write_csv(
        output_path,
        metadata,
        (
            "k", "time_s",
            "raw_g_nS", "gsr_eval_g_nS",
            "raw_tonic", "gsr_eval_tonic",
            "raw_phasic", "gsr_eval_phasic",
        ),
        paired,
    )

    print_metric_table(
        "Raw dLC FE vs reconstructed-flow gsr_eval C baseline",
        (
            ("Signal (g_nS)", paired["raw_g_nS"], paired["gsr_eval_g_nS"]),
            ("Tonic", paired["raw_tonic"], paired["gsr_eval_tonic"]),
            ("Phasic", paired["raw_phasic"], paired["gsr_eval_phasic"]),
        ),
    )
    print_phasic_baseline_offsets(phasic_offsets, phasic_baseline_count)

    plot_comparison(
        (
            "Raw dLC FE vs Reconstruct-Then-gsr_eval Baseline"
            + (" (phasic baseline normalized)" if normalize_phasic_baseline_flag else "")
        ),
        paired["time_s"],
        (
            ("Signal (nS)", "Raw dLC FE", paired["raw_g_nS"], "gsr_eval C", paired["gsr_eval_g_nS"]),
            ("Tonic (nS)", "Raw dLC FE", paired["raw_tonic"], "gsr_eval C", paired["gsr_eval_tonic"]),
            ("Phasic (nS)", "Raw dLC FE", paired["raw_phasic"], "gsr_eval C", paired["gsr_eval_phasic"]),
        ),
        output_path=plot_path,
        show_plot=show_plot,
    )


def output_paths(prefix):
    return {
        "events": prefix.with_name(prefix.name + "_events.csv"),
        "deltas": prefix.with_name(prefix.name + "_deltas.csv"),
        "output": prefix.with_name(prefix.name + "_output.csv"),
        "raw_plot": prefix.with_name(prefix.name + "_raw_dlvl.png"),
        "reconstructed": prefix.with_name(prefix.name + "_reconstructed.csv"),
        "reconstructed_uniform": prefix.with_name(prefix.name + "_reconstructed_uniform.csv"),
        "vs_gt": prefix.with_name(prefix.name + "_vs_gt.csv"),
        "vs_gt_plot": prefix.with_name(prefix.name + "_vs_gt.png"),
        "vs_reconstructed_fe": prefix.with_name(prefix.name + "_vs_reconstructed_fe.csv"),
        "vs_reconstructed_fe_plot": prefix.with_name(prefix.name + "_vs_reconstructed_fe.png"),
        "vs_old": prefix.with_name(prefix.name + "_vs_old_fe.csv"),
        "vs_old_plot": prefix.with_name(prefix.name + "_vs_old_fe.png"),
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run FE directly on raw dLC dt/dlvl events."
    )
    parser.add_argument("--input", type=Path, default=_SCRIPT_DIR / "sim.txt")
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=_SCRIPT_DIR / "raw_dlc_fe",
        help="Prefix for generated CSVs.",
    )
    parser.add_argument(
        "--method",
        choices=("level", "delta", "rate"),
        default=_DEFAULT_METHOD,
        help=(
            "delta: C FE on original dlvl with dt weights (default), "
            "rate: C FE on dlvl/dt with dt weights, "
            "level: C FE on cumulative dLC level with hold-duration weights"
        ),
    )
    parser.add_argument("--lambda-weight", "--lambda", type=float, default=1.0)
    parser.add_argument(
        "--fea-binary",
        type=Path,
        default=_DEFAULT_FEA_BINARY,
        help="Native C raw dLC FE executable.",
    )
    parser.add_argument(
        "--crop-events",
        default="auto",
        help="Events to trim from each end before FE; use 'auto' for the legacy trim window.",
    )
    parser.add_argument(
        "--gt",
        type=Path,
        default=_SCRIPT_DIR.parent / "gsr_eval" / "gt_fe_output.csv",
        help="Ground-truth FE CSV from gsr_eval. Use --compare-gt to enable.",
    )
    parser.add_argument(
        "--no-gt",
        action="store_true",
        help="Skip conductance-domain GT comparison. This is the default.",
    )
    parser.add_argument(
        "--compare-gt",
        dest="no_gt",
        action="store_false",
        help=(
            "Enable conductance-domain comparison against --gt. This requires "
            "post-FE level/conductance conversion for the comparison only."
        ),
    )
    parser.add_argument("--gt-line-start", type=float, default=500000.0)
    parser.add_argument("--gt-change-rate-hz", type=float, default=20000.0)
    parser.add_argument(
        "--gt-align",
        choices=("nominal", "fit-offset", "fit-affine"),
        default="fit-affine",
        help=(
            "Align GT to raw event FE using nominal metadata, fitted offset, "
            "or fitted offset plus effective sample rate."
        ),
    )
    parser.add_argument(
        "--gt-compare-grid",
        choices=("fixed", "event"),
        default="event",
        help=(
            "Compare at raw event times, or use a fixed-rate held reconstruction "
            "of the post-FE outputs for GT comparison."
        ),
    )
    parser.add_argument(
        "--gt-offset-search",
        type=float,
        default=5000.0,
        help="GT FE rows searched around the nominal offset for fitted alignment.",
    )
    parser.add_argument(
        "--gt-step-search-frac",
        type=float,
        default=1.0,
        help="Fractional GT-rate search width for --gt-align fit-affine.",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Show comparison plots interactively after saving them.",
    )
    parser.add_argument(
        "--plot-raw",
        action="store_true",
        help="Plot raw-domain signal, tonic, and phasic outputs.",
    )
    parser.add_argument(
        "--no-save-plots",
        action="store_true",
        help="Do not write PNG comparison plots.",
    )
    parser.add_argument(
        "--normalize-phasic-baseline",
        action="store_true",
        help=(
            "For nS-domain comparisons, subtract each phasic trace's initial "
            "baseline median and add that offset back to its tonic trace."
        ),
    )
    parser.add_argument(
        "--phasic-baseline-frac",
        type=float,
        default=0.1,
        help="Fraction of paired comparison samples used for phasic baseline centering.",
    )
    parser.add_argument(
        "--old-fe",
        type=Path,
        default=None,
        help="Old reconstructed-flow FE CSV to compare against.",
    )
    parser.add_argument(
        "--write-post-fe-reconstruction",
        action="store_true",
        help=(
            "Also write post-FE level/conductance reconstruction CSVs. Disabled "
            "by default so the normal run stays in the raw dlvl domain."
        ),
    )
    baseline_group = parser.add_mutually_exclusive_group()
    baseline_group.add_argument(
        "--compare-reconstructed-flow-baseline",
        dest="reconstructed_flow_baseline",
        action="store_true",
        help=(
            "Enable the reconstruct-then-gsr_eval C comparison baseline. This is "
            "off by default because it deliberately reconstructs amplitude before "
            "running the legacy FE path."
        ),
    )
    baseline_group.add_argument(
        "--no-reconstructed-flow-baseline",
        dest="reconstructed_flow_baseline",
        action="store_false",
        help="Skip the reconstruct-then-gsr_eval C comparison baseline.",
    )
    parser.add_argument(
        "--gsr-eval-dir",
        type=Path,
        default=_DEFAULT_GSR_EVAL_DIR,
        help="Directory containing the real gsr_eval main.c/descompv5.c baseline.",
    )
    parser.add_argument(
        "--reconstructed-baseline-trim-end",
        type=int,
        default=5,
        help="Trim this many final gsr_eval baseline samples before comparison.",
    )
    parser.add_argument(
        "--old-fe-sample-rate",
        type=float,
        default=None,
        help="Sample rate for old FE rows when the old CSV has no time_s column.",
    )
    parser.add_argument(
        "--old-fe-time-start",
        type=float,
        default=None,
        help="Time of old FE row 0 when the old CSV has no time_s column.",
    )
    parser.set_defaults(no_gt=True, reconstructed_flow_baseline=False)
    return parser.parse_args()


def feature_domain_name(method):
    if method == "level":
        return "level"
    if method == "rate":
        return "dlvl_per_tick"
    return "dlvl"


def feature_output_for_method(fe, method):
    common = {
        "k": fe["k"],
        "time_s": fe["time_s"],
        "ticks": fe["ticks"],
    }

    if method == "level":
        columns = ("k", "time_s", "ticks", "level", "tonic_level", "phasic_level")
        data = {
            **common,
            "level": fe["fe_input"],
            "tonic_level": fe["tonic_fe_input"],
            "phasic_level": fe["phasic_fe_input"],
        }
    elif method == "rate":
        columns = (
            "k", "time_s", "ticks",
            "dlvl_per_tick", "tonic_dlvl_per_tick", "phasic_dlvl_per_tick",
        )
        data = {
            **common,
            "dlvl_per_tick": fe["fe_input"],
            "tonic_dlvl_per_tick": fe["tonic_fe_input"],
            "phasic_dlvl_per_tick": fe["phasic_fe_input"],
        }
    else:
        columns = ("k", "time_s", "ticks", "dlvl", "tonic_dlvl", "phasic_dlvl")
        data = {
            **common,
            "dlvl": fe["dlvl"],
            "tonic_dlvl": fe["tonic_dlvl"],
            "phasic_dlvl": fe["phasic_dlvl"],
        }

    return columns, data


def main():
    args = parse_args()
    needs_post_fe_reconstruction = (
        args.write_post_fe_reconstruction
        or not args.no_gt
        or args.reconstructed_flow_baseline
        or args.old_fe is not None
    )
    needs_decoded_level = args.reconstructed_flow_baseline
    decoded_all = decode_raw_dlc(args.input, include_level=needs_decoded_level)
    decoded, crop_n = crop_events(decoded_all, args.crop_events)
    fe = run_raw_dlc_fe(
        decoded,
        args.input,
        crop_n,
        args.method,
        args.lambda_weight,
        args.fea_binary,
        include_post_fe_reconstruction=needs_post_fe_reconstruction,
    )
    paths = output_paths(args.output_prefix)

    common_meta = {
        "RAW_DLC_FE": 1,
        "FEA_IMPL": "C",
        "TIME_AWARE": 1,
        "METHOD": args.method,
        "FEATURE_DOMAIN": feature_domain_name(args.method),
        "PRE_FE_RECONSTRUCTION": 0,
        "LAMBDA": _csv_value(args.lambda_weight),
        "INPUT": args.input,
        "SAMPLE_RATE_HZ": _csv_value(decoded["sample_rate_hz"]),
        "LOG_LEVEL_WIDTH": decoded["params"].get("LOG_LEVEL_WIDTH", ""),
        "INITIAL_LEVEL": decoded["params"].get("INITIAL_LEVEL", ""),
        "IDAC_CODE": decoded["params"].get("IDAC_CODE", ""),
        "CROP_EVENTS_EACH_END": crop_n,
        "EVENTS": len(fe["k"]),
        "TIME_WEIGHT": fe.get("_metadata", {}).get("TIME_WEIGHT", ""),
        "ROUGHNESS": fe.get("_metadata", {}).get("ROUGHNESS", ""),
    }

    write_csv(
        paths["events"],
        common_meta,
        (
            "k", "time_s", "ticks", "dt_ticks", "dt_s",
            "hold_ticks", "weight_ticks", "dlvl",
        ),
        {
            "k": fe["k"],
            "time_s": fe["time_s"],
            "ticks": fe["ticks"],
            "dt_ticks": fe["dt_ticks"],
            "dt_s": fe["dt_s"],
            "hold_ticks": fe["hold_ticks"],
            "weight_ticks": fe["weight_ticks"],
            "dlvl": fe["dlvl"],
        },
    )

    write_csv(
        paths["deltas"],
        common_meta,
        (
            "k", "time_s", "dt_ticks", "hold_ticks", "weight_ticks", "dlvl",
            "fe_input", "tonic_fe_input", "phasic_fe_input",
            "tonic_dlvl", "phasic_dlvl",
        ),
        fe,
    )

    output_columns, output_data = feature_output_for_method(fe, args.method)
    write_csv(paths["output"], common_meta, output_columns, output_data)

    if args.plot_raw:
        plot_feature_output(
            fe,
            args.method,
            output_path=None if args.no_save_plots else paths["raw_plot"],
            show_plot=args.plot,
        )

    if args.write_post_fe_reconstruction:
        fe_uniform = reconstruct_fe_to_uniform_grid(fe)
        reconstructed_meta = dict(common_meta)
        reconstructed_meta["POST_FE_RECONSTRUCTION"] = 1
        write_csv(
            paths["reconstructed"],
            reconstructed_meta,
            (
                "k", "time_s", "ticks", "dt_ticks", "dt_s", "hold_ticks", "weight_ticks",
                "dlvl", "tonic_dlvl", "phasic_dlvl",
                "level", "tonic_level", "phasic_level",
                "g_nS", "tonic", "phasic",
            ),
            fe,
        )

        uniform_meta = dict(reconstructed_meta)
        uniform_meta["GRID"] = "fixed_rate_hold"
        uniform_meta["UNIFORM_SAMPLES"] = len(fe_uniform["k"])
        write_csv(
            paths["reconstructed_uniform"],
            uniform_meta,
            ("k", "time_s", "ticks", "event_k", "g_nS", "tonic", "phasic"),
            fe_uniform,
        )

    print(f"Decoded raw dLC events : {len(decoded_all['dlvl'])}")
    print(f"Cropped events/end     : {crop_n}")
    print(f"FE events              : {len(fe['k'])}")
    print(f"Raw FE implementation  : C ({args.fea_binary})")
    print(f"Raw FE method          : {args.method}")
    print(f"Raw FE domain          : {common_meta['FEATURE_DOMAIN']}")
    print(f"Raw FE time weight     : {common_meta['TIME_WEIGHT']}")
    print(f"Wrote                  : {paths['events']}")
    print(f"Wrote                  : {paths['deltas']}")
    print(f"Wrote                  : {paths['output']}")
    if args.plot_raw and not args.no_save_plots:
        print(f"Wrote                  : {paths['raw_plot']}")
    if args.write_post_fe_reconstruction:
        print(f"Wrote                  : {paths['reconstructed']}")
        print(f"Wrote                  : {paths['reconstructed_uniform']}")
    print()

    if not args.no_gt:
        if args.gt.exists():
            compare_with_gt(
                fe,
                args.gt,
                args.gt_line_start,
                args.gt_change_rate_hz,
                paths["vs_gt"],
                args.gt_align,
                args.gt_offset_search,
                args.gt_step_search_frac,
                args.gt_compare_grid,
                normalize_phasic_baseline_flag=args.normalize_phasic_baseline,
                phasic_baseline_frac=args.phasic_baseline_frac,
                plot_path=None if args.no_save_plots else paths["vs_gt_plot"],
                show_plot=args.plot,
            )
            print(f"Wrote                  : {paths['vs_gt']}")
            print()
        else:
            print(f"GT CSV not found, skipping GT comparison: {args.gt}")

    if args.reconstructed_flow_baseline:
        baseline = build_reconstructed_flow_baseline(decoded, args.gsr_eval_dir)
        compare_with_reconstructed_flow_baseline(
            fe,
            baseline,
            paths["vs_reconstructed_fe"],
            trim_end_samples=args.reconstructed_baseline_trim_end,
            normalize_phasic_baseline_flag=args.normalize_phasic_baseline,
            phasic_baseline_frac=args.phasic_baseline_frac,
            plot_path=None if args.no_save_plots else paths["vs_reconstructed_fe_plot"],
            show_plot=args.plot,
        )
        print(f"Wrote                  : {paths['vs_reconstructed_fe']}")
        print()

    if args.old_fe is not None:
        if not args.old_fe.exists():
            raise FileNotFoundError(f"Old FE CSV not found: {args.old_fe}")
        old_sample_rate = (
            args.old_fe_sample_rate
            if args.old_fe_sample_rate is not None
            else decoded["sample_rate_hz"]
        )
        old_time_start = (
            args.old_fe_time_start
            if args.old_fe_time_start is not None
            else float(fe["time_s"][0])
        )
        compare_with_old_fe(
            fe,
            args.old_fe,
            old_sample_rate,
            old_time_start,
            paths["vs_old"],
            normalize_phasic_baseline_flag=args.normalize_phasic_baseline,
            phasic_baseline_frac=args.phasic_baseline_frac,
            plot_path=None if args.no_save_plots else paths["vs_old_plot"],
            show_plot=args.plot,
        )
        print(f"Wrote                  : {paths['vs_old']}")


if __name__ == "__main__":
    main()
