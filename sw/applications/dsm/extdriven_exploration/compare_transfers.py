#In[]:
"""
CIC and SES decimation filter analysis.

Shared definitions (used identically in both filters):
  - fs        : input sample rate (Hz), e.g. 1e6 for a delta-sigma modulator
  - fbw_min   : lower edge of band of interest (Hz), used as droop reference
  - fbw_max   : upper edge of band of interest (Hz), sets f_nyq = 2*fbw_max
  - f_nyq     : Nyquist frequency = 2 * fbw_max  — droop is measured here,
                because this is the highest frequency that survives decimation
                without aliasing.  Alias zones are centred at k*fs/R.

Droop definition (gain-independent):
  droop_dB = 10 * log10( |H(f_nyq)|^2 / |H(fbw_min)|^2 )
  f_nyq = 2 * fbw_max is the band edge that matters: any frequency above it
  will alias back into the band after decimation by R.  Measuring droop at
  fbw_max instead would underestimate the distortion on the upper half of the
  usable band [fbw_max, f_nyq].
  Ideal = 0 dB. More negative = more attenuation at the Nyquist edge = worse.

Aliasing power (dBc):
  10 * log10( sum of |H(f)|^2 integrated over alias bands / in-band power )
  Alias bands: centred at k * fs/R, half-width f_nyq, for k = 1 .. R-1.
  In-band power: integral of |H(f)|^2 from fbw_min to f_nyq.

Cost (area proxy):
  CIC : (N + (1+D)*N/R) * ((2+D)*N)
  SES : N^2

SES stage gain convention:
  Stage 0 (first) : uses the supplied Wg value.
  Stages 1..N-1   : Wg = 0  (no extra gain word after the first stage).
  This reflects the physical implementation where only the input stage has
  the full gain scaling; subsequent smoothers operate on the already-scaled
  signal.
"""

import numpy as np
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Shared band / system parameters (pass these into both filter functions)
# ---------------------------------------------------------------------------

@dataclass
class BandSpec:
    """Defines the signal band and system clock. Independent of filter choice."""
    fs: float        # input sample rate (Hz)
    fbw_min: float   # lower band edge — droop reference (Hz)
    fbw_max: float   # upper band edge of interest (Hz); f_nyq = 2 * fbw_max

    @property
    def f_nyq(self):
        """Nyquist frequency: highest frequency that survives decimation."""
        return 2.0 * self.fbw_max

    def normalise(self, f_hz):
        """Convert Hz to normalised frequency (cycles per input sample)."""
        return f_hz / self.fs


# ---------------------------------------------------------------------------
# Internal shared helpers — same equations used for both filters
# ---------------------------------------------------------------------------

def _integrate_mag2(mag2_fn, f0_norm, f1_norm, n_pts=800):
    """
    Integrate mag2_fn(f_norm) over [f0_norm, f1_norm] using the midpoint rule.
    f_norm is normalised frequency in [0, 0.5].
    """
    if f1_norm <= f0_norm:
        return 0.0
    df = (f1_norm - f0_norm) / n_pts
    f = np.linspace(f0_norm + 0.5 * df, f1_norm - 0.5 * df, n_pts)
    return float(np.sum(mag2_fn(f)) * df)


def _droop_db(mag2_fn, fbw_min_norm, f_nyq_norm):
    """
    Droop = 10*log10( |H(f_nyq)|^2 / |H(fbw_min)|^2 ).

    The measurement point is f_nyq = 2*fbw_max, not fbw_max, because that is
    the highest frequency that survives decimation.  Any attenuation between
    fbw_min and f_nyq is genuine in-band distortion.

    Uses the two scalar endpoints — pure shape metric, integral-free.
    fbw_min_norm is the flat-region reference; f_nyq_norm is the band edge.
    """
    h2_ref  = mag2_fn(np.array([fbw_min_norm]))[0]
    h2_edge = mag2_fn(np.array([f_nyq_norm]))[0]
    if h2_ref <= 0 or h2_edge <= 0:
        return -np.inf
    return 10.0 * np.log10(h2_edge / h2_ref)


