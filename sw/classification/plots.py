"""Timeseries diagnostics and feature-space views of the fitted SVM.

Decision surfaces are conditional slices: the two selected features span the
plane and the omitted model features are fixed to their training medians.
Full-dimensional decisions are plotted separately in time.
References: REFERENCES.md.
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Patch

from .classification.features_to_classification import (
    _training_rows, classify_features, feature_matrix, plane_margins,
)
from .conversion.data_to_conductance_S import normalized_conductance
from .features.conductance_S_to_features import decompose_conductance, extraction_options
from .features.features import INSTANT_RATE_Q85_HZ, PEAK_HEIGHT_Q50_S
from .tools.ts_params import (
    TSP_CLASSES, TSP_DECISION, TSP_PEAK_COUNT, TSP_PROCESSING, TSP_THRESHOLD, TSP_UNITS,
    TSP_VALID, TSP_WINDOW_S,
)


def display_transform(unit):
    """Scale and unit label for legible axes (e.g. siemens shown as microsiemens)."""
    if unit in ("S", "S/s"):
        return 1e6, unit.replace("S", "µS", 1)
    if unit in ("uV", "µV"):
        return 1e-6, "V"
    if unit == "mV":
        return 1e-3, "V"
    return 1., unit


def _range_mask(ts, time_range):
    """Boolean mask of the samples of ts inside the plotting range."""
    if time_range is None:
        return np.ones(len(ts.time), dtype=bool)
    return (ts.time >= time_range[0]) & (ts.time <= time_range[1])


def _annotation_spans(annotations, time_range):
    """Half-open (start, end, "stress") spans clipped to the plotting range.

    Stress is the only class of interest; every other annotation is irrelevant
    for the plots and is skipped.
    """
    if annotations is None or not len(annotations.time):
        return []
    classes = annotations.params.get(TSP_CLASSES, {})
    limit = time_range[1] if time_range is not None else annotations.time[-1]
    spans = []
    for i, value in enumerate(annotations.data):
        label = int(value)
        if label < 0 or classes.get(label) != "stress":
            continue
        start = annotations.time[i] if time_range is None else max(annotations.time[i], time_range[0])
        end = annotations.time[i + 1] if i + 1 < len(annotations.time) else limit
        if time_range is not None:
            end = min(end, limit)
        if end > start:
            spans.append((start, end, "stress"))
    return spans


def plot_stages(data, conductance, features, annotations=None, time_range=None):
    """Offline conductance decomposition for one recording, optionally cropped in time."""
    first = next(iter(features.values()))
    d = decompose_conductance(conductance, **extraction_options(first.params[TSP_PROCESSING]))
    fig, axes = plt.subplots(3, 1, figsize=(14, 8), sharex=True, constrained_layout=True,
                             gridspec_kw={"height_ratios": (2, 1, 1)})
    fig.suptitle(f"{data.name} — offline EDA processing")
    m = _range_mask(conductance, time_range)
    axes[0].plot(conductance.time[m], conductance.data[m] * 1e6, lw=.6, color="gray", label="Converted G")
    for row, names in ((1, ("level", "tonic")), (2, ("phasic", "threshold"))):
        for name in names:
            ts = d[name]
            m = _range_mask(ts, time_range)
            axes[row].plot(ts.time[m], ts.data[m] * 1e6, lw=.8, label=name)
    peaks = d["peaks"]
    m = _range_mask(peaks, time_range)
    axes[2].scatter(peaks.time[m], peaks.data[m] * 1e6, s=24, marker="*", color="tab:red", label="Detected peaks")
    valid = first.params[TSP_VALID]
    mask = valid.data == 1
    if time_range is not None:
        mask &= (valid.time >= time_range[0]) & (valid.time <= time_range[1])
    eligible = np.flatnonzero(mask)
    if len(eligible):
        index = eligible[len(eligible) // 2]
        end = first.time[index]
        start = end - first.params[TSP_WINDOW_S]
        for ax in axes[1:]:
            ax.axvspan(start, end, color="gold", alpha=.15)
        details = [f"Window {start:.0f}–{end:.0f} s; {first.params[TSP_PEAK_COUNT].data[index]:.0f} peaks"]
        for key, label, factor, unit in ((PEAK_HEIGHT_Q50_S, "height q50", 1e6, "µS"),
                                         (INSTANT_RATE_Q85_HZ, "instant. rate q85", 1., "Hz")):
            if key in features:
                value = features[key].data[index] * factor
                if np.isfinite(value):
                    details.append(f"{label}={value:.3g} {unit}")
                    if key == PEAK_HEIGHT_Q50_S:
                        axes[2].hlines(value, start, end, colors="purple", lw=2, label="Height q50")
        axes[2].text(.01, .95, "; ".join(details), transform=axes[2].transAxes, va="top", fontsize=9,
                     bbox=dict(facecolor="white", alpha=.85, edgecolor="none"))
    for number, (a, b, label) in enumerate(_annotation_spans(annotations, time_range)):
        axes[0].axvspan(a, b, alpha=.12, color="tab:red")
        axes[0].text((a + b) / 2, 1.01 + .05 * (number % 2), label, fontsize=8, ha="center",
                     transform=axes[0].get_xaxis_transform())
    bad = d["blocks"].data < 0
    edges = np.diff(np.r_[False, bad, False].astype(int))
    for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        for ax in axes[1:]:
            ax.axvspan(d["level"].time[a], d["level"].time[min(b, len(bad) - 1)], color="gray", alpha=.12)
    for ax, label in zip(axes, ("Conductance [µS]", "Level / tonic [µS]", "Phasic [µS]")):
        ax.set_ylabel(label)
        ax.grid(alpha=.2)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Recording time [s]")
    if time_range is not None:
        for ax in axes:
            ax.set_xlim(*time_range)
    return fig


def _merged_spans(spans):
    """Union of possibly overlapping (start, end) spans, as disjoint spans."""
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _prediction_spans(prediction, time_range):
    """Disjoint (start, end) spans of stress-predicted windows.

    Prediction timestamps are window ends, so a span starts one window before
    its first stress prediction and ends at the last one. Because windows are
    longer than the step between them (e.g. 60 s window, 15 s step), the
    per-run spans of nearby stress windows can overlap; their union is
    returned, so predicted regions never overlap each other.
    """
    if prediction is None or not len(prediction.time):
        return []
    classes = prediction.params.get(TSP_CLASSES, {})
    stress = next((index for index, name in classes.items() if name == "stress"), None)
    if stress is None:
        return []
    stressed = (prediction.data == stress) & np.isfinite(prediction.data)
    edges = np.diff(np.r_[False, stressed, False].astype(int))
    window = prediction.params.get(TSP_WINDOW_S)
    limit = time_range[1] if time_range is not None else prediction.time[-1]
    spans = []
    for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        start = prediction.time[a] - window if window else prediction.time[max(a - 1, 0)]
        end = prediction.time[b - 1]
        if time_range is not None:
            start, end = max(start, time_range[0]), min(end, limit)
        if end > start:
            spans.append((start, end))
    return _merged_spans(spans)


def plot_recording(data, conductance, features, annotations=None, feature_names=(), time_range=None,
                   prediction=None, normalized=False):
    """One figure per recording: conductance, features, confidence, annotations.

    Feature rows are scatter plots at a much smaller height than the
    conductance row. The annotation spans are drawn as background bands across
    every row; the class names label the bands on the first row and in its
    legend. With a prediction Timeseries, stress-predicted windows are shaded
    in blue (merged into disjoint regions) next to the red ground-truth stress
    bands, and a final row shows the per-window raw SVM margin as the
    prediction confidence: its sign is the predicted class and its magnitude
    the distance to the decision boundary. The threshold line marks the
    operating point (windows above it are stress); with a nonzero threshold,
    the dotted line marks the raw margin-zero boundary the SVM was fitted to.
    With normalized=True, the conductance row shows normalized_conductance
    instead of microsiemens.
    """
    missing = [name for name in feature_names if name not in features]
    if missing:
        raise ValueError(f"Features not available for plotting: {missing}")
    names = list(feature_names)
    rows = 1 + len(names) + (1 if prediction is not None else 0)
    fig, axes = plt.subplots(rows, 1, figsize=(12, .8 * (3 + rows - 1)),
                             sharex=True, constrained_layout=True,
                             gridspec_kw={"height_ratios": (3, *([1] * (rows - 1)))})
    axes = np.atleast_1d(axes)
    fig.suptitle(f"{data.name} — conductance, features and annotations")
    if normalized:
        shown = normalized_conductance(conductance)
        m = _range_mask(shown, time_range)
        axes[0].plot(shown.time[m], shown.data[m], lw=.8, color="tab:green")
        axes[0].set_ylabel("Conductance\n[normalized]")
        axes[0].set_ylim(-.02, 1.05)
    else:
        m = _range_mask(conductance, time_range)
        axes[0].plot(conductance.time[m], conductance.data[m] * 1e6, lw=.8, color="tab:green")
        axes[0].set_ylabel("Conductance\n[µS]")
    for ax, name in zip(axes[1:1 + len(names)], names):
        ts = features[name]
        m = _range_mask(ts, time_range)
        scale, unit = display_transform(ts.params[TSP_UNITS])
        ax.plot(ts.time[m], ts.data[m] * scale, marker=".", ms=2.5, lw=.6)
        ax.set_ylabel(name if unit == "1" else f"{name}\n[{unit}]",
                      rotation=0, ha="right", va="center", fontsize=7)
    if prediction is not None:
        margins = prediction.params[TSP_DECISION]
        threshold = prediction.params.get(TSP_THRESHOLD, 0.)
        m = _range_mask(margins, time_range) & np.isfinite(margins.data)
        # Raw SVM margins: a window is stress exactly above the threshold line.
        axes[-1].plot(margins.time[m], margins.data[m] + threshold, marker=".", ms=3, lw=.6,
                      color="tab:purple")
        axes[-1].axhline(threshold, color="black", lw=1, label=f"threshold {threshold:g}")
        if threshold:
            axes[-1].axhline(0., color="black", lw=.8, ls="dotted", label="fitted boundary")
        axes[-1].legend(loc="upper right", fontsize=7)
        axes[-1].set_ylabel("SVM margin\n(confidence)", rotation=0, ha="right", va="center", fontsize=7)
    spans = _annotation_spans(annotations, time_range)
    for number, (a, b, label) in enumerate(spans):
        for ax in axes:
            ax.axvspan(a, b, alpha=.12, color="tab:red")
        axes[0].text((a + b) / 2, 1.02 + .06 * (number % 2), label, fontsize=8, ha="center",
                     transform=axes[0].get_xaxis_transform())
    predicted = _prediction_spans(prediction, time_range)
    for a, b in predicted:
        for ax in axes:
            ax.axvspan(a, b, alpha=.12, color="tab:blue")
    handles = []
    if spans:
        handles.append(Patch(facecolor="tab:red", alpha=.3, label="stress"))
    if predicted:
        handles.append(Patch(facecolor="tab:blue", alpha=.3, label="predicted stress"))
    if handles:
        axes[0].legend(handles=handles, loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Recording time [s]")
    for axis in axes:
        axis.grid(alpha=.2)
    if time_range is not None:
        for axis in axes:
            axis.set_xlim(*time_range)
    return fig


def plot_classification(conductance, prediction, annotations=None, time_range=None):
    """Conductance, per-window SVM predictions and the stress-only ground truth.

    Classes follow the agreed convention: "stress" versus "not stress"
    (everything else). Ground-truth stress spans are also shaded on the
    conductance row for direct comparison.
    """
    classes = prediction.params[TSP_CLASSES]
    stress = next(index for index, name in classes.items() if name == "stress")
    fig, axes = plt.subplots(3, 1, figsize=(12, 5.5), sharex=True, constrained_layout=True,
                             gridspec_kw={"height_ratios": (3, 1, 1)})
    fig.suptitle(f"{prediction.name} — SVM prediction against stress ground truth")
    m = _range_mask(conductance, time_range)
    axes[0].plot(conductance.time[m], conductance.data[m] * 1e6, lw=.8, color="tab:green")
    axes[0].set_ylabel("Conductance\n[µS]")
    m = _range_mask(prediction, time_range) & np.isfinite(prediction.data)
    colors = ["tab:red" if value == stress else "tab:green" for value in prediction.data[m]]
    axes[1].scatter(prediction.time[m], prediction.data[m], s=6, c=colors)
    axes[1].set_yticks(sorted(classes), [classes[key] for key in sorted(classes)])
    axes[1].set_ylim(min(classes, default=0) - .4, max(classes, default=1) + .4)
    axes[1].set_ylabel("Prediction")
    stress_spans = [(a, b) for a, b, label in _annotation_spans(annotations, time_range) if label == "stress"]
    for a, b in stress_spans:
        axes[2].axvspan(a, b, color="tab:red", alpha=.6)
        axes[0].axvspan(a, b, color="tab:red", alpha=.12)
    axes[2].set_yticks([1], ["stress"])
    axes[2].set_ylim(0, 1.5)
    axes[2].set_ylabel("Ground truth")
    axes[-1].set_xlabel("Recording time [s]")
    for axis in axes:
        axis.grid(alpha=.2)
    if time_range is not None:
        for axis in axes:
            axis.set_xlim(*time_range)
    return fig


def _annotation_labels(annotations, times):
    """Annotation class index (step function) at each time; -1 where unannotated."""
    index = np.searchsorted(annotations.time, times, side="right") - 1
    labels = np.full(len(times), -1)
    inside = index >= 0
    labels[inside] = annotations.data[index[inside]]
    return labels


def plot_feature_trajectory(model, features, feature_names=None, time_range=None, decision_threshold=None,
                            annotations=None):
    """Feature-space views of the fitted SVM with the recording's trajectory.

    One panel per feature pair: the selected pair and — when the model has
    exactly three features — the pair sharing the first selected feature with
    the remaining one, so the three-dimensional feature space can be read from
    two projections. Omitted model features are fixed to their training
    medians. The shaded regions and the solid boundary show the effective
    decision at the model's decision_threshold (or the decision_threshold
    override); the dotted line marks the raw margin-zero boundary the SVM was
    fitted to. Training windows and support vectors are open circles (no
    filling) in their class colors; the support vectors are fainter and larger.
    The recording trajectory is drawn in the plasma colormap by window time,
    with red square markers on the windows the annotations mark as stress: a
    square on the decision side opposite to the stress region is a missed
    stress window.
    """
    pipeline, model_names = model["pipeline"], model["feature_names"]
    threshold = model.get("decision_threshold", 0.) if decision_threshold is None else decision_threshold
    names = tuple(feature_names) if feature_names is not None else tuple(model_names[:2])
    if len(set(names)) != len(names):
        raise ValueError("Feature names for plotting must be unique.")
    if not set(names).issubset(features):
        raise ValueError(f"Features not available for plotting: {sorted(set(names) - set(features))}")
    if len(names) != 2:
        raise ValueError("Select exactly two features for the feature-space plot.")
    if not set(names).issubset(model_names):
        raise ValueError(f"Feature-space plots can only use model features: {model_names}")
    inputs, valid = feature_matrix(features, model_names)
    predictions = classify_features(model, features, decision_threshold)
    mask = valid.copy()
    if time_range is not None:
        mask &= (predictions.time >= time_range[0]) & (predictions.time <= time_range[1])
    if not mask.any():
        raise ValueError("No valid prediction windows inside the plotting time range.")
    times = predictions.time[mask]
    step_index = np.flatnonzero(mask)
    adjacent = np.diff(step_index) == 1
    svm = pipeline[-1]
    classes = list(pipeline.classes_)
    stress_name = next((name for name in classes if name == "stress"), classes[-1])
    class_colors = {name: "tab:red" if name == stress_name else "tab:blue" for name in classes}
    x_train, labels, _ = _training_rows(model["training_features"], model["training_annotations"], model_names)
    # Annotated stress windows, the only ones that get square trajectory markers.
    marked = None
    if annotations is not None and len(annotations.time):
        annotation_labels = _annotation_labels(annotations, times)
        stress_key = next((key for key, name in annotations.params.get(TSP_CLASSES, {}).items()
                           if name == "stress"), None)
        marked = stress_key is not None and annotation_labels == stress_key
    remaining = [name for name in model_names if name not in names]
    pairs = [names] + ([(names[0], remaining[0])] if len(model_names) == 3 else [])
    fig, spaces = plt.subplots(1, len(pairs), figsize=(6.5 * len(pairs), 6), constrained_layout=True)
    spaces = np.atleast_1d(spaces)
    time_norm = plt.Normalize(times.min(), times.max() + 1e-9)
    for space, pair in zip(spaces, pairs):
        index = [model_names.index(name) for name in pair]
        trajectory = inputs[mask][:, index]
        scales = [display_transform(features[name].params[TSP_UNITS]) for name in pair]
        points = np.vstack([x_train[:, index], trajectory])
        low, high = points.min(axis=0), points.max(axis=0)
        span = np.maximum(high - low, 1e-12) * .08
        uu, vv = np.meshgrid(np.linspace(low[0] - span[0], high[0] + span[0], 160),
                             np.linspace(low[1] - span[1], high[1] + span[1], 160))
        scores = plane_margins(model, pair, uu, vv)
        plane = (uu * scales[0][0], vv * scales[1][0])
        space.contourf(plane[0], plane[1], scores > threshold, levels=[-.5, .5, 1.5],
                       colors=["#d9eaf7", "#f9dddd"], alpha=.7)
        if scores.min() < threshold < scores.max():
            space.contour(plane[0], plane[1], scores, levels=[threshold], colors="black", linewidths=1.2)
        if threshold:
            space.contour(plane[0], plane[1], scores, levels=[0], colors="black", linewidths=.8,
                          linestyles="dotted")
        for name in classes:
            selected = labels == name
            space.scatter(x_train[selected, index[0]] * scales[0][0],
                          x_train[selected, index[1]] * scales[1][0],
                          s=22, facecolors="none", edgecolors=class_colors[name], alpha=.6, label=name)
        # Support vectors: faint open circles in their class color.
        support_labels = labels[svm.support_]
        for name in classes:
            selected = support_labels == name
            if not selected.any():
                continue
            support = pipeline.named_steps["standardscaler"].inverse_transform(svm.support_vectors_[selected])
            space.scatter(support[:, index[0]] * scales[0][0], support[:, index[1]] * scales[1][0],
                          s=35, facecolors="none", edgecolors=class_colors[name], alpha=.25, linewidths=.9)
        axis_labels = [name if unit == "1" else f"{name} [{unit}]" for name, (_, unit) in zip(pair, scales)]
        space.set_xlabel(axis_labels[0])
        space.set_ylabel(axis_labels[1])
        space.set_title("SVM slice; omitted features at training medians\n"
                        f"boundary: margin = {threshold:g} "
                        f"({'effective' if threshold else 'fitted'}; dotted: raw 0)\n"
                        "red squares = annotated stress; faint circles = support vectors")
        segments = np.stack([trajectory[:-1], trajectory[1:]], axis=1)[adjacent]
        scaled = np.stack([segments[:, :, 0] * scales[0][0], segments[:, :, 1] * scales[1][0]], axis=2)
        lines = LineCollection(scaled, cmap="plasma", norm=time_norm, linewidths=1.5)
        lines.set_array(times[1:][adjacent])
        space.add_collection(lines)
        if marked is not None and marked.any():
            space.scatter(trajectory[marked][:, 0] * scales[0][0],
                          trajectory[marked][:, 1] * scales[1][0],
                          marker="s", s=30, c="tab:red", edgecolors="black", linewidths=.4, zorder=5)
        if space is spaces[0]:
            space.legend(fontsize=8)
            for position, label in ((0, "start"), (-1, "end")):
                space.annotate(label, (trajectory[position, 0] * scales[0][0],
                                       trajectory[position, 1] * scales[1][0]),
                               xytext=(6, 6), textcoords="offset points", weight="bold")
    fig.colorbar(lines, ax=list(spaces), label="Window end [s]", shrink=.8)
    for axis in fig.axes:
        if hasattr(axis, "grid"):
            axis.grid(alpha=.2)
    return fig
