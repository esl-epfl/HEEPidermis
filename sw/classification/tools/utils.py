from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict, is_dataclass
from enum import Enum
from functools import wraps
from pathlib import Path
from time import perf_counter
import hashlib
import json
import re
import numpy as np

from .ts_params import TSP_F_HZ


# =============================================================================
# arrays.py
# =============================================================================

def centers_to_edges(c):
    c = np.asarray(c)
    edges = np.empty(len(c) + 1)
    edges[1:-1] = 0.5 * (c[:-1] + c[1:])
    edges[0] = c[0] - 0.5 * (c[1] - c[0])
    edges[-1] = c[-1] + 0.5 * (c[-1] - c[-2])
    return edges


def edges_to_centers(edges):
    edges = np.asarray(edges)
    return 0.5 * (edges[:-1] + edges[1:])


def rebin_counts(bins_initial, counts_initial, bins_final):
    bins_initial = np.asarray(bins_initial)
    counts_initial = np.asarray(counts_initial, dtype=float)
    bins_final = np.asarray(bins_final)

    edges_initial = centers_to_edges(bins_initial)
    edges_final = centers_to_edges(bins_final)
    counts_final = np.zeros(len(bins_final))

    for i, count in enumerate(counts_initial):
        left, right = edges_initial[i], edges_initial[i + 1]
        overlap = np.maximum(0, np.minimum(right, edges_final[1:]) - np.maximum(left, edges_final[:-1]))
        if overlap.sum() > 0:
            counts_final += count * overlap / overlap.sum()

    return counts_final


def get_bin_at_coverage(bins, counts, coverage):
    counts = np.asarray(counts, dtype=float)
    cumsum = np.cumsum(counts) / np.sum(counts)
    return bins[np.searchsorted(cumsum, coverage)]


def moving_average(x, w=8):
    return np.convolve(x, np.ones(w) / w, mode="same")

# =============================================================================
# distributions.py
# =============================================================================

@dataclass
class DistributionStatistics:
    mass: float
    mean: float
    sigma: float
    mean_m1: float
    mean_p1: float
    mean_m3: float
    mean_p3: float
    pmf: np.ndarray


def get_distribution_statistics(axis, P):
    axis = np.asarray(axis)
    P = np.asarray(P)
    mass = np.sum(P)

    if mass <= 0:
        return DistributionStatistics(mass, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.zeros_like(P))

    PMF = P / mass
    mean = np.sum(PMF * axis)
    sigma = np.sqrt(np.sum(PMF * (axis - mean) ** 2))

    return DistributionStatistics(
        mass=mass,
        mean=mean,
        sigma=sigma,
        mean_m1=mean - sigma,
        mean_p1=mean + sigma,
        mean_m3=mean - 3 * sigma,
        mean_p3=mean + 3 * sigma,
        pmf=PMF,
    )

# =============================================================================
# event_modes.py
# =============================================================================

@dataclass
class CrossingModeRateResults:
    """Crossing-rate statistics for one output-event policy."""

    name: str
    mean_rate_Hz: float
    interval_mean_rate_Hz: float
    sigma_rate_Hz: float
    min_rate_Hz: float
    max_rate_Hz: float
    mean_interval_s: float
    sigma_interval_s: float
    crossing_time_s: np.ndarray | None = None
    crossing_time_PMF: np.ndarray | None = None
    rate_samples_Hz: np.ndarray | None = None
    events_n: float | None = None

    @property
    def expected_interval_s(self):
        return self.mean_interval_s

# =============================================================================
# identity.py
# =============================================================================

def jsonable(value):
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        return {"__ndarray__": {"shape": list(array.shape), "dtype": str(array.dtype), "sha256": hashlib.sha256(array.tobytes()).hexdigest()}}
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def stable_id(value, length=10):
    payload = json.dumps(jsonable(value), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:length]


def adc_id(adc):
    identity = {"name": adc.name, "design_parameters": adc.dp}
    dac = getattr(getattr(adc, "comps", None), "dac", None)
    if dac is not None:
        identity["dac_dnl_LSB"] = getattr(dac, "dnl_LSB", None)
        identity["dac_inl_LSB"] = getattr(dac, "inl_LSB", None)
        identity["dac_out_vals_V"] = getattr(dac, "out_vals_V", None)
    return stable_id(identity)


