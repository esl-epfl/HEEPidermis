"""The 16 EDA features of Setz et al. (2010), section IV-C.

Reference: C. Setz, B. Arnrich, J. Schumm, R. La Marca, G. Tröster and
U. Ehlert, doi:10.1109/TITB.2009.2036164. Filtering uses SciPy (Virtanen
et al., 2020). The rolling robust threshold is an implementation choice;
the paper does not specify its adaptive threshold. See ../REFERENCES.md.
"""

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, find_peaks, sosfiltfilt

from ..tools.timeseries import Timeseries
from ..tools.ts_params import (
    TSP_CHANNELS, TSP_CITATION, TSP_COVERAGE, TSP_FEATURE, TSP_MAGNITUDE,
    TSP_PEAK_COUNT, TSP_PROCESSING, TSP_SOURCE, TSP_SUBJECT, TSP_UNITS,
    TSP_VALID, TSP_WINDOW_S,
)
from .features import (
    AMPLITUDE_FEATURES, FEATURE_NAMES, INSTANT_RATE_Q85_HZ, INSTANT_RATE_Q95_HZ,
    LEVEL_MAX_S, LEVEL_MEAN_S, LEVEL_MIN_S, LEVEL_SLOPE_S_PER_S,
    MIST_LINEAR_FEATURES, PEAK_HEIGHT_MEAN_S, PEAK_HEIGHT_Q50_S, PEAK_RATE_PER_MIN,
)

QUANTILES = (25, 50, 75, 85, 95)
DEFAULTS = dict(sample_rate_Hz=4., lowpass_Hz=5., highpass_Hz=.05,
                max_gap_s=1., min_block_s=10., edge_exclusion_s=3.,
                threshold_window_s=10., threshold_noise_multiplier=3.,
                min_peak_height_S=.01e-6, min_peak_distance_s=1.,
                window_s=60., step_s=15., min_coverage=.9, interpolate_gaps=True,
                relative_windows=False)


def _settings(options):
    if set(options) - set(DEFAULTS):
        raise ValueError(f"Unknown feature options: {set(options) - set(DEFAULTS)}")
    settings = {**DEFAULTS, **options}
    if not all(np.isfinite(v) and v >= 0 for v in settings.values()):
        raise ValueError("Feature parameters must be finite and nonnegative.")
    for key in ("sample_rate_Hz", "lowpass_Hz", "highpass_Hz", "max_gap_s", "min_block_s",
                "threshold_window_s", "min_peak_distance_s", "window_s", "step_s"):
        if settings[key] <= 0:
            raise ValueError(f"{key} must be positive.")
    if not 0 < settings["min_coverage"] <= 1:
        raise ValueError("min_coverage must be in (0, 1].")
    if not isinstance(settings["interpolate_gaps"], bool):
        raise ValueError("interpolate_gaps must be a boolean.")
    if not isinstance(settings["relative_windows"], bool):
        raise ValueError("relative_windows must be a boolean.")
    return settings


def _identity(data):
    return {key: data.params[key] for key in (TSP_SUBJECT, TSP_SOURCE, TSP_CITATION) if key in data.params}