def _aliasing_dbc2(mag2_fn, band: BandSpec, R: int, n_pts_alias=200):

    """
    Computes the Total Signal-to-Aliasing Ratio.

    This integrates the power in EVERY frequency band that will fold back
    into the range [f_min, f_nyq] after decimation by R.
    """
    f_min_n = band.normalise(band.fbw_min)
    f_nyq_n = band.normalise(band.f_nyq)

    # 1. Total Power of the 'Useful' Signal (In-Band)
    p_signal = _integrate_mag2(mag2_fn, f_min_n, f_nyq_n)
    p_aliased = _integrate_mag2(mag2_fn, f_nyq_n, 2e6/R)

    # if p_signal <= 0:
    #     return -np.inf

    # # 2. Total Power of all Aliasing 'Mirrors'
    # # We check every harmonic k*fs/R that could fold into our band
    # p_aliased = 0.0
    # # The max number of harmonics is roughly R/2
    # num_harmonics = int(0.5 * R) + 1

    # for k in range(1, num_harmonics + 1):
    #     f_center = k / R

    #     # Lower side of the harmonic folding into our band
    #     # (f_center - f_nyq) folds to f_nyq
    #     # (f_center - f_min) folds to f_min
    #     a0_low = f_center - f_nyq_n
    #     a1_low = f_center - f_min_n

    #     # Upper side of the harmonic folding into our band
    #     a0_high = f_center + f_min_n
    #     a1_high = f_center + f_nyq_n

    #     # Integrate both "wings" that fold into the band
    #     # We clip to 0.5 (the original Nyquist)
    #     p_aliased += _integrate_mag2(mag2_fn, max(0, a0_low), min(0.5, a1_low))
    #     p_aliased += _integrate_mag2(mag2_fn, max(0, a0_high), min(0.5, a1_high))

    # if p_aliased <= 0:
    #     return 150.0 # Effectively no aliasing noise

    # We return Signal-to-Aliasing (higher is better)
    # or Aliasing-to-Signal (more negative is better)
    return 10.0 * np.log10(p_aliased / p_signal)



    """
    Aliasing power relative to in-band power (dBc).
    In-band  : integral of |H(f)|^2 from fbw_min to f_nyq.
    Alias sum : integral over bands [k/R - f_nyq/fs, k/R + f_nyq/fs]
                for k = 1 .. R-1, clipped to (0, 0.5).

    The half-width of each alias zone is f_nyq (= 2*fbw_max), consistent
    with the droop definition: the full usable band mirrors around each
    alias centre after decimation.
    """
    f_min_n  = band.normalise(band.fbw_min)
    f_nyq_n  = band.normalise(band.f_nyq)     # = 2 * fbw_max / fs
    half_bw  = f_nyq_n                        # alias zone half-width

    in_band = _integrate_mag2(mag2_fn, f_min_n, f_nyq_n)
    if in_band <= 0:
        return np.nan

    alias_sum = 0.0
    for k in range(1, R):
        fc = k / R                             # alias centre (normalised)
        a0 = max(fc - half_bw, f_min_n)
        a1 = min(fc + half_bw, 0.5 - 1e-9)
        alias_sum += _integrate_mag2(mag2_fn, a0, a1, n_pts=n_pts_alias)

    if alias_sum <= 0:
        return -np.inf
    return 10.0 * np.log10(alias_sum / in_band)

def _stopband_db(mag2_fn, band: BandSpec, R: int, n_pts=200):
    """
    Calculates the worst-case (peak) gain in the stopband relative to the passband.

    The range checked is from f_nyq (passband edge) to 1/R (first harmonic).
    For most low-pass filters, the 'worst' point is f_nyq, but for CICs
    with nulls, we want to see the rejection across the transition.
    """
    f_ref_n = band.normalise(band.fbw_min)
    f_start_n = band.normalise(band.f_nyq)
    f_end_n = 1.0 / R # The first harmonic (normalized)

    if f_end_n <= f_start_n:
        # If R is so small that f_nyq is past the first harmonic
        return 0.0

    # Sample the range to find the peak magnitude
    f_scan = np.linspace(f_start_n, f_end_n, n_pts)
    h2_ref = mag2_fn(np.array([f_ref_n]))[0]
    h2_stop_peak = np.max(mag2_fn(f_scan))

    if h2_ref <= 0 or h2_stop_peak <= 0:
        return -np.inf

    return 10.0 * np.log10(h2_stop_peak / h2_ref)


