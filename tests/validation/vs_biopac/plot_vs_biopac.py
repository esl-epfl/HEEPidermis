#!/usr/bin/env python3
"""Comparison of the HEEPidERmis on-skin conductance against the Biopac reference.

Inputs (conductance only):
  * sw/gui/outs/2026_11_13_1858_vs_biopac.csv      (HEEPidermis, 10 Hz)
  * sw/biopac/outs/biopac_vs_heepidermis.csv       (Biopac,     100 Hz)

Plot layout (3 rows):
  1. Both raw conductances, time-aligned (each trace zeroed at its own start).
  2. The same two conductances, each normalized to its own range. The range
     min/max is computed after outlier exclusion (1.5*IQR fences), so isolated
     spikes do not squash the scaling; in-range data is preserved as-is and
     only the limits are robust.
  3. Pearson correlation of the two conductances in 10 s windows.

The recordings were not started at the same time, so the figure has a lag
slider: dragging it displaces the Biopac trace in time with respect to the
HEEPidERmis trace, and the normalization plus the windowed Pearson
correlation are recomputed live so the alignment improvement is visible.

Usage:
  Either run the whole script from a terminal:

    python3 tests/validation/vs_biopac/plot_vs_biopac.py [--show] [-o OUT.png]

  or execute it cell by cell (Spyder / VS Code, cells marked with `#In[]:`):
    Cell 1 = initialization (loads the data into variables you can inspect),
    Cell 2 = plotting (tweak the knobs at the top, then run it; the slider
    only works in an interactive backend, e.g. "%matplotlib widget").
"""

#In[]:
#  ==========================================================================
# Cell 1 — Initialization: imports, constants, helpers and data loading.
# Run this cell first; afterwards all variables (heep, biopac, t_heep, ...)
# are available for inspection and the plotting cell can be run / re-run.
# ================================================================================
import argparse
from pathlib import Path

import matplotlib

# Pop up real figure windows when running cell by cell; use the headless
# backend when running the whole file as a plain script.
try:
    get_ipython()
    IN_INTERACTIVE_CELL = True
except NameError:
    IN_INTERACTIVE_CELL = False

# Use the interactive widget backend when running cell by cell (Jupyter magic,
# expressed in plain Python so the file also stays runnable as a script).
if IN_INTERACTIVE_CELL:
    get_ipython().run_line_magic("matplotlib", "widget")

if not IN_INTERACTIVE_CELL:
    matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.widgets import Slider

REPO_ROOT = Path(__file__).resolve().parents[3]

HEEP_CSV = REPO_ROOT / "sw" / "gui" / "outs" / "2026_11_13_1858_vs_biopac.csv"
# The Biopac export moved from sw/biopac/outs to sw/gui/outs/biopac; accept both.
_BIO_CSV_CANDIDATES = [
    REPO_ROOT / "sw" / "gui" / "outs" / "biopac" / "biopac_vs_heepidermis.csv",
    REPO_ROOT / "sw" / "biopac" / "outs" / "biopac_vs_heepidermis.csv",
]
BIO_CSV = next((p for p in _BIO_CSV_CANDIDATES if p.exists()), _BIO_CSV_CANDIDATES[0])

DEFAULT_OUT = Path(__file__).resolve().parent / "vs_biopac_comparison.png"

# Resampling grid for the windowed correlation (limited by the 10 Hz device).
COMMON_FS_HZ = 10.0
# Correlation window length.
WINDOW_S = 10.0

COL_HEEP_TIME = "time_s"
COL_HEEP_COND = "conductance_uS"
COL_BIO_TIME = "time_s"
COL_BIO_COND = "conductance_us_nominal"