def decompose_conductance(data, **options):
    """Return a dictionary of Timeseries: level, tonic, phasic, threshold and peaks.

    Blocks and peak-block IDs are also Timeseries. With interpolate_gaps on,
    invalid conductance samples are linearly interpolated between their nearest
    valid neighbours (the nearest valid value is held at the recording edges),
    so the extracted features stay continuous; only large time gaps and segment
    boundaries still split the zero-phase filtering. Native-grid low-pass
    filtering precedes resampling for anti-aliasing.

    The input may be physical conductance in siemens or per-recording
    normalized conductance (units "normalized", e.g. from
    data_to_conductance_S.normalized_conductance); with normalized input, all
    amplitude quantities (min_peak_height_S, peak heights) are in normalized
    units.
    """
    p = _settings(options)
    if data.params[TSP_MAGNITUDE] != "conductance" or data.params[TSP_UNITS] not in ("S", "normalized"):
        raise ValueError("Feature extraction requires conductance in siemens or normalized conductance.")
    time, values = data.time, np.array(data.data, dtype=float)
    if len(time) < 2:
        raise ValueError("Decomposition requires at least two samples.")
    hz = p["sample_rate_Hz"]
    grid = time[0] + np.arange(int(np.floor((time[-1] - time[0]) * hz)) + 1) / hz
    level, phasic, threshold = (np.full(len(grid), np.nan) for _ in range(3))
    blocks = np.full(len(grid), -1.)
    segments = data.params.get(TSP_CHANNELS, {}).get("segment")
    if segments is not None and not np.array_equal(segments.time, time):
        raise ValueError("Segment timestamps must match conductance timestamps.")
    boundary = np.diff(time) > p["max_gap_s"]
    if segments is not None:
        boundary |= np.diff(segments.data) != 0
    valid = np.isfinite(values) & (values > 0)
    if p["interpolate_gaps"]:
        if not valid.any():
            raise ValueError("No valid conductance samples to interpolate from.")
        index = np.arange(len(values))
        values = np.interp(index, index[valid], values[valid])
        valid = np.ones(len(values), dtype=bool)
    starts = np.flatnonzero(np.r_[True, boundary | ~valid[:-1] | ~valid[1:]])
    peak_times, peak_heights, peak_blocks = [], [], []
    cutoffs = []
    block = 0
    for a, b in zip(starts, np.r_[starts[1:], len(time)]):
        if b - a < 16 or not valid[a:b].all() or time[b - 1] - time[a] < p["min_block_s"]:
            continue
        native_hz = 1 / np.median(np.diff(time[a:b]))
        cutoff = min(p["lowpass_Hz"], .45 * native_hz, .45 * hz)
        if p["highpass_Hz"] >= cutoff:
            raise ValueError("High-pass cutoff must be below the effective low-pass cutoff.")
        native_time = np.arange(time[a], time[b - 1], 1 / native_hz)
        if len(native_time) < 16:
            continue
        native = np.interp(native_time, time[a:b], values[a:b])
        smooth = sosfiltfilt(butter(2, cutoff, fs=native_hz, output="sos"), native)
        idx = np.flatnonzero((grid >= native_time[0]) & (grid <= native_time[-1]))
        if len(idx) < 16:
            continue
        local_level = np.interp(grid[idx], native_time, smooth)
        local_phasic = sosfiltfilt(butter(2, p["highpass_Hz"], "highpass", fs=hz, output="sos"), local_level)
        local_phasic = sosfiltfilt(butter(2, cutoff, fs=hz, output="sos"), local_phasic)
        width = max(3, round(p["threshold_window_s"] * hz) | 1)
        differences = np.r_[0., np.diff(local_phasic)]
        center = median_filter(differences, size=width, mode="nearest")
        noise = median_filter(np.abs(differences - center), size=width, mode="nearest") * 1.4826 / np.sqrt(2.)
        local_threshold = np.maximum(p["min_peak_height_S"], p["threshold_noise_multiplier"] * noise)
        peaks, _ = find_peaks(local_phasic, height=local_threshold,
                              distance=max(1, round(p["min_peak_distance_s"] * hz)))
        interior = ((grid[idx] >= grid[idx[0]] + p["edge_exclusion_s"]) &
                    (grid[idx] <= grid[idx[-1]] - p["edge_exclusion_s"]))
        peaks = peaks[interior[peaks]]
        level[idx], phasic[idx], threshold[idx] = local_level, local_phasic, local_threshold
        blocks[idx[interior]] = block
        peak_times.extend(grid[idx[peaks]])
        peak_heights.extend(local_phasic[peaks])
        peak_blocks.extend([block] * len(peaks))
        cutoffs.append(cutoff)
        block += 1
    params = {**_identity(data), TSP_PROCESSING: p, "Effective lowpass (Hz)": sorted(set(cutoffs))}
    result = {}
    for key, value in (("level", level), ("tonic", level - phasic),
                       ("phasic", phasic), ("threshold", threshold)):
        ts = Timeseries(data.name, value, grid)
        ts.params.update(params)
        ts.params[TSP_MAGNITUDE], ts.params[TSP_UNITS] = "conductance", "S"
        result[key] = ts
    for key, values, magnitude in (("blocks", blocks, "processing_block"),
                                   ("peaks", peak_heights, "peak_height"),
                                   ("peak_blocks", peak_blocks, "processing_block")):
        time = peak_times if key in ("peaks", "peak_blocks") else grid
        ts = Timeseries(data.name, values, time)
        ts.params.update(params)
        ts.params[TSP_MAGNITUDE], ts.params[TSP_UNITS] = magnitude, "S" if key == "peaks" else "index"
        result[key] = ts
    return result


def summarize_window(decomposition, start, end, relative=False):
    """Calculate one window's scalar statistics; instantaneous rates never cross gaps.

    With relative=True, the amplitude features (AMPLITUDE_FEATURES) are divided
    by the mean conductance of the window itself, so a 25% rise reads as 1.25
    regardless of the baseline SCL; level_mean_S becomes exactly 1. The rate
    features are already level-independent and stay absolute. Caveat: peak
    detection keeps the absolute min_peak_height_S floor, so with very small
    absolute levels the detected peak set (and with it the peak-rate and
    peak-height features) can differ from a scaled-up copy of the signal.
    """
    level, blocks, peaks = (decomposition[key] for key in ("level", "blocks", "peaks"))
    selected = (level.time >= start) & (level.time < end) & (blocks.data >= 0)
    time, values = level.time[selected], level.data[selected]
    if len(time) < 2:
        return dict.fromkeys(FEATURE_NAMES, np.nan), 0, 0.
    duration = len(time) * np.median(np.diff(level.time))
    selected_peaks = (peaks.time >= start) & (peaks.time < end)
    heights, times = peaks.data[selected_peaks], peaks.time[selected_peaks]
    peak_blocks = decomposition["peak_blocks"].data[selected_peaks]
    rates = 1 / np.diff(times)[np.diff(peak_blocks) == 0]
    centered = time - time.mean()
    slope = np.dot(centered, values - values.mean()) / np.dot(centered, centered)
    features = [values.mean(), values.max(), values.min(), slope,
                heights.mean() if len(heights) else np.nan, len(heights) * 60 / duration]
    for distribution in (heights, rates):
        features.extend(np.percentile(distribution, QUANTILES) if len(distribution) else [np.nan] * 5)
    features = dict(zip(FEATURE_NAMES, features))
    if relative:
        reference = values.mean()
        if np.isfinite(reference) and reference > 0:
            for name in AMPLITUDE_FEATURES:
                features[name] = features[name] / reference
        else:  # Without a usable level reference the amplitudes are meaningless.
            for name in AMPLITUDE_FEATURES:
                features[name] = np.nan
    return features, len(heights), min(1., duration / (end - start))