def _integrate_shaped_mag2(mag2_fn, f0_norm, f1_norm, order=3, n_pts=1000):
    """
    Integrate |H(f)|^2 weighted by the Delta-Sigma noise shaping curve.
    S_noise(f) = [2*sin(pi*f)]^(2*order)
    """
    if f1_norm <= f0_norm:
        return 0.0

    df = (f1_norm - f0_norm) / n_pts
    f = np.linspace(f0_norm + 0.5 * df, f1_norm - 0.5 * df, n_pts)

    # The Noise Shaping weight
    # As f -> 0.5 (fs/2), this value becomes very large.
    shaping_weight = (2.0 * np.sin(np.pi * f))**(2 * order)

    # Total weighted power
    weighted_power = np.sum(mag2_fn(f) * shaping_weight) * df
    return float(weighted_power)

def _aliasing_dbc(mag2_fn, band: BandSpec, R: int, modulator_order=3):
    """
    Computes the total aliased noise power relative to the in-band noise,
    accounting for the high-frequency noise shaping of a Delta-Sigma Modulator.
    """
    f_min_n = band.normalise(band.fbw_min)
    f_nyq_n = band.normalise(band.f_nyq)

    # 1. In-band noise (after filtering)
    # Even in-band, the noise isn't flat, but it's very low.
    p_in_band = _integrate_shaped_mag2(mag2_fn, f_min_n, f_nyq_n, order=modulator_order)

    p_off_band  = _integrate_shaped_mag2(mag2_fn, f_nyq_n, 1e6/R, order=modulator_order)


    # if p_in_band <= 0:
    #     return -np.inf

    # # 2. Total Aliased Power
    # # We sum the noise from every band that folds into [f_min, f_nyq]
    # p_aliased = 0.0
    # num_harmonics = int(0.5 * R) # Foldings up to fs/2

    # for k in range(1, num_harmonics + 1):
    #     fc = k / R

    #     # Every harmonic k*fs/R has a 'lower' and 'upper' wing that folds in
    #     # We integrate the shaped noise * filter_response in those specific wings
    #     wings = [
    #         (fc - f_nyq_n, fc - f_min_n), # Folds to [f_min, f_nyq]
    #         (fc + f_min_n, fc + f_nyq_n)  # Folds to [f_min, f_nyq]
    #     ]

    #     for w0, w1 in wings:
    #         # We only care about noise up to the Nyquist of the source (0.5)
    #         a0 = max(0.0, w0)
    #         a1 = min(0.5, w1)
    #         if a1 > a0:
    #             p_aliased += _integrate_shaped_mag2(mag2_fn, a0, a1, order=modulator_order)

    # Returns the ratio of aliased noise to in-band noise in dB
    # A positive value means your aliased noise floor is HIGHER than your signal floor.
    return 10.0 * np.log10(p_off_band / p_in_band)
# ---------------------------------------------------------------------------
# CIC filter
# ---------------------------------------------------------------------------

