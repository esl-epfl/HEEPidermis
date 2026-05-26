#!/usr/bin/env python3
"""
Plot gt_fe_output.csv over a selectable sample range.

Usage:
  python3 plot_gt_fe_output.py [--start N] [--end N]

  --start N   first sample index to plot (default: 0)
  --end   N   last sample index to plot  (default: last sample)
"""

import sys
import pandas as pd
import matplotlib.pyplot as plt

start = 0
end   = 1000000

args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] in ('--start', '-s') and i + 1 < len(args):
        start = int(args[i + 1]); i += 2
    elif args[i].startswith('--start='):
        start = int(args[i].split('=', 1)[1]); i += 1
    elif args[i] in ('--end', '-e') and i + 1 < len(args):
        end = int(args[i + 1]); i += 2
    elif args[i].startswith('--end='):
        end = int(args[i].split('=', 1)[1]); i += 1
    else:
        i += 1

df = pd.read_csv("gt_fe_output.csv", comment="#")
df = df.iloc[start:end] if end is not None else df.iloc[start:]

fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True)
title = f"GSR Ground Truth FE Output  |  samples {df['idx'].iloc[0]} - {df['idx'].iloc[-1]}"

axes[0].plot(df["idx"], df["signal_nS"], linewidth=0.5, color="steelblue")
axes[0].set_ylabel("signal_nS (nS)")
axes[0].set_title(title)
axes[0].grid(True, alpha=0.3)

axes[1].plot(df["idx"], df["tonic"], linewidth=0.5, color="darkorange")
axes[1].set_ylabel("tonic (nS)")
axes[1].grid(True, alpha=0.3)

axes[2].plot(df["idx"], df["phasic"], linewidth=0.5, color="green")
axes[2].set_ylabel("phasic (nS)")
axes[2].set_xlabel("sample index")
axes[2].grid(True, alpha=0.3)

plt.tight_layout()
out = f"gt_fe_output_{df['idx'].iloc[0]}_{df['idx'].iloc[-1]}.png"
plt.savefig(out, dpi=150)
print(f"Saved {out}")
plt.show()