def conductance_S_to_features(data, feature_names=None, **options):
    """Return {feature_name: Timeseries} for the requested features of FEATURE_NAMES.

    feature_names selects the vocabulary entries to extract (all of them when
    None); each timestamp is the window's end. Window duration, units, subject,
    processing settings and Timeseries quality channels are embedded in params.
    The processing settings record the input representation (input_units), so
    the train/inference consistency check rejects mixed physical/normalized
    inputs. With relative_windows, amplitude features are divided by the mean
    conductance of their own window (units become "1"), making them independent
    of the baseline SCL. No labels are required for extraction.
    """
    p = _settings(options)
    selected = FEATURE_NAMES if feature_names is None else tuple(feature_names)
    unknown = [name for name in selected if name not in FEATURE_NAMES]
    if unknown:
        raise ValueError(f"Unknown feature names {unknown}; available: {list(FEATURE_NAMES)}")
    decomposition = decompose_conductance(data, **p)
    level = decomposition["level"]
    end = level.time[-1] + 1 / p["sample_rate_Hz"]
    starts = np.arange(level.time[0], end - p["window_s"] + 1e-7, p["step_s"])
    times = starts + p["window_s"]
    columns = {name: [] for name in FEATURE_NAMES}
    valid, coverage, counts = [], [], []
    for start, stop in zip(starts, times):
        values, count, fraction = summarize_window(decomposition, start, stop,
                                                   relative=p["relative_windows"])
        usable = fraction >= p["min_coverage"]
        for name in FEATURE_NAMES:
            columns[name].append(values[name] if usable else np.nan)
        valid.append(usable)
        coverage.append(fraction)
        counts.append(count)
    params = {**_identity(data), TSP_WINDOW_S: p["window_s"],
              TSP_PROCESSING: {**p, "input_units": data.params[TSP_UNITS]}}
    for key, values, magnitude in ((TSP_VALID, valid, "validity"), (TSP_COVERAGE, coverage, "coverage"),
                                   (TSP_PEAK_COUNT, counts, "peak_count")):
        ts = Timeseries(data.name, values, times)
        ts.params.update(_identity(data))
        ts.params[TSP_MAGNITUDE], ts.params[TSP_UNITS] = magnitude, "1"
        params[key] = ts
    result = {}
    for name in selected:
        if p["relative_windows"] and name in AMPLITUDE_FEATURES:
            unit = "1/s" if name == LEVEL_SLOPE_S_PER_S else "1"
        else:
            unit = "S/s" if "slope" in name else "peaks/min" if name == PEAK_RATE_PER_MIN else "Hz" if name.startswith("instant") else "S"
        ts = Timeseries(data.name, columns[name], times)
        ts.params.update(params)
        ts.params[TSP_MAGNITUDE], ts.params[TSP_UNITS], ts.params[TSP_FEATURE] = "feature", unit, name
        result[name] = ts
    return result


def extraction_options(processing):
    """Feature-extraction options from a processing dictionary, dropping metadata keys."""
    return {key: value for key, value in processing.items() if key in DEFAULTS}


def baseline_relative(features, baseline):
    """Setz IV-C: subtract baseline mean for levels, divide remaining features.

    baseline is a dictionary of single-window feature Timeseries. A zero or
    missing denominator yields NaN. Output feature values remain Timeseries.
    """
    result = {}
    for name, feature in features.items():
        reference = baseline[LEVEL_MEAN_S] if name in (LEVEL_MEAN_S, LEVEL_MAX_S, LEVEL_MIN_S) else baseline[name]
        if len(reference.data) != 1:
            raise ValueError("Baseline features must contain exactly one window.")
        value = reference.data[0]
        if name in (LEVEL_MEAN_S, LEVEL_MAX_S, LEVEL_MIN_S):
            values, units = feature.data - value, "S"
        else:
            values = feature.data / value if np.isfinite(value) and value != 0 else np.full(len(feature.time), np.nan)
            units = "1"
        result[name] = Timeseries(feature.name, values, feature.time)
        result[name].params.update(feature.params)
        result[name].params[TSP_MAGNITUDE], result[name].params[TSP_UNITS] = "feature", units
    return result