def cic_response(band: BandSpec, N: int, R: int, D: int):
    """
    Analyse a CIC decimation filter.

    Transfer function (magnitude squared):
      |H_CIC(f)|^2 = | sin(pi * f * R * D) / sin(pi * f) |^(2N)
    where f is normalised frequency (cycles per input sample).

    At f = 0 the limit gives (R*D)^(2N).

    Parameters
    ----------
    band : BandSpec
        Shared band and clock specification.
    N : int
        Number of stages (integrators = differentiators = N).
    R : int
        Decimation factor.
    D : int
        Differential delay (1 or 2 are typical).

    Returns
    -------
    dict with keys:
        droop_db      : float  — droop at f_nyq ref fbw_min (dB, ideal=0, more negative=worse)
        aliasing_dbc  : float  — alias power relative to in-band power (dBc)
        cost          : float  — area proxy = (N + (1+D)*N/R) * ((2+D)*N)
    """

    def mag2(f_norm):
        """Vectorised |H_CIC(f)|^2.  f_norm is a numpy array."""
        f = np.asarray(f_norm, dtype=float)
        pf_rd = np.pi * f * R * D
        pf    = np.pi * f
        # Use the limit RD at f->0 to avoid 0/0
        safe  = np.abs(f) > 1e-12
        result = np.where(
            safe,
            (np.abs(np.sin(pf_rd)) / np.abs(np.sin(pf))) ** (2 * N),
            float(R * D) ** (2 * N)
        )
        return result

    f_min_n = band.normalise(band.fbw_min)
    f_nyq_n = band.normalise(band.f_nyq)

    droop    = _droop_db(mag2, f_min_n, f_nyq_n)
    aliasing = _aliasing_dbc2(mag2, band, R)
    cost     = (N + (1 + D) * N / R) * ((2 + D) * N)

    stopband = _stopband_db(mag2, band, R)

    return {
        "stopband_db": stopband,
        "droop_db"     : droop,
        "aliasing_dbc" : aliasing,
        "cost"         : cost,
    }


# ---------------------------------------------------------------------------
# SES filter  (cascade of single-exponential smoothers)
# ---------------------------------------------------------------------------

def ses_response(band: BandSpec, N: int, R: int, Ww: int, Wg: int):
    """
    Analyse a cascade of N SES (single-exponential smoother) stages.

    Stage gain convention:
      - Stage 0 (first) : gain word = Wg  → per-stage gain factor 2^(Wg - Ww)
      - Stages 1..N-1   : gain word = 0   → per-stage gain factor 2^(0  - Ww) = 2^(-Ww)

    Each stage transfer function (magnitude squared):
      |H_i(f)|^2 = G_i^2 / (1 + alpha^2 - 2*alpha*cos(2*pi*f))
    where
      alpha = 1 - 2^(-Ww)          (pole, shared across all stages)
      G_0   = 2^(Wg - Ww)          (first stage gain)
      G_i   = 2^(0  - Ww) = 2^(-Ww)  for i = 1 .. N-1

    Combined magnitude squared (stages multiply):
      |H_SES(f)|^2 = |H_0(f)|^2 · |H_1(f)|^(2*(N-1))
                   = [G_0^2 / denom(f)] · [G_1^2 / denom(f)]^(N-1)
                   = G_0^2 · G_1^(2*(N-1)) / denom(f)^N

    where denom(f) = 1 + alpha^2 - 2*alpha*cos(2*pi*f).

    Note: in the droop ratio |H(f_nyq)|^2 / |H(fbw_min)|^2, the gain prefactor
    G_0^2 · G_1^(2*(N-1)) cancels completely, so droop depends only on Ww and N.
    Wg affects only absolute gain level, not shape.

    Parameters
    ----------
    band : BandSpec
        Shared band and clock specification.
    N : int
        Number of SES stages (>=1).
    R : int
        Decimation factor (used only for aliasing zone calculation).
    Ww : int
        Word width controlling the pole: alpha = 1 - 2^(-Ww).
        Approximate -3 dB point of a single stage: fs * 2^(-Ww) / (2*pi).
    Wg : int
        Gain word width of the first stage only.

    Returns
    -------
    dict with keys:
        droop_db      : float  — droop at f_nyq ref fbw_min (dB, ideal=0, more negative=worse)
        aliasing_dbc  : float  — alias power relative to in-band power (dBc)
        cost          : float  — area proxy = N^2
        pole_alpha    : float  — IIR pole location alpha
        f3db_hz       : float  — approximate -3 dB frequency of a single stage (Hz)
        gain_prefactor: float  — combined gain G_0^2 * G_1^(2*(N-1)) for reference
    """
    alpha  = 1.0 - 2.0 ** (-Ww)
    G0_sq  = 2.0 ** (2 * (Wg - Ww))              # first stage |gain|^2
    G1_sq  = 2.0 ** (2 * (0  - Ww))              # subsequent stages |gain|^2
    gain_prefactor = G0_sq * (G1_sq ** (N - 1))  # cancels in droop ratio

    def mag2(f_norm):
        """Vectorised |H_SES(f)|^2.  f_norm is a numpy array."""
        f = np.asarray(f_norm, dtype=float)
        theta = 2.0 * np.pi * f
        denom = 1.0 + alpha ** 2 - 2.0 * alpha * np.cos(theta)
        # denom(f)^N shared; gain prefactor applied once
        return gain_prefactor / (denom ** N)

    f_min_n = band.normalise(band.fbw_min)
    f_nyq_n = band.normalise(band.f_nyq)

    droop    = _droop_db(mag2, f_min_n, f_nyq_n)
    aliasing = _aliasing_dbc2(mag2, band, R)
    cost     = N ** 2

    # Approximate -3 dB point of a single stage (valid for alpha close to 1)
    f3db_hz = (1.0 - alpha) / (2.0 * np.pi) * band.fs

    stopband = _stopband_db(mag2, band, R)

    return {
        "droop_db"      : droop,
        "stopband_db"   : stopband,
        "aliasing_dbc"  : aliasing,
        "cost"          : cost,
        "pole_alpha"    : alpha,
        "f3db_hz"       : f3db_hz,
        "gain_prefactor": gain_prefactor,
    }


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------