def signal_id(series):
    data = np.asarray(series.data)
    time = np.asarray(series.time)
    metadata = {
        "name": series.name,
        "params": series.params,
        "samples": len(data),
        "data_sha256": hashlib.sha256(data.tobytes()).hexdigest(),
        "time_sha256": hashlib.sha256(time.tobytes()).hexdigest(),
    }
    return stable_id(metadata)


def safe_name(value):
    value = "unnamed" if value is None else str(value)
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return value[:80] or "unnamed"

# =============================================================================
# progress.py
# =============================================================================

PROGRESS_WIDTH = 210


def print_progress(stage, run, total, detail="", width=PROGRESS_WIDTH):
    percentage = 100 * run / total if total > 0 else 100.0
    line = f"{stage:<32} | {run:6d}/{total:<6d} | {percentage:6.2f}% | {detail}"
    print("\r" + line.ljust(width), end="", flush=True)


def finish_progress(message="Complete", width=PROGRESS_WIDTH):
    print("\r" + message.ljust(width))


# =============================================================================
# verbosity.py
# =============================================================================

_VERBOSITY = 1

def set_verbosity(level):
    global _VERBOSITY
    _VERBOSITY = int(level)

def get_verbosity():
    return int(_VERBOSITY)

def vprint(*args, level=1, **kwargs):
    if _VERBOSITY >= int(level):
        print(*args, **kwargs)

# =============================================================================
# timing.py
# =============================================================================

_TIMINGS = defaultdict(lambda: {"calls": 0, "total_s": 0.0, "max_s": 0.0, "last_s": 0.0})


def timed(label=None):
    """Report wall-clock time and accumulate it for the final timing summary."""
    def decorator(func):
        timer_label = label or f"{func.__module__}.{func.__name__}"

        @wraps(func)
        def wrapper(*args, **kwargs):
            start = perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed = perf_counter() - start
                entry = _TIMINGS[timer_label]
                entry["calls"] += 1
                entry["total_s"] += elapsed
                entry["max_s"] = max(entry["max_s"], elapsed)
                entry["last_s"] = elapsed
                if get_verbosity() >= 2:
                    print(f"[TIME] {timer_label}: {format_duration(elapsed)}")

        return wrapper
    return decorator


def reset_timings():
    _TIMINGS.clear()


def timing_rows():
    rows = []
    for label, value in _TIMINGS.items():
        rows.append((label, int(value["calls"]), float(value["total_s"]), float(value["max_s"]), float(value["last_s"])))
    return sorted(rows, key=lambda x: x[2], reverse=True)


def format_duration(seconds):
    seconds = float(seconds)
    if seconds < 1e-3:
        return f"{seconds*1e6:.1f} us"
    if seconds < 1.0:
        return f"{seconds*1e3:.1f} ms"
    if seconds < 60.0:
        return f"{seconds:.2f} s"
    minutes, sec = divmod(seconds, 60.0)
    if minutes < 60:
        return f"{int(minutes)}m {sec:.1f}s"
    hours, minutes = divmod(minutes, 60.0)
    return f"{int(hours)}h {int(minutes)}m {sec:.0f}s"


