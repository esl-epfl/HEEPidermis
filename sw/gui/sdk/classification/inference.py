"""Pure live-inference computation for the Classification device.

Every algorithmic step is delegated to ``sw/classification`` — model loading,
unit conversion, the Setz feature extraction, SVM inference, plane margins and
span merging — so GUI behaviour automatically follows any change there. The
functions here only adapt interfaces: workspace series in, plain display
dicts out. No Tk, no threads.

Class naming: the model's positive class (the margin-above-threshold side) is
displayed as POSITIVE, the other as NEGATIVE, whatever the training called it.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[4]

# Workspace catalogue units the pipeline can convert to siemens, mapped to the
# TSP magnitude data_to_conductance_S expects. Voltage sources are not usable
# here: the conversion needs a time-aligned current channel the workspace
# series do not carry.
SUPPORTED_UNITS = {
    "S": "conductance", "mS": "conductance", "uS": "conductance",
    "µS": "conductance", "nS": "conductance",
    "ohm": "resistance", "kohm": "resistance", "Mohm": "resistance",
}

_PLANE_RESOLUTION = 120
_PLANE_PAD = .08   # Fraction of the data span added to each side of the plane.

_MODULES = None


def classification_modules():
    """Import (and cache) the sw/classification functions the device reuses."""
    global _MODULES
    if _MODULES is None:
        if str(ROOT) not in sys_path():
            sys_path().insert(0, str(ROOT))
        from sw.classification.classification.features_to_classification import (
            classify_features, load_model, plane_margins)
        from sw.classification.conversion.data_to_conductance_S import (
            data_to_conductance_S, normalized_conductance)
        from sw.classification.features.conductance_S_to_features import (
            conductance_S_to_features, extraction_options, feature_unit)
        from sw.classification.plots import _merged_spans, display_transform
        from sw.classification.tools.timeseries import Timeseries
        from sw.classification.tools.ts_params import (
            TSP_DECISION, TSP_MAGNITUDE, TSP_UNITS, TSP_VALID, TSP_WINDOW_S)
        _MODULES = SimpleNamespace(
            classify_features=classify_features, load_model=load_model,
            plane_margins=plane_margins, data_to_conductance_S=data_to_conductance_S,
            normalized_conductance=normalized_conductance,
            conductance_S_to_features=conductance_S_to_features,
            extraction_options=extraction_options, feature_unit=feature_unit,
            merged_spans=_merged_spans, display_transform=display_transform,
            timeseries=Timeseries, tsp_magnitude=TSP_MAGNITUDE, tsp_units=TSP_UNITS,
            tsp_valid=TSP_VALID, tsp_window=TSP_WINDOW_S, tsp_decision=TSP_DECISION)
    return _MODULES


def sys_path():
    import sys
    return sys.path


def load_model(path):
    """Load and structurally validate a saved SVM model (joblib)."""
    model = classification_modules().load_model(path)
    for key in ("pipeline", "feature_names", "feature_options", "classes"):
        if key not in model:
            raise ValueError(f"Not a sw/classification SVM model: missing '{key}'.")
    if len(model["feature_names"]) < 1:
        raise ValueError("The model has no features.")
    return model


def input_timeseries(name, times, values, unit):
    """Workspace series -> conductance/resistance Timeseries in pipeline form."""
    mods = classification_modules()
    magnitude = SUPPORTED_UNITS.get(unit)
    if magnitude is None:
        raise ValueError(f"Unsupported source unit '{unit}'; "
                         f"expected one of {sorted(SUPPORTED_UNITS)}.")
    data = np.array([np.nan if value is None else float(value) for value in values], dtype=float)
    series = mods.timeseries(name, data, np.asarray(times, dtype=float))
    series.params[mods.tsp_magnitude] = magnitude
    series.params[mods.tsp_units] = unit
    return series


def positive_spans(times, raw_margins, window_s, threshold=0.):
    """Disjoint (start, end) spans of POSITIVE windows (raw margin > threshold).

    Timestamps are window ends, so each span starts one window before its
    first POSITIVE prediction; overlapping windows (window longer than step)
    are merged into unions, like the pipeline's prediction spans.
    """
    mods = classification_modules()
    positive = np.isfinite(raw_margins) & (raw_margins > threshold)
    if not positive.any():
        return []
    edges = np.diff(np.r_[False, positive, False].astype(int))
    spans = []
    for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        start = times[a] - window_s if window_s else times[max(a - 1, 0)]
        spans.append((max(0., float(start)), float(times[b - 1])))
    return mods.merged_spans(spans)


def training_ranges(model, names):
    """Per-feature (low, high) over the model's training windows, raw units."""
    low = {name: np.inf for name in names}
    high = {name: -np.inf for name in names}
    for features in model["training_features"].values():
        for name in names:
            values = np.asarray(features[name].data, dtype=float)
            finite = values[np.isfinite(values)]
            if len(finite):
                low[name] = min(low[name], float(finite.min()))
                high[name] = max(high[name], float(finite.max()))
    return low, high