# if __name__ == "__main__":
#     # Define system and band
#

#     print(f"System: fs={band.fs/1e3:.0f} kHz,  "
#           f"band [{band.fbw_min:.0f} Hz – {band.fbw_max/1e3:.1f} kHz],  "
#           f"f_nyq={band.f_nyq/1e3:.1f} kHz\n")

#     # --- CIC example sweep over R ---
#     print("=== CIC  (N=3, D=1, sweep R) ===")
#     print(f"{'R':>6}  {'droop (dB)':>12}  {'aliasing (dBc)':>16}  {'cost':>8}")
#     for R in [10, 20, 50, 100]:
#         res = cic_response(band, N=3, R=R, D=1)
#         print(f"{R:>6}  {res['droop_db']:>12.4f}  {res['aliasing_dbc']:>16.2f}  {res['cost']:>8.2f}")

#     print()

#     # --- SES example sweep over Ww ---
#     print("=== SES  (N=3, R=50, Wg=10, sweep Ww) ===")
#     print(f"{'Ww':>4}  {'pole α':>8}  {'f3dB (Hz)':>10}  {'droop (dB)':>12}  {'aliasing (dBc)':>16}  {'cost':>6}")
#     for Ww in [4, 5, 6, 8, 10, 12]:
#         res = ses_response(band, N=3, R=50, Ww=Ww, Wg=10)
#         print(f"{Ww:>4}  {res['pole_alpha']:>8.5f}  {res['f3db_hz']:>10.1f}  "
#               f"{res['droop_db']:>12.4f}  {res['aliasing_dbc']:>16.2f}  {res['cost']:>6.0f}")

#     print()

#     # --- Direct comparison at a single operating point ---
#     print("=== Head-to-head: CIC vs SES  (N=3, R=50) ===")
#     cic = cic_response(band, N=3, R=50, D=1)
#     ses = ses_response(band, N=3, R=50, Ww=6, Wg=10)
#     for key in ["droop_db", "aliasing_dbc", "cost"]:
#         print(f"  {key:20s}  CIC={cic[key]:10.3f}   SES={ses[key]:10.3f}")


#In[]:
import itertools
import matplotlib.pyplot as plt

band = BandSpec(
        fs      = 1e6,    # 1 MHz delta-sigma input rate
        fbw_min = 10.0,   # 10 Hz  — droop reference (flat region)
        fbw_max = 5e3,    # 5 kHz  — band edge, f_nyq = 10 kHz
    )

ses_wws = [4,5,6]
ses_wgs = [16]
ses_ns  = [4,5,6,7]
ses_rs  = [16, 32, 50, 64, 100]

