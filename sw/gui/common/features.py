"""Signal feature extraction for the workspace feature rows.

Peak detection reuses the shared smoothness-prior EDA analysis exactly as the
HEEPidermis GUI runs it (algorithm, smoothing λ, prominence; >=0.5 s width and
>=1 s separation at up to 10 Hz). Peak measures follow each response from its
preceding trough to the next trough.

All functions accept plain lists and return numpy arrays / plain dicts; they
never touch Tk or device panels.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

from .eda import ALGORITHMS, analyze

PEAK_WIDTH_S = 0.5      # Same minimum response width as the device GUI.
PEAK_SEPARATION_S = 1.0  # Same minimum separation as the device GUI.


def _analysis_rate(times) -> float:
    """Sample rate used for peak geometry; EDA analysis never exceeds 10 Hz."""
    if len(times) < 2:
        return 1.0
    median_dt = float(np.median(np.diff(times)))
    return min(10.0, max(0.1, 1.0 / max(median_dt, 1e-6)))


def detect_peaks(times, values, segments=None, is_phasic=False,
                 algorithm=ALGORITHMS[1], smoothing=1.0, prominence=0.01):
    """Return (peak_times, peak_values) for a conductance or phasic series.

    A conductance source runs the full tonic/phasic decomposition first (the
    same pipeline as the HEEPidermis GUI); a phasic source is already a
    residual, so peaks are found on it directly.
    """
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    if finite.sum() < 3:
        return np.array([]), np.array([])
    times, values = times[finite], values[finite]
    if segments is not None:
        segments = np.asarray(segments)[finite]
    else:
        segments = np.zeros(len(times), dtype=int)
    rate = _analysis_rate(times)
    geometry = dict(prominence=max(1e-9, float(prominence)),
                    height=max(1e-9, float(prominence)),
                    distance=max(1, round(rate * PEAK_SEPARATION_S)),
                    width=max(1, rate * PEAK_WIDTH_S))
    if is_phasic:
        locations, _ = find_peaks(values, **geometry)
    else:
        _, phasic, peaks = analyze(times, values, segments, algorithm,
                                   float(smoothing), float(prominence))
        locations = np.flatnonzero(peaks)
        if not len(locations):
            return np.array([]), np.array([])
        # Snap the analysis-grid peaks onto the strongest nearby sample.
        snapped = []
        for location in locations:
            first = np.searchsorted(times, times[location] - 0.5)
            last = np.searchsorted(times, times[location] + 0.5, side="right")
            snapped.append(first + int(np.argmax(values[first:last])))
        locations = np.asarray(snapped, dtype=int)
    if len(locations) > 1:
        # Enforce the separation on the (possibly re-snapped) locations.
        keep = [locations[0]]
        for index in locations[1:]:
            if times[index] - times[keep[-1]] >= PEAK_SEPARATION_S:
                keep.append(index)
        locations = np.asarray(keep, dtype=int)
    return times[locations], values[locations]


def peak_measures(times, values, peak_times, peak_values):
    """Per-peak response measures between neighbouring troughs.

    For each peak: the preceding trough (the lowest sample between the
    previous peak and this one) gives the peak-to-peak amplitude and the
    rising time; the following trough gives the falling time.
    """
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)
    indices = []
    for t in np.asarray(peak_times):
        index = min(len(times) - 1, int(np.searchsorted(times, t)))
        if index and abs(times[index - 1] - t) <= abs(times[index] - t):
            index -= 1
        indices.append(index)
    measures = []
    for position, index in enumerate(indices):
        previous = indices[position - 1] if position else 0
        rise_trough = previous + int(np.argmin(values[previous:index + 1]))
        following = indices[position + 1] if position + 1 < len(indices) else len(values) - 1
        fall_trough = index + int(np.argmin(values[index:following + 1]))
        measures.append({
            "time": float(times[index]),
            "value": float(peak_values[position]),
            "amplitude": float(values[index] - values[rise_trough]),
            "rise_s": float(times[index] - times[rise_trough]),
            "fall_s": float(times[fall_trough] - times[index]),
        })
    return measures