def feature_plane(model, features, point_raw, resolution=_PLANE_RESOLUTION):
    """Feature-space slice for the pane: grid extents, margins and axes.

    The pair is the model's first two features. Omitted features are fixed to
    the latest live values (``point_raw``), so the drawn boundary is the true
    slice through the current operating point; missing values fall back to
    the training medians inside plane_margins. Grid and point are returned in
    display units (the same transformation the pipeline plots use).
    """
    mods = classification_modules()
    names = tuple(model["feature_names"])
    if len(names) < 2:
        return None
    pair = names[:2]
    processing = dict(model["feature_options"])
    scales = [mods.display_transform(mods.feature_unit(name, processing)) for name in names]
    low, high = training_ranges(model, names)
    live = {name: features[name].data for name in names}
    for position, name in enumerate(names):
        values = np.asarray(live[name], dtype=float)
        finite = values[np.isfinite(values)]
        if len(finite):
            low[name] = min(low[name], float(finite.min()))
            high[name] = max(high[name], float(finite.max()))
    low = np.array([low[name] for name in pair])
    high = np.array([high[name] for name in pair])
    span = np.maximum(high - low, 1e-12) * _PLANE_PAD
    uu, vv = np.meshgrid(np.linspace(low[0] - span[0], high[0] + span[0], resolution),
                         np.linspace(low[1] - span[1], high[1] + span[1], resolution))
    at = {name: value for name, value in (point_raw or {}).items() if np.isfinite(value)}
    margins = mods.plane_margins(model, pair, uu, vv, at=at)
    point_xy = None
    if point_raw is not None and all(np.isfinite(point_raw.get(name, np.nan)) for name in pair):
        point_xy = (float(point_raw[pair[0]]) * scales[0][0], float(point_raw[pair[1]]) * scales[1][0])
    return {"pair": pair, "uu": uu * scales[0][0], "vv": vv * scales[1][0],
            "margins": margins, "scales": scales, "point_xy": point_xy}


def compute_outputs(model, threshold, times, values, unit,
                    window_s=None, step_s=None, sample_rate_Hz=None):
    """One full live inference from a source series snapshot.

    Returns a plain dict with the device's signals (display units), the
    POSITIVE spans, the operating point and the feature-space plane, or raises
    on unusable input. ``threshold`` overrides the model's stored threshold
    (inference-time operating point, as in sw/classification main.py).
    ``window_s``/``step_s``/``sample_rate_Hz``, when given, override the
    windowing the model was trained with (its stored feature options).
    """
    mods = classification_modules()
    threshold = float(threshold)
    processing = dict(model["feature_options"])
    overrides = {"window_s": window_s, "step_s": step_s,
                 "sample_rate_Hz": sample_rate_Hz}
    overrides = {key: value for key, value in overrides.items() if value is not None}
    options = mods.extraction_options(processing)
    options.update(overrides)
    names = tuple(model["feature_names"])
    conductance = mods.data_to_conductance_S(input_timeseries("live", times, values, unit))
    if processing.get("input_units") == "normalized":
        conductance = mods.normalized_conductance(conductance)
    features = mods.conductance_S_to_features(conductance, feature_names=names, **options)
    # classify_features rejects feature settings that differ from the fitted
    # model; the windowing overrides are deliberate inference-time tuning, so
    # classify against a view of the model that records them.
    classify_model = (dict(model, feature_options={**processing, **overrides})
                      if overrides else model)
    prediction = mods.classify_features(classify_model, features, threshold)
    window_s = float(prediction.params.get(mods.tsp_window, 0.) or 0.)
    window_times = [float(value) for value in prediction.time]
    # classify_features stores margins already shifted by the threshold; the
    # device exposes the raw margin plus the threshold as a separate signal.
    raw_margins = np.asarray(prediction.params[mods.tsp_decision].data, dtype=float) + threshold
    finite = np.isfinite(raw_margins)
    decision_values = np.where(finite, (raw_margins > threshold).astype(float), np.nan)
    scales = {name: mods.display_transform(mods.feature_unit(name, processing)) for name in names}
    point_raw = None
    valid = np.asarray(features[names[0]].params[mods.tsp_valid].data, dtype=float) == 1
    for index in range(len(window_times) - 1, -1, -1):
        if valid[index] and all(np.isfinite(features[name].data[index]) for name in names):
            point_raw = {name: float(features[name].data[index]) for name in names}
            break
    return {
        "window_times": window_times,
        "threshold": threshold,
        "features": {name: {"times": window_times,
                            "values": [np.nan if not np.isfinite(value) else float(value) * scales[name][0]
                                       for value in features[name].data],
                            "unit": scales[name][1]}
                     for name in names},
        "margin": {"times": window_times,
                   "values": [np.nan if not np.isfinite(value) else float(value)
                              for value in raw_margins]},
        "margin_threshold": {"times": window_times, "values": [threshold] * len(window_times)},
        "decision": {"times": window_times,
                     "values": [np.nan if not np.isfinite(value) else float(value)
                                for value in decision_values],
                     "spans": positive_spans(np.asarray(window_times), raw_margins,
                                             window_s, threshold)},
        "point_raw": point_raw,
        "point_display": ({name: float(point_raw[name]) * scales[name][0]
                           for name in names} if point_raw is not None else None),
        "plane": feature_plane(model, features, point_raw),
        "windows_valid": int(np.count_nonzero(finite)),
    }