cic_ds  = [ 1, 2, 3]
cic_ns  = ses_ns
cic_rs  = ses_rs


combinations_ses = itertools.product(ses_wws, ses_wgs, ses_ns, ses_rs)
combinations_cic = itertools.product(cic_ds, cic_ns, cic_rs)


plt.figure()
for ses_params in combinations_ses:
    ww, wg, n, r = ses_params
    res = ses_response(band, N=n, R=r, Ww=ww, Wg=wg)
    droop_dB        = res['droop_db']
    aliasing_dBc    = res['aliasing_dbc']
    cost            = res['cost']
    plt.scatter(aliasing_dBc, droop_dB, marker='o', color='green', alpha=(r/100))

plt.show()

#In[]:


import itertools
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib.colors as mcolors

# ---------------------------------------------------------------------------
# System definition
# ---------------------------------------------------------------------------
band = BandSpec(
    fs      = 1e6,
    fbw_min = 10.0,
    fbw_max = 5e3,
)

# ---------------------------------------------------------------------------
# Parameter grids
# ---------------------------------------------------------------------------
ses_wws = [4, 5, 6]
ses_wgs = [16]
ses_ns  = [4, 5, 6, 7]
ses_rs  = [16, 32, 50, 64, 100]

cic_ds  = [1, 2, 3]
cic_ns  = ses_ns
cic_rs  = ses_rs

# ---------------------------------------------------------------------------
# Run sweeps and collect results
# ---------------------------------------------------------------------------
ses_results, cic_results = [], []

for ww, wg, n, r in itertools.product(ses_wws, ses_wgs, ses_ns, ses_rs):
    res = ses_response(band, N=n, R=r, Ww=ww, Wg=wg)
    ses_results.append(dict(ww=ww, wg=wg, n=n, r=r, **res))

for d, n, r in itertools.product(cic_ds, cic_ns, cic_rs):
    res = cic_response(band, N=n, R=r, D=d)
    cic_results.append(dict(d=d, n=n, r=r, **res))

# ---------------------------------------------------------------------------
# Colormap: normalise cost across ALL points so scales are comparable
# ---------------------------------------------------------------------------
all_costs = [p['cost'] for p in ses_results + cic_results]
cost_min, cost_max = min(all_costs), max(all_costs)
norm = mcolors.Normalize(vmin=cost_min, vmax=cost_max)
cmap = cm.jet

# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(9, 6))

# --- SES: circle markers ---
for p in ses_results:
    ax.scatter(
        p['aliasing_dbc'], p['droop_db'],
        marker='o',
        color=cmap(norm(p['cost'])),
        s=60,
        edgecolors='none',
        alpha=0.85,
        zorder=2,
    )

# --- CIC: triangle markers ---
for p in cic_results:
    ax.scatter(
        p['aliasing_dbc'], p['droop_db'],
        marker='^',
        color=cmap(norm(p['cost'])),
        s=70,
        edgecolors='none',
        alpha=0.85,
        zorder=2,
    )

# --- Colorbar ---
sm = cm.ScalarMappable(cmap=cmap, norm=norm)
sm.set_array([])
cbar = fig.colorbar(sm, ax=ax, pad=0.02)
cbar.set_label('Cost (area proxy)', fontsize=11)

# --- Legend for marker shapes ---
ax.scatter([], [], marker='o', color='gray', s=60, label='SES')
ax.scatter([], [], marker='^', color='gray', s=70, label='CIC')
ax.legend(fontsize=10, framealpha=0.7)

# --- Labels ---
ax.set_xlabel('Aliasing power (dBc)  [more negative = better →]', fontsize=11)
ax.set_ylabel('Passband droop at f_nyq (dB)  [closer to 0 = better ↑]', fontsize=11)
ax.set_title(
    f'CIC vs SES — cost/distortion/aliasing tradeoff\n'
    f'fs={band.fs/1e3:.0f} kHz, band [{band.fbw_min:.0f} Hz – {band.fbw_max/1e3:.0f} kHz], '
    f'f_nyq={band.f_nyq/1e3:.0f} kHz',
    fontsize=11,
)
ax.grid(True, linestyle='--', alpha=0.4)

