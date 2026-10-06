"""Python counterparts of Blanca's smoothness-prior EDA decomposition.

Source: https://github.com/Blanca-c-m/x-heep/tree/main/sw/applications/tfg_blanca
All three solvers implement (I + lambda**2 D2.T D2) tonic = conductance.
Floating point replaces the MCU versions' fixed-point approximations.
"""

from functools import lru_cache

import numpy as np
from scipy.linalg import cholesky_banded, cho_solve_banded, solve_banded
from scipy.signal import find_peaks

ALGORITHMS = ("Blanca v1 · banded elimination", "Blanca v3/v5 · cached factorization",
              "Blanca · inverse matrix")


def diagonals(size, smoothing):
    """Five diagonals, including exact boundary terms for even short signals."""
    penalty = float(smoothing) ** 2
    band = np.zeros((5, size))
    band[2] = 1.0
    for offset in range(max(0, size - 2)):
        for i, a in enumerate((1., -2., 1.)):
            for j, b in enumerate((1., -2., 1.)):
                band[2 + i - j, offset + j] += penalty * a * b
    return band


@lru_cache(maxsize=16)
def factorization(size, smoothing):
    band = diagonals(size, smoothing)
    return cholesky_banded(band[2:], lower=True)


@lru_cache(maxsize=4)
def inverse_matrix(size, smoothing):
    band = diagonals(size, smoothing)
    # Build the inverse by solving each identity column, as descompinversa.c
    # assumes an inverse already exists. Bounded blocks keep this mode cheap.
    return solve_banded((2, 2), band, np.eye(size))


def decompose(values, algorithm=ALGORITHMS[1], smoothing=1.0):
    values = np.asarray(values, dtype=float)
    if values.size < 3:
        return values.copy(), np.zeros_like(values)
    if algorithm == ALGORITHMS[0]:
        tonic = solve_banded((2, 2), diagonals(len(values), smoothing), values)
    elif algorithm == ALGORITHMS[1]:
        tonic = cho_solve_banded((factorization(len(values), smoothing), True), values)
    elif algorithm == ALGORITHMS[2]:
        tonic = inverse_matrix(len(values), smoothing).dot(values)
    else:
        raise ValueError("Unknown decomposition algorithm")
    return tonic, values - tonic


def analyze(times, values, segments, algorithm, smoothing, prominence, sample_rates=None, sources=None):
    """Analyze uniform time grids without bridging pauses, gaps or settings changes.

    EDA analysis uses at most 10 Hz. dLC's irregular samples use zero-order
    hold; repeated timestamps from split crossing packets retain the final level.
    Returned arrays match the original samples, including invalid conductances.
    Peak detection uses SciPy prominence, >=0.5 s width and >=1 s separation.
    Live estimates near the right boundary can change as data arrives.
    """
    times, values = np.asarray(times), np.asarray(values, dtype=float)
    tonic = np.full(len(times), np.nan)
    phasic = tonic.copy()
    peaks = np.zeros(len(times), dtype=bool)
    if not len(times):
        return tonic, phasic, peaks
    boundaries = np.r_[0, np.flatnonzero(np.diff(segments)) + 1, len(times)]
    block_limit = 256 if algorithm == ALGORITHMS[2] else 2048
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        # Invalid values also terminate an analysis interval.
        valid = np.isfinite(values[start:end])
        edges = np.flatnonzero(np.diff(np.r_[False, valid, False])) + start
        for first, last in zip(edges[::2], edges[1::2]):
            t, y = times[first:last], values[first:last]
            unique = np.r_[np.flatnonzero(np.diff(t)), len(t) - 1]
            t, y = t[unique], y[unique]
            if len(t) < 3 or t[-1] <= t[0]:
                tonic[first:last] = values[first:last]
                phasic[first:last] = 0.0
                continue
            rate = (min(10.0, max(0.1, float(sample_rates[first]))) if sample_rates is not None else
                    min(10.0, 1.0 / max(float(np.median(np.diff(t))), 0.1)))
            grid = np.linspace(t[0], t[-1], max(3, int((t[-1] - t[0]) * rate) + 1))
            rate = (len(grid) - 1) / (grid[-1] - grid[0])
            if sources is not None and sources[first] == "raw":
                uniform = np.interp(grid, t, y)
            else:
                uniform = y[np.clip(np.searchsorted(t, grid+1e-9, side="right")-1, 0, len(y)-1)]
            base = np.empty(len(grid))
            # Overlapping blocks avoid a new filter edge at each block boundary.
            for block in range(0, len(grid), max(1, block_limit - 64)):
                low, high = max(0, block - 32), min(len(grid), block + block_limit - 32)
                result, _ = decompose(uniform[low:high], algorithm, smoothing)
                take_end = min(len(grid), block + block_limit - 64)
                base[block:take_end] = result[block-low:take_end-low]
            residual = uniform - base
            locations, _ = find_peaks(residual, prominence=prominence, height=prominence,
                                      distance=max(1, round(rate)), width=max(1, rate * 0.5))
            tonic[first:last] = np.interp(times[first:last], grid, base)
            phasic[first:last] = values[first:last] - tonic[first:last]
            for location in locations:
                index = first + int(np.argmin(abs(times[first:last] - grid[location])))
                peaks[index] = True
    return tonic, phasic, peaks
