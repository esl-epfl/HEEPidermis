"""BIOPAC-specific anti-alias resampling into the shared HEEPidermis EDA solver."""

from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly

from ...common.eda import ALGORITHMS, analyze
from .recording import moving_average


def analyze_recording(samples, average=1, smoothing=1.0, prominence=0.01, algorithm=ALGORITHMS[1]):
    times, tonic, phasic, identities, peaks = [], [], [], [], []
    _, signal, keys = moving_average(samples, average)
    if not samples:
        return {"times": times, "tonic": tonic, "phasic": phasic, "segments": identities, "peaks": peaks}
    signal, keys = np.asarray(signal), np.asarray(keys)
    boundaries = np.r_[0, np.flatnonzero(np.diff(keys)) + 1, len(keys)]
    identity = 0
    for first, last in zip(boundaries[:-1], boundaries[1:]):
        finite = np.isfinite(signal[first:last])
        edges = np.flatnonzero(np.diff(np.r_[False, finite, False])) + first
        for low, high in zip(edges[::2], edges[1::2]):
            if high - low < 3:
                continue
            # Samples are stored at the segment's output rate; reach the shared
            # 10 Hz analysis grid from there (identity at exactly 10 Hz).
            rate = round(samples[low].segment.settings.sample_rate)
            values = resample_poly(signal[low:high], 10, max(10, rate), padtype="line")
            grid = samples[low].time_s + np.arange(len(values)) / 10
            n = len(values)
            t, p, found = analyze(grid, values, np.zeros(n), algorithm, smoothing, prominence,
                                  sample_rates=np.full(n, 10.0), sources=np.full(n, "raw"))
            times.extend(grid.tolist())
            tonic.extend(t.tolist())
            phasic.extend(p.tolist())
            identities.extend([identity] * n)
            peaks.extend(grid[found].tolist())
            identity += 1
    return {"times": times, "tonic": tonic, "phasic": phasic, "segments": identities, "peaks": peaks}