plt.tight_layout()
plt.ylim(-200,10)
plt.show()
print("Saved filter_sweep.png")


#In[]:

import itertools
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


# ---------------------------------------------------------------------------
# System definition
# ---------------------------------------------------------------------------
band = BandSpec(
    fs      = 1e6,
    fbw_min = 10.0,
    fbw_max = 5e3,
)

# ---------------------------------------------------------------------------
# Parameter grids
# ---------------------------------------------------------------------------
ses_wws = [2, 3, 4, 5]
ses_wgs = [16]
ses_ns  = [2, 3, 4, 5, 6]
ses_rs  = [4, 8, 16, 32]

cic_ds  = [1]
cic_ns  = ses_ns
cic_rs  = ses_rs

# ---------------------------------------------------------------------------
# Run sweeps and collect results
# ---------------------------------------------------------------------------
ses_results, cic_results = [], []

for ww, wg, n, r in itertools.product(ses_wws, ses_wgs, ses_ns, ses_rs):
    res = ses_response(band, N=n, R=r, Ww=ww, Wg=wg)
    ses_results.append(dict(ww=ww, wg=wg, n=n, r=r, **res))

for d, n, r in itertools.product(cic_ds, cic_ns, cic_rs):
    res = cic_response(band, N=n, R=r, D=d)
    cic_results.append(dict(d=d, n=n, r=r, **res))

# ---------------------------------------------------------------------------
# Data Transformation (DataFrames)
# ---------------------------------------------------------------------------
df_ses = pd.DataFrame(ses_results)
df_ses['Filter Type'] = 'SES'

df_cic = pd.DataFrame(cic_results)
df_cic['Filter Type'] = 'CIC'

# Combine dataframes
df = pd.concat([df_ses, df_cic], ignore_index=True)

# Calculate logarithmic cost for the color scale
df['log_cost'] = np.log10(df['cost'])

# ---------------------------------------------------------------------------
# Interactive Plotting
# ---------------------------------------------------------------------------

# Define hover data layout
hover_cols = {
    'Filter Type': True,
    'cost': ':.2f',
    'aliasing_dbc': ':.2f',
    'droop_db': ':.2f',
    'stopband_db': ':.2f',
    'n': True,
    'r': True,
    'ww': True,
    'wg': True,
    'd': True,
    'log_cost': False # Hide internal log column
}

title_str = (
    f'CIC vs SES<br>'
    f'fs={band.fs/1e3:.0f} kHz, band [{band.fbw_min:.0f} Hz – {band.fbw_max/1e3:.0f} kHz], '
    f'f_nyq={band.f_nyq/1e3:.0f} kHz'
)

# 1. Create the base scatter plot for markers
fig = px.scatter(
    df,
    x='aliasing_dbc',
    y='droop_db',
    color='log_cost',
    symbol='Filter Type',
    symbol_sequence=['circle', 'triangle-up'],
    hover_data=hover_cols,
    title=title_str,
    labels={
        'aliasing_dbc': 'Aliasing power (dBc)',
        'droop_db': 'Passband droop (dB)',
        'log_cost': 'log10(Cost)'
    },
    color_continuous_scale='Jet',
    width=700,
    height=500
)


# 3. Apply Axis limits and styling
fig.update_yaxes(
    range=[-150, 0.1], # Limit to -150 dB as requested
    showgrid=True,
    gridcolor='rgba(0,0,0,0.1)',
    zerolinecolor='rgba(0,0,0,0.2)'
)

fig.update_xaxes(
    autorange='reversed', # Typically better for dBc where "more negative" is on the right
    showgrid=True,
    gridcolor='rgba(0,0,0,0.1)'
)

fig.update_layout(
    plot_bgcolor='white',
    coloraxis_colorbar=dict(
        title='Cost (Log Scale)',
        tickvals=np.linspace(df['log_cost'].min(), df['log_cost'].max(), 5),
        ticktext=[f"{10**v:.1f}" for v in np.linspace(df['log_cost'].min(), df['log_cost'].max(), 5)]
    )
)