def print_timing_summary():
    rows = timing_rows()
    if not rows:
        print("\nTIMING SUMMARY\n(no timed functions executed)")
        return

    headers = ("Function", "Calls", "Total", "Max call", "Last call")
    rendered = [(label, str(calls), format_duration(total), format_duration(max_s), format_duration(last_s)) for label, calls, total, max_s, last_s in rows]
    widths = [len(h) for h in headers]
    for row in rendered:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    def line(values):
        return " | ".join(str(value).ljust(widths[i]) for i, value in enumerate(values))

    print("\nTIMING SUMMARY (inclusive wall time; nested functions may overlap)")
    print(line(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rendered:
        print(line(row))

# =============================================================================
# matrix_summary.py
# =============================================================================

def _shape_text(shape):
    return " × ".join(str(int(v)) for v in shape)


def _memory_text(shape, bytes_per_value=8):
    n = int(np.prod(shape))
    size = n * bytes_per_value
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{size / 1024:.1f} KiB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MiB"
    return f"{size / 1024**3:.2f} GiB"


def get_matrix_plan(adc, simulation_parameters):
    char = simulation_parameters.characterization
    sp = simulation_parameters.signal_statistics
    stat = simulation_parameters.statistical
    order = str(stat.model_order).upper()
    rank = {"W": -1, "D0": 0, "D1": 1, "D2": 2}[order]

    n_w_v = len(np.asarray(adc.comps.dac.codes_V))
    n_w_dv = 2 * int(char.slew_range_steps_n)
    n_w = int(char.width_pmf_bins_n)
    n_v = n_w_v if sp.amplitude_bins_n is None else int(sp.amplitude_bins_n)
    n_dv = int(sp.derivative_bins_n)
    n_d2v = int(sp.second_derivative_bins_n)
    n_hs = n_w if stat.headstart_bins_n is None else int(stat.headstart_bins_n)
    n_dw = 2 * n_w - 1
    n_rev = int(stat.reversal_error_bins_n)
    n_dt = int(stat.crossing_time_bins_n)

    rows = [
        ("W(V,V',w)", (n_w_v, n_w_dv, n_w), "ADC characterization PMF, independent from the signal.", 8),
    ]
    if rank >= 0:
        rows.append(("D0(V)", (n_v,), "Signal amplitude occupancy PMF.", 8))
    if rank >= 1:
        rows.extend([
            ("D1(V,V')", (n_v, n_dv), "Signal amplitude/derivative occupancy PMF.", 8),
            ("W -> D1 grid", (n_v, n_dv, n_w), "Characterized W interpolated onto the signal V/V' grid.", 8),
        ])
    if rank >= 2:
        rows.extend([
            ("D2(V,V',V'')", (n_v, n_dv, n_d2v), "Second-order signal occupancy PMF.", 8),
            ("Headstart grid", (n_hs,), "Distance-state resolution carried through probabilistic transitions.", 8),
            ("P(w_same,V)", (n_v, n_w), "Same-direction crossing-conditioned width PMF.", 8),
            ("P(Delta w_same,V)", (n_v, n_dw), "Same-direction local width-difference distribution.", 8),
            ("P(reversal error,V)", (n_v, n_rev), "Signed amplitude recapture error on reversals.", 8),
            ("P(Delta t_cross)", (n_dt,), "Reported-event interval PMF.", 8),
        ])
        if simulation_parameters.runtime.keep_statistical_debug:
            rows.extend([
                ("J_all debug", (n_v, n_dv, n_dv), "Stored transition matrices for every amplitude.", 4),
                ("cross_map_all debug", (n_v, n_d2v, n_dv), "Stored next-crossing destination map.", 4),
            ])
    return rows

def print_matrix_plan(adc, simulation_parameters):
    rows = get_matrix_plan(adc, simulation_parameters)
    headers = ("Matrix", "Dimensions", "Approx. RAM", "Use")
    formatted = [(name, _shape_text(shape), _memory_text(shape, bytes_per_value), use) for name, shape, use, bytes_per_value in rows]
    widths = [len(h) for h in headers]
    for row in formatted:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    def line(values):
        return " | ".join(str(value).ljust(widths[i]) for i, value in enumerate(values))

    print("")
    print("PLANNED MATRIX DIMENSIONS")
    print(line(headers))
    print("-+-".join("-" * w for w in widths))
    for row in formatted:
        print(line(row))

# =============================================================================
# reporting.py
# =============================================================================

def _fmt_fs(value):
    value = float(value)
    if value >= 1e6:
        return f"{value/1e6:.3f} MHz"
    if value >= 1e3:
        return f"{value/1e3:.3f} kHz"
    return f"{value:.3f} Hz"


def print_experiment_summary(adc, signal, simulation_parameters, characterization=None, signal_statistics=None, empirical_fs_Hz=None, signal_duration_s=None):
    """Print the identity and numerical settings associated with a result table."""
    dp = adc.dp
    source_fs = float(signal.params[TSP_F_HZ])
    source_duration_s = float(signal.time[-1] - signal.time[0]) if len(signal.time) > 1 else 0.0
    empirical_fs_Hz = simulation_parameters.empirical.sampling_frequency_for_signal(signal) if empirical_fs_Hz is None else empirical_fs_Hz
    signal_duration_s = source_duration_s if signal_duration_s is None else signal_duration_s
    char = simulation_parameters.characterization
    d2 = simulation_parameters.signal_statistics
    stat = simulation_parameters.statistical

    print("\nRESULT CONTEXT")
    print(f"ADC                  : {adc.name} [{adc_id(adc)}]")
    print(f"  architecture       : {dp.res_gen_type.name}")
    print(f"  input range        : [{dp.Vss_V:g}, {dp.Vdd_V:g}] V")
    print(f"  levels             : {dp.lsb_n} ({dp.lsb_range_b} bits)")
    print(f"  nominal width      : {dp.lsb_V*1e3:.3f} mV")
    print(f"  DAC DNL            : max {dp.dac_max_dnl:g} LSB, seed={dp.dac_dnl_seed}")
    print(f"  input noise        : max {dp.max_noise_V*1e3:.3f} mV, seed={dp.noise_seed}")
    print(f"  comparator tau     : {dp.cmp_tau_s*1e6:.3f} us")
    print(f"  loop delay         : {dp.loop_delay_s*1e6:.3f} us")

    print(f"Signal               : {signal.name} [{signal_id(signal)}]")
    print(f"  source samples     : {len(signal.data)}")
    print(f"  source fs          : {_fmt_fs(source_fs)}")
    print(f"  source duration    : {source_duration_s:.6f} s")
    if len(signal.data):
        print(f"  amplitude observed : [{float(np.min(signal.data)):.5g}, {float(np.max(signal.data)):.5g}] V")

    print(f"Simulation           : {simulation_parameters.name} [{simulation_parameters.id}]")
    print(f"  model order        : {stat.model_order.upper()}")
    print(f"  W characterization : {char.slew_range_steps_n} linear slopes, {char.slew_range_low_V_s:g}..{char.slew_range_high_V_s:g} V/s")
    print(f"  ramp repetitions   : {char.periods_n} periods/slope")
    print(f"  ramp resolution    : {char.ramp_half_intervals_n} half-ramp intervals")
    print(f"  characterization fs: {'adaptive from ramp source' if char.sampling_frequency_Hz is None else _fmt_fs(char.sampling_frequency_Hz)}")
    print(f"  width PMF          : {char.width_pmf_bins_n} bins")
    print(f"  signal-stat bins   : V={d2.amplitude_bins_n if d2.amplitude_bins_n is not None else 'ADC grid'}, V'={d2.derivative_bins_n}, V''={d2.second_derivative_bins_n}")
    if signal_statistics is not None:
        actual_order = (signal_statistics.metadata or {}).get("model_order", "D2")
        print(f"  {actual_order} actual          : {signal_statistics.D2.shape} = {signal_statistics.D2.size:,} cells")
    if characterization is not None:
        print(f"  W actual           : {characterization.W.shape} = {characterization.W.size:,} cells")
    print(f"  headstart bins     : {stat.headstart_bins_n}")
    print(f"  crossing-time bins : {stat.crossing_time_bins_n}")
    print(f"  empirical fs       : {_fmt_fs(empirical_fs_Hz)}")
    print(f"  compared duration  : {signal_duration_s:.6f} s")


def print_approximation_recommendations(simulation_parameters):
    """Print the three highest-leverage convergence parameters for D2-vs-empirical agreement."""
    char = simulation_parameters.characterization
    d2 = simulation_parameters.signal_statistics

    next_vpp = max(d2.second_derivative_bins_n + 32, int(np.ceil(1.5 * d2.second_derivative_bins_n / 2.0) * 2))
    if next_vpp % 2:
        next_vpp += 1
    next_slopes = max(char.slew_range_steps_n + 16, int(np.ceil(1.6 * char.slew_range_steps_n)))
    next_periods = max(char.periods_n + 10, 2 * char.periods_n)

    print("\nTOP 3 CONVERGENCE PARAMETERS")
    print(f"1. signal_statistics.second_derivative_bins_n : {d2.second_derivative_bins_n} -> test {next_vpp}")
    print("   Improves the second-order turning/reversal probability. No ADC reruns; D2 memory and D2-state work grow roughly linearly with this value.")
    print(f"2. characterization.slew_range_steps_n       : {char.slew_range_steps_n} -> test {next_slopes}")
    print("   Improves interpolation of W versus V'. Requires proportionally more ramp ADC simulations and grows W approximately linearly in the slope dimension.")
    print(f"3. characterization.periods_n                : {char.periods_n} -> test {next_periods}")
    print("   Improves W, crossing-position and paired-Delta-w statistics. Characterization runtime grows approximately linearly; it is especially expensive when characterization.sampling_frequency_Hz is fixed for slow ramps.")
    print("Change one parameter at a time and stop increasing it once D2-vs-empirical metrics cease moving materially.")