def load_heep(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "valid" in df.columns:
        df = df[df["valid"] == 1]
    df = df[[COL_HEEP_TIME, COL_HEEP_COND]].dropna()
    df = df.sort_values(COL_HEEP_TIME)
    return df.rename(columns={COL_HEEP_COND: "heep"})


def load_biopac(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[[COL_BIO_TIME, COL_BIO_COND]].dropna()
    df = df.sort_values(COL_BIO_TIME)
    return df.rename(columns={COL_BIO_COND: "biopac"})


def robust_minmax(x: np.ndarray) -> tuple[float, float]:
    """Min/max of the data after excluding outliers (1.5*IQR fences)."""
    q1, q3 = np.percentile(x, [25.0, 75.0])
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    inliers = x[(x >= lo) & (x <= hi)]
    if inliers.size == 0:  # degenerate fall-back
        inliers = x
    return float(np.min(inliers)), float(np.max(inliers))


def normalize(x: np.ndarray) -> np.ndarray:
    """Scale to [0, 1] using the outlier-free range; out-of-range clips."""
    lo, hi = robust_minmax(x)
    if hi <= lo:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def windowed_correlation(t1, y1, t2, y2, fs=COMMON_FS_HZ, win_s=WINDOW_S):
    """Pearson r per `win_s` window on a common time grid."""
    t0 = max(t1[0], t2[0])
    t_end = min(t1[-1], t2[-1])
    if t_end <= t0:
        raise ValueError("No time overlap between the two recordings.")

    grid = np.arange(t0, t_end, 1.0 / fs)
    a = np.interp(grid, t1, y1)
    b = np.interp(grid, t2, y2)

    centers, rs = [], []
    step = int(round(win_s * fs))
    for start in range(0, len(grid) - step + 1, step):
        wa, wb = a[start : start + step], b[start : start + step]
        wa, wb = wa - wa.mean(), wb - wb.mean()
        denom = np.sqrt(np.sum(wa * wa) * np.sum(wb * wb))
        if denom == 0:
            continue
        rs.append(np.sum(wa * wb) / denom)
        centers.append(0.5 * (grid[start] + grid[start + step - 1]))
    return np.asarray(centers), np.asarray(rs)


# --- Load the data and precompute everything the plots need -------------------
heep = load_heep(HEEP_CSV)
biopac = load_biopac(BIO_CSV)

# Time alignment: zero each trace at its own recording start.
t_heep = heep[COL_HEEP_TIME].to_numpy() - heep[COL_HEEP_TIME].iloc[0]
t_bio = biopac[COL_BIO_TIME].to_numpy() - biopac[COL_BIO_TIME].iloc[0]
g_heep = heep["heep"].to_numpy()
g_bio = biopac["biopac"].to_numpy()

n_heep, n_bio = normalize(g_heep), normalize(g_bio)
centers, rs = windowed_correlation(t_heep, g_heep, t_bio, g_bio)

duration = max(t_heep[-1], t_bio[-1])

#In[]:
# ==========================================================================
# Cell 2 — Plotting with an interactive lag slider. Run this after Cell 1;
# re-run freely. Drag the slider to displace the Biopac trace in time with
# respect to the HEEPidERmis trace: the normalization and the windowed
# Pearson correlation are recomputed live so you can see the improvement.
# ================================================================================

OUTPUT_PNG = DEFAULT_OUT  # where the PNG is written (script mode)
SHOW_FIGURE = False       # set True to also open the figure window (cell mode)
INITIAL_SHIFT_S = 0.0     # initial lag (s) applied to the Biopac trace
SLIDER_STEP_S = 0.1       # slider resolution (s)


def render(shift_s: float):
    """Build the 3-row figure with the Biopac trace shifted by `shift_s`.

    Returns (fig, slider). Dragging the slider re-shifts the Biopac trace and
    recomputes the normalization and the windowed Pearson correlation.
    """
    t_bio_s = t_bio + shift_s
    n_heep_s, n_bio_s = normalize(g_heep), normalize(g_bio)
    centers_s, rs_s = windowed_correlation(t_heep, g_heep, t_bio_s, g_bio)

    fig, (ax_raw, ax_norm, ax_corr) = plt.subplots(
        3, 1, figsize=(14, 10), sharex=True, gridspec_kw={"height_ratios": [1.2, 1.2, 1.0]}
    )
    # Leave room for the slider underneath the axes.
    fig.subplots_adjust(bottom=0.12)

    # Row 1: raw conductances.
    line_bio_raw, = ax_raw.plot(t_bio_s, g_bio, color="tab:red", lw=1.0, label="Biopac (100 Hz)")
    ax_raw.plot(t_heep, g_heep, color="tab:blue", lw=1.2, label="HEEPidermis (10 Hz)")
    ax_raw.set_ylabel("Conductance (µS)")
    ax_raw.set_title(f"Raw conductance — HEEPidERmis vs Biopac ({duration:.0f} s, time-aligned)")
    ax_raw.legend(loc="upper right")
    ax_raw.grid(alpha=0.3)

    # Row 2: per-signal normalized conductances (robust range).
    line_bio_norm, = ax_norm.plot(t_bio_s, n_bio_s, color="tab:red", lw=1.0, label="Biopac")
    ax_norm.plot(t_heep, n_heep_s, color="tab:blue", lw=1.2, label="HEEPidermis")
    ax_norm.set_ylabel("Normalized conductance (–)")
    ax_norm.set_ylim(-0.05, 1.05)
    ax_norm.set_title("Normalized conductance (each to its outlier-free range)")
    ax_norm.legend(loc="upper right")
    ax_norm.grid(alpha=0.3)

    # Row 3: 10 s windowed Pearson correlation.
    def draw_corr(centers, rs) -> None:
        ax_corr.clear()
        ax_corr.bar(centers, rs, width=0.9 * WINDOW_S, color="tab:green", alpha=0.8)
        ax_corr.axhline(0.0, color="k", lw=0.8)
        ax_corr.set_ylim(-1.05, 1.05)
        ax_corr.set_ylabel("Pearson r")
        ax_corr.set_xlabel("Time (s)")
        mean_r = rs.mean() if rs.size else float("nan")
        ax_corr.set_title(f"Correlation in {WINDOW_S:.0f} s windows (mean r = {mean_r:.2f})")
        ax_corr.grid(alpha=0.3)

    draw_corr(centers_s, rs_s)

    # Lag slider: displace the Biopac trace (positive = Biopac shifted later).
    # Keep enough overlap for at least one correlation window at either end.
    max_shift = float(max(min(t_heep[-1], t_bio[-1]) - 2.0 * WINDOW_S, 0.0))
    ax_slide = fig.add_axes([0.15, 0.03, 0.70, 0.02])
    slider = Slider(
        ax_slide, "Biopac lag (s)", -max_shift, max_shift,
        valinit=shift_s, valstep=SLIDER_STEP_S,
    )

    def update(val: float) -> None:
        s = slider.val
        t_bio_new = t_bio + s
        # Recompute normalization and correlation for this lag. (The per-signal
        # outlier-free range does not depend on the time shift, so the
        # normalized curves only move horizontally, but they are recomputed
        # here so the row always reflects the current alignment.)
        n_bio_new = normalize(g_bio)
        n_heep_new = normalize(g_heep)
        try:
            centers_new, rs_new = windowed_correlation(t_heep, g_heep, t_bio_new, g_bio)
        except ValueError:
            return  # no time overlap left at this lag
        line_bio_raw.set_xdata(t_bio_new)
        line_bio_norm.set_data(t_bio_new, n_bio_new)
        ax_norm.lines[1].set_data(t_heep, n_heep_new)
        draw_corr(centers_new, rs_new)
        print(f"lag {s:+.1f} s → Pearson r per {WINDOW_S:.0f} s window: "
              f"mean {rs_new.mean():.3f}, min {rs_new.min():.3f}, max {rs_new.max():.3f}")
        fig.canvas.draw_idle()

    slider.on_changed(update)
    return fig, slider


fig, slider = render(INITIAL_SHIFT_S)

if IN_INTERACTIVE_CELL:
    if SHOW_FIGURE:
        plt.show()
elif __name__ == "__main__":
    # Headless script mode: the slider has no event loop here, so just save
    # the figure for the lag set by INITIAL_SHIFT_S.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT, help="Output PNG path")
    parser.add_argument("--show", action="store_true", help="Also open the figure in a window")
    _args = parser.parse_args()
    fig.savefig(_args.output, dpi=200)
    print(f"Wrote {_args.output}")
    print(f"Pearson r per {WINDOW_S:.0f} s window: mean {rs.mean():.3f}, "
          f"min {rs.min():.3f}, max {rs.max():.3f}")

    if _args.show:
        plt.show()

#In[]:
# ==========================================================================
# Cell 3 — Refined, artifact-aware alignment. Run after Cells 1 and 2.
#
# * Starts from the believed alignment (LAG_BELIEVED_S) with a small 2 s
#   correlation window, then runs a coarse-to-fine loop that maximizes
#   mu - 3*sigma of the per-window Pearson r (robust against outlier windows).
# * Windows with r below R_BAD are treated as movement artifacts: they are
#   excluded from mu/sigma, from the normalization range, and are drawn as
#   dashed line segments instead of contiguous ones.
# * Figure: top = the two aligned (artifact-aware normalized) traces with the
#   background faded red (low r) -> green (high r) per window; bottom = the
#   per-window correlation bars. Sliders (lag is fine-grained at 10 ms and
#   covers only the neighbourhood of the best alignment, far pieces are simply
#   discarded) recompute everything live and show mu / sigma in real time.
# ================================================================================

LAG_BELIEVED_S = -248.1    # user-estimated correct alignment
WIN_S3 = 2.0               # correlation window for this refined analysis (s)
R_BAD = 0.30               # windows with r below this are movement artifacts
SLIDER_LAG_SPAN_S = 30.0   # lag slider covers best_lag +/- this (fine step)
OUTPUT_PNG3 = Path(__file__).resolve().parent / "vs_biopac_alignment_search.png"

CMAP = plt.get_cmap("RdYlGn")  # red = low r, green = high r


def analyze(lag_s: float, win_s: float, r_bad: float):
    """Windowed correlation + artifact-aware stats for one configuration.

    Both traces are resampled onto a common `COMMON_FS_HZ` grid with the
    Biopac trace shifted by `lag_s`. Windows with r < `r_bad` are considered
    movement artifacts and excluded from the statistics and from the
    normalization range. Returns None if there is not enough usable overlap.
    """
    t_bio_s = t_bio + lag_s
    t_lo = max(t_heep[0], t_bio_s[0])
    t_hi = min(t_heep[-1], t_bio_s[-1])
    if t_hi <= t_lo:
        return None
    grid = np.arange(t_lo, t_hi, 1.0 / COMMON_FS_HZ)
    a = np.interp(grid, t_heep, g_heep)  # HEEPidERmis on the common grid
    b = np.interp(grid, t_bio_s, g_bio)  # shifted Biopac on the common grid

    step = int(round(win_s * COMMON_FS_HZ))
    if step < 3 or len(grid) < step:
        return None
    starts, rs = [], []
    for start in range(0, len(grid) - step + 1, step):
        wa = a[start : start + step] - a[start : start + step].mean()
        wb = b[start : start + step] - b[start : start + step].mean()
        denom = np.sqrt(np.sum(wa * wa) * np.sum(wb * wb))
        if denom == 0:
            continue
        rs.append(np.sum(wa * wb) / denom)
        starts.append(grid[start])
    rs = np.asarray(rs)
    if rs.size < 3:
        return None
    spans = [(s, s + step / COMMON_FS_HZ) for s in starts]
    centers = np.asarray([0.5 * (s + e) for s, e in spans])

    good = rs >= r_bad  # False => movement artifact, ignored everywhere below
    if good.sum() < 3:
        return None
    mu = float(rs[good].mean())
    sigma = float(rs[good].std())
    obj = mu - 3.0 * sigma

    # Re-normalize using only the samples inside non-artifact windows.
    n_full = len(spans)
    win_of_sample = np.arange(len(grid)) // step
    good_sample = np.zeros(len(grid), dtype=bool)
    in_full = win_of_sample < n_full
    good_sample[in_full] = good[win_of_sample[in_full]]
    lo_a, hi_a = robust_minmax(a[good_sample])
    lo_b, hi_b = robust_minmax(b[good_sample])
    na = np.clip((a - lo_a) / (hi_a - lo_a), 0.0, 1.0) if hi_a > lo_a else np.zeros_like(a)
    nb = np.clip((b - lo_b) / (hi_b - lo_b), 0.0, 1.0) if hi_b > lo_b else np.zeros_like(b)

    return {
        "lag": float(lag_s), "win_s": float(win_s), "r_bad": float(r_bad),
        "grid": grid, "a": a, "b": b, "na": na, "nb": nb,
        "spans": spans, "centers": centers, "rs": rs, "good": good,
        "mu": mu, "sigma": sigma, "obj": obj, "overlap": float(t_hi - t_lo),
    }


def scan(lags, win_s: float, r_bad: float, min_good_frac: float = 0.3,
         min_overlap_frac: float = 0.6):
    """Evaluate every lag and return the config maximizing mu - 3*sigma.

    Rejected: configurations where fewer than `min_good_frac` of the windows
    survive the artifact filter, or where the time overlap shrinks below
    `min_overlap_frac` of the maximum possible overlap (otherwise a tiny,
    cherry-picked overlap would trivially maximize the objective).
    """
    max_overlap = float(min(t_heep[-1], t_bio[-1]) - max(t_heep[0], t_bio[0]))
    min_overlap = max(min_overlap_frac * max_overlap, 30.0)
    best = None
    for lag in lags:
        cfg = analyze(lag, win_s, r_bad)
        if cfg is None or cfg["overlap"] < min_overlap:
            continue
        if cfg["good"].sum() < min_good_frac * cfg["rs"].size:
            continue
        if best is None or cfg["obj"] > best["obj"]:
            best = cfg
    return best


# --- Small search loop for the best alignment (2 s windows) -------------------
lag_span = float(max(min(t_heep[-1], t_bio[-1]) - 2.0 * WIN_S3, 0.0))

cfg_believed = analyze(LAG_BELIEVED_S, WIN_S3, R_BAD)
if cfg_believed is not None:
    print(f"believed lag {LAG_BELIEVED_S:+.2f} s → mu {cfg_believed['mu']:.3f}, "
          f"sigma {cfg_believed['sigma']:.3f}, mu-3sigma {cfg_believed['obj']:.3f}, "
          f"artifacts {int((~cfg_believed['good']).sum())}/{cfg_believed['rs'].size}")

best = scan(np.arange(-lag_span, lag_span + 1e-9, 0.5), WIN_S3, R_BAD)  # coarse: 0.5 s
if best is not None:
    print(f"coarse best  {best['lag']:+.2f} s → mu-3sigma {best['obj']:.3f}")
    best = scan(
        np.arange(best["lag"] - 0.5, best["lag"] + 0.5 + 1e-9, 0.01), WIN_S3, R_BAD
    )  # fine: 10 ms
    print(f"fine best    {best['lag']:+.2f} s → mu {best['mu']:.3f}, "
          f"sigma {best['sigma']:.3f}, mu-3sigma {best['obj']:.3f}, "
          f"artifacts {int((~best['good']).sum())}/{best['rs'].size}")
if best is None:  # search failed: fall back to the believed alignment
    best = cfg_believed
if best is None:
    raise RuntimeError("No usable alignment found; check the input data.")

# --- Interactive two-row figure ------------------------------------------------
fig3, (ax_top, ax_bot) = plt.subplots(
    2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [1.6, 1.0]}
)
fig3.subplots_adjust(bottom=0.24, hspace=0.12)


def plot_segments(ax, t, y, cfg, color) -> None:
    """Draw y(t) per correlation window: solid where good, dashed on artifacts."""
    for (ts, te), is_good in zip(cfg["spans"], cfg["good"]):
        m = (t >= ts) & (t <= te)
        if m.sum() < 2:
            continue
        ax.plot(t[m], y[m], color=color, lw=1.2, ls="-" if is_good else "--")


def draw(cfg) -> None:
    ax_top.clear()
    # Background: faded gradient, red (low r) -> green (high r), per window.
    for (ts, te), r in zip(cfg["spans"], cfg["rs"]):
        ax_top.axvspan(ts, te, color=CMAP(r), alpha=0.15, lw=0)
    plot_segments(ax_top, cfg["grid"], cfg["na"], cfg, "tab:blue")
    plot_segments(ax_top, cfg["grid"], cfg["nb"], cfg, "tab:red")
    ax_top.set_ylim(-0.05, 1.05)
    ax_top.set_ylabel("Normalized conductance (–)")
    ax_top.legend(
        handles=[
            Line2D([], [], color="tab:blue", lw=1.2, label="HEEPidermis (norm)"),
            Line2D([], [], color="tab:red", lw=1.2, label="Biopac (norm, shifted)"),
            Line2D([], [], color="0.4", lw=1.2, ls="--", label="movement artifact"),
        ],
        loc="upper right",
    )
    ax_top.grid(alpha=0.3)
    ax_top.set_title(
        f"Aligned conductances — lag {cfg['lag']:+.2f} s, window {cfg['win_s']:.1f} s   |   "
        f"mu {cfg['mu']:.3f}   sigma {cfg['sigma']:.3f}   mu-3sigma {cfg['obj']:.3f}   |   "
        f"artifacts excluded {int((~cfg['good']).sum())}/{cfg['rs'].size}"
    )

    ax_bot.clear()
    ax_bot.bar(cfg["centers"], cfg["rs"], width=0.9 * cfg["win_s"],
               color=CMAP(cfg["rs"]), alpha=0.9)
    ax_bot.axhline(0.0, color="k", lw=0.8)
    ax_bot.axhline(cfg["mu"], color="k", lw=1.0)
    ax_bot.axhline(cfg["mu"] - 3 * cfg["sigma"], color="k", lw=0.8, ls=":")
    ax_bot.axhline(cfg["mu"] + 3 * cfg["sigma"], color="k", lw=0.8, ls=":")
    ax_bot.set_ylim(-1.05, 1.05)
    ax_bot.set_ylabel("Pearson r")
    ax_bot.set_xlabel("Time (s)")
    ax_bot.grid(alpha=0.3)


# Sliders: fine lag around the best alignment, window size, artifact threshold.
ax_lag = fig3.add_axes([0.15, 0.15, 0.70, 0.02])
ax_win = fig3.add_axes([0.15, 0.10, 0.70, 0.02])
ax_bad = fig3.add_axes([0.15, 0.05, 0.70, 0.02])
slider_lag = Slider(
    ax_lag, "Lag (s)",
    best["lag"] - SLIDER_LAG_SPAN_S, best["lag"] + SLIDER_LAG_SPAN_S,
    valinit=best["lag"], valstep=0.01,
)
slider_win = Slider(ax_win, "Window (s)", 0.5, 10.0, valinit=WIN_S3, valstep=0.5)
slider_bad = Slider(ax_bad, "Artifact r <", 0.0, 0.9, valinit=R_BAD, valstep=0.05)


def refresh(_=None) -> None:
    cfg = analyze(slider_lag.val, slider_win.val, slider_bad.val)
    if cfg is None:
        return  # not enough overlap / good windows at this configuration
    draw(cfg)
    print(f"lag {slider_lag.val:+.2f} s, window {slider_win.val:.1f} s, "
          f"r_bad {slider_bad.val:.2f} → mu {cfg['mu']:.3f}, "
          f"sigma {cfg['sigma']:.3f}, mu-3sigma {cfg['obj']:.3f}, "
          f"artifacts {int((~cfg['good']).sum())}/{cfg['rs'].size}")
    fig3.canvas.draw_idle()


for s in (slider_lag, slider_win, slider_bad):
    s.on_changed(refresh)

draw(best)  # initial render at the best alignment found

if IN_INTERACTIVE_CELL:
    if SHOW_FIGURE:
        plt.show()
elif __name__ == "__main__":
    # Headless script mode: save the figure at the best alignment found.
    fig3.savefig(OUTPUT_PNG3, dpi=200)
    print(f"Wrote {OUTPUT_PNG3}")
