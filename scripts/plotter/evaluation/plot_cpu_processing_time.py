#!/usr/bin/env python3
"""Plot CPU processing-time characterization from VCD debug timing CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


DEFAULT_INPUT = Path(__file__).resolve().parent / "cpu_process_time_vs_N.csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "cpu_processing_time_characterization.png"


def read_processing_points(csv_path: Path) -> tuple[list[int], list[float]]:
    grouped: dict[int, list[float]] = {}

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])

        if {"group", "duration_us_real"}.issubset(fields):
            for row in reader:
                if not row.get("group"):
                    continue
                grouped.setdefault(int(row["group"]), []).append(float(row["duration_us_real"]))
        elif {"group", "max_us_real"}.issubset(fields):
            for row in reader:
                if not row.get("group"):
                    continue
                grouped.setdefault(int(row["group"]), []).append(float(row["max_us_real"]))
        else:
            raise ValueError(
                "Unsupported CSV format. Expected columns 'group,duration_us_real' "
                "or 'group,max_us_real'."
            )

    if not grouped:
        raise ValueError(f"No grouped processing-time rows found in {csv_path}")

    N_vals = sorted(grouped)
    # Use the maximum per N to avoid invalid/short intervals biasing the timing model.
    T_proc_us = [max(grouped[N]) for N in N_vals]
    return N_vals, T_proc_us


def linear_fit(x_vals: list[int], y_vals: list[float]) -> tuple[float, float]:
    if len(x_vals) < 2:
        raise ValueError("At least two N values are required for a linear fit.")

    x_mean = sum(x_vals) / len(x_vals)
    y_mean = sum(y_vals) / len(y_vals)
    denom = sum((x - x_mean) ** 2 for x in x_vals)
    if denom == 0:
        raise ValueError("Cannot fit a line when all N values are identical.")

    a = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals)) / denom
    b = y_mean - a * x_mean
    return a, b


def plot_processing_time(csv_path: Path, output_path: Path) -> tuple[float, float]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    N_vals, T_proc_us = read_processing_points(csv_path)
    a, b = linear_fit(N_vals, T_proc_us)
    fit_vals = [a * N + b for N in N_vals]

    plt.rcParams.update(
        {
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.labelsize": 12,
            "legend.fontsize": 10,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
        }
    )

    fig, ax = plt.subplots(figsize=(10, 5.1), constrained_layout=True)

    ax.plot(
        N_vals,
        T_proc_us,
        linestyle="None",
        marker="o",
        markersize=7,
        color="#8E6AA5",
        label="Measured processing time",
    )

    ax.plot(
        N_vals,
        fit_vals,
        linestyle="--",
        linewidth=2.2,
        color="#D99A00",
        label=r"Linear model: $T_{\mathrm{processing}}(N)=aN+b$",
    )

    ax.set_title(
        "Software Processing Time Characterization:\nactive CPU time vs N (window size)"
    )
    ax.set_xlabel("N (Window size)")
    ax.set_ylabel(r"$T_{\mathrm{processing}}(N)$ [$\mu$s]")

    ax.text(
        0.05,
        0.95,
        rf"$a = {a:.0f}~\mu s/\mathrm{{sample}}$" "\n"
        rf"$b = {b:.0f}~\mu s$" "\n\n"
        r"where:" "\n"
        r"$a$ is the per-sample processing cost" "\n"
        r"$b$ is a fixed window overhead",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=11,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="0.72", alpha=0.94),
    )

    ax.grid(True, alpha=0.22, linewidth=0.8)
    ax.legend(loc="lower right", frameon=True, framealpha=0.96, edgecolor="0.75")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300)
    plt.close(fig)

    return a, b


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot CPU processing-time characterization from parser CSV output."
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    a, b = plot_processing_time(args.csv, args.out)
    print(f"wrote {args.out}")
    print(f"a_us_per_sample={a:.6f}")
    print(f"b_us={b:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