fig.update_traces(
    marker=dict(size=10, opacity=0.9, line=dict(width=0.5, color='white')),
    selector=dict(mode='markers') # Ensures lines aren't affected
)

# Save to HTML for sharing and display
# fig.write_html('filter_sweep_interactive.html')
fig.show()

# Export data for reference
# df.to_csv('filter_sweep_results.csv', index=False)


#In[]:

mod_order = 3

ses_n = 6
ses_r = 100
ses_ww= 3
ses_wg = 16

cic_n = 6
cic_r = 100
cic_d = 1

f_max_ses = band.fs / ses_r
f_ses = np.logspace(np.log10(band.fbw_min), np.log10(f_max_ses), 2000)
fn_ses = f_ses / band.fs
alpha = 1.0 - 2.0**(-ses_ww)
h_ses = ((1 - alpha) / np.sqrt(1 + alpha**2 - 2*alpha*np.cos(2*np.pi*fn_ses)))**ses_n
h_ses_db = 20 * np.log10(np.abs(h_ses))
ntf_ses_db = 20 * np.log10((2 * np.sin(np.pi * fn_ses))**mod_order)

# 2. Generate CIC Data (up to its fs/R)
f_max_cic = band.fs / cic_r
f_cic = np.logspace(np.log10(band.fbw_min), np.log10(f_max_cic), 2000)
fn_cic = f_cic / band.fs
pf_rd = np.pi * fn_cic * cic_r * cic_d
pf = np.pi * fn_cic
h_cic = np.where(fn_cic > 1e-12, (np.sin(pf_rd) / np.sin(pf))**cic_n, (cic_r*cic_d)**cic_n)
h_cic_db = 20 * np.log10(np.abs(h_cic) / (cic_r*cic_d)**cic_n)
ntf_cic_db = 20 * np.log10((2 * np.sin(np.pi * fn_cic))**mod_order)

# --- Plotting ---
plt.figure(figsize=(6, 3))

# Plot SES (Stops at its own fs/R)
plt.plot(f_ses/1e3, h_ses_db, label=f'SES (R={ses_r}, N={ses_n})', color='darkorange', linewidth=2)

# Plot CIC (Stops at its own fs/R)
plt.plot(f_cic/1e3, h_cic_db, label=f'CIC (R={cic_r}, N={cic_n})', color='tab:blue', linewidth=2)

# Formatting
plt.xscale('log')
plt.grid(True, which='both', alpha=0.3)
plt.ylim(-100, 10)
plt.xlabel("Frequency (kHz)")
plt.ylabel("Gain (dB)")

plt.axvline(10, linewidth=1, color='k', linestyle='--')
plt.axvline(15, linewidth=1, color='k', linestyle='--')
plt.axhline(-3, linewidth=1, color='k', linestyle='--')

# Set x-limit to the larger of the two max frequencies to fit both
plt.xlim(band.fbw_min/1e3, max(f_max_ses, f_max_cic)/1e2)

plt.legend(loc='lower left')
plt.tight_layout()
plt.show()


#In[]:

plt.figure(figsize=(6, 3))

# Plot SES (Stops at its own fs/R)
plt.plot(f_ses/1e3, h_ses_db, label=f'SES (R={ses_r}, N={ses_n})', color='darkorange', linewidth=2)

# Plot CIC (Stops at its own fs/R)
plt.plot(f_cic/1e3, h_cic_db, label=f'CIC (R={cic_r}, N={cic_n})', color='tab:blue', linewidth=2)

# Formatting
plt.grid(True, which='both', alpha=0.3)
plt.ylim(-40, 10)
plt.xlabel("Frequency (kHz)")
plt.ylabel("Gain (dB)")

plt.axvline(10, linewidth=1, color='k', linestyle='--')
plt.axhline(-3, linewidth=1, color='k', linestyle='--')

# Set x-limit to the larger of the two max frequencies to fit both
plt.xlim(band.fbw_min/1e3, 40)

plt.legend(loc='lower left')
plt.tight_layout()
plt.show()