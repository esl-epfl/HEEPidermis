#!/usr/bin/env python3
"""Small desktop monitor for the VCO based GSR measurements."""

from __future__ import annotations

import csv
import bisect
from dataclasses import dataclass
from datetime import datetime
import math
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

try:
    import serial
except ImportError as exc:
    raise SystemExit(
        "pyserial is required. Install it with 'python3 -m pip install pyserial'."
    ) from exc


ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS = ROOT / "sw" / "applications"
DEMO_SOURCE = APPLICATIONS / "gsr" / "demo" / "main.c"
TEST_SOURCE = APPLICATIONS / "test_VCO_counter" / "main.c"
OUT_DIR = ROOT / "sw" / "gui" / "outs"
TRANSFER_CSV = ROOT / "hw" / "vendor" / "analog-library" / "VCO" / "VCO_characteristics" / "data" / "VCO variability - transfer.csv"
MEMORY_SCRIPT = ROOT / "hw" / "vendor" / "x-heep" / "scripts" / "building" / "mem_usage.py"
ELF_MAP = ROOT / "sw" / "build" / "main.map"
LOGO_PNG = ROOT / "docs" / "img" / "cheep_logo.png"
QR_PNG = ROOT / "docs" / "img" / "HEEPidermis_QR.png"
DEFAULT_PORT = "/dev/serial/by-id/usb-FTDI_Quad_RS232-HS-if02-port0"
VIEW_SECONDS = 60.0
TERMINAL_LINES = 1000
MESSAGE_LINES = 100
IDAC_STEP_NA = 40
IDAC_MAX_CODE = 255
SAMPLING_RATES_HZ = tuple(float(f"{mantissa * 10 ** decade:g}")
                          for decade in range(-1, 4) for mantissa in range(1, 10)) + (10_000.0,)

# The firmware emits e.g. "3: 42000 Hz | 400000 uV| 3: 41000 Hz | 390000 uV = ...".
MICROVOLTS = re.compile(r"(-?\d+)\s*uV\b", re.IGNORECASE)
FREQUENCY_VOLTAGE = re.compile(r"(\d+)\s*Hz\s*\|\s*(-?\d+)\s*uV\b", re.IGNORECASE)
SAMPLE_RECORD = re.compile(r"^(\d+),(\d+),(\d+),(\d+)$")
GSR_BANNER = re.compile(r"^=== GSR demo: (\d+(?:\.\d+)?) Hz, (\d+) nA ===$")
CURRENT_ACK = re.compile(r"^Current set: (\d+) nA$")
SAMPLING_ACK = re.compile(r"^Sampling set: (\d+(?:\.\d+)?) (mHz|Hz)$")
SAMPLE_CURRENT = re.compile(r"\bI=(\d+)\s*nA\b")
MEMORY_BANK = re.compile(r"^(?:Cont|IntL)\s+\d+\s+[Cdi-]+\s+\d+(?:\.\d+)?%$")
RUNTIME_SYMBOL = re.compile(r"^\s*(0x[0-9a-fA-F]+)\s+(gsr_(?:injected_current_nA|sample_rate_millihz))\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Sample:
    time_s: float
    p_uv: int
    n_uv: int
    p_hz: int | None = None
    n_hz: int | None = None
    current_ua: float = 0.52

    @property
    def delta_uv(self) -> int:
        return self.p_uv - self.n_uv


@dataclass(frozen=True)
class OperatingPoint:
    p_uv: float
    n_uv: float
    p_hz: float | None
    n_hz: float | None


def conductance_us(delta_uv: float, current_ua: float) -> float | None:
    """Return conductance in µS from current in µA and voltage in µV."""
    return current_ua * 1_000_000 / abs(delta_uv) if delta_uv else None


def load_transfer_curve() -> list[tuple[float, float]]:
    """Return the measured nominal VCO curve as (input mV, frequency kHz)."""
    with TRANSFER_CSV.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        next(reader)
        return sorted((float(row[0]), float(row[1])) for row in reader if len(row) >= 2 and row[0] and row[1])


def nominal_frequency_khz(curve: list[tuple[float, float]], voltage_mv: float) -> float:
    if not curve:
        return 0.0
    if voltage_mv <= curve[0][0]:
        return curve[0][1]
    if voltage_mv >= curve[-1][0]:
        return curve[-1][1]
    index = bisect.bisect_left(curve, (voltage_mv, -math.inf))
    x0, y0 = curve[index - 1]
    x1, y1 = curve[index]
    return y0 + (voltage_mv - x0) * (y1 - y0) / (x1 - x0)


def nominal_voltage_uv(curve: list[tuple[float, float]], frequency_hz: int) -> int:
    """Invert the nominal (input mV, frequency kHz) curve for UART frequencies."""
    if not curve:
        return 0
    frequency_points = sorted((frequency_khz, voltage_mv) for voltage_mv, frequency_khz in curve)
    unique: list[tuple[float, float]] = []
    for frequency_khz, voltage_mv in frequency_points:
        if not unique or frequency_khz != unique[-1][0]:
            unique.append((frequency_khz, voltage_mv))
    target_khz = frequency_hz / 1000.0
    if target_khz <= unique[0][0]:
        return round(unique[0][1] * 1000)
    if target_khz >= unique[-1][0]:
        return round(unique[-1][1] * 1000)
    index = bisect.bisect_left(unique, (target_khz, -math.inf))
    f0, v0 = unique[index - 1]
    f1, v1 = unique[index]
    return round((v0 + (target_khz - f0) * (v1 - v0) / (f1 - f0)) * 1000)


def read_define(name: str, source: Path = DEMO_SOURCE) -> int:
    match = re.search(rf"^#define\s+{re.escape(name)}\s+(\d+)\s*$", source.read_text(), re.MULTILINE)
    if match is None:
        raise ValueError(f"Could not find {name} in {source}")
    return int(match.group(1))


def write_defines(changes: dict[str, int], path: Path = DEMO_SOURCE) -> None:
    source = path.read_text()
    for name, value in changes.items():
        pattern = rf"^(#define[ \t]+{re.escape(name)}[ \t]+)\d+([ \t]*)$"
        source, count = re.subn(pattern, lambda match: f"{match.group(1)}{value}{match.group(2)}", source, count=1, flags=re.MULTILINE)
        if count != 1:
            raise ValueError(f"Could not update {name} in {path}")
    path.write_text(source)


def write_runtime_word(symbol: str, value: int) -> int:
    """Write one volatile GSR runtime setting through OpenOCD's Tcl port."""
    matches = {name: int(address, 16) for address, name in RUNTIME_SYMBOL.findall(ELF_MAP.read_text())}
    if symbol not in matches:
        raise RuntimeError(f"The built firmware has no {symbol} symbol. Build gsr/demo first.")
    address = matches[symbol]
    with socket.create_connection(("127.0.0.1", 6666), timeout=4) as connection:
        connection.settimeout(4)

        def rpc(command: str) -> str:
            connection.sendall(command.encode("ascii") + b"\x1a")
            response = bytearray()
            while b"\x1a" not in response:
                chunk = connection.recv(4096)
                if not chunk:
                    raise ConnectionError("OpenOCD closed its control connection.")
                response.extend(chunk)
            result = response.split(b"\x1a", 1)[0].decode("utf-8", errors="replace").strip()
            if "Error:" in result:
                raise RuntimeError(result)
            return result

        halted = False
        try:
            rpc("halt")
            halted = True
            rpc(f"mww 0x{address:08x} 0x{value:08x}")
            readback = rpc(f"mem2array gsr_readback 32 0x{address:08x} 1; format 0x%08x $gsr_readback(0)")
            readback_value = int(readback, 16)
            if readback_value != value:
                raise RuntimeError(f"JTAG readback was {readback}, expected 0x{value:08x}.")
        finally:
            if halted:
                rpc("resume")
    return address


class SignalPlot(tk.Canvas):
    """Raw samples and moving averages over a shared time viewport."""

    def __init__(self, master: tk.Misc, channels: list[tuple[str, str, str]], *, y_unit: str, display_scale: float = 1.0, fixed_range: tuple[float, float] | None = None, **kwargs):
        super().__init__(master, background="#101820", highlightthickness=0, **kwargs)
        self.channels = channels  # (sample attribute, strong color, faded color)
        self.y_unit = y_unit
        self.display_scale = display_scale
        self.fixed_range = fixed_range
        self.samples: list[Sample] = []
        self.times: list[float] = []
        self.prefix_p: list[int] = [0]
        self.prefix_n: list[int] = [0]
        self.prefix_current: list[float] = [0.0]
        self.start_s = 0.0
        self.view_seconds = VIEW_SECONDS
        self.cursor_time: float | None = None
        self.window = 1
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_view(self, samples: list[Sample], times: list[float], prefix_p: list[int], prefix_n: list[int], prefix_current: list[float], start_s: float, window: int, view_seconds: float, cursor_time: float | None) -> None:
        self.samples, self.times = samples, times
        self.prefix_p, self.prefix_n = prefix_p, prefix_n
        self.prefix_current = prefix_current
        self.start_s, self.window = start_s, window
        self.view_seconds = view_seconds
        self.cursor_time = cursor_time
        self.redraw()

    def _raw_value(self, sample: Sample, channel: str) -> float | None:
        if channel == "conductance_us":
            return conductance_us(sample.delta_uv, sample.current_ua)
        return getattr(sample, channel)

    def _average(self, index: int, channel: str) -> float | None:
        first = max(0, index + 1 - self.window)
        count = index + 1 - first
        p = (self.prefix_p[index + 1] - self.prefix_p[first]) / count
        n = (self.prefix_n[index + 1] - self.prefix_n[first]) / count
        if channel == "conductance_us":
            current = (self.prefix_current[index + 1] - self.prefix_current[first]) / count
            return conductance_us(p - n, current)
        return p if channel == "p_uv" else n

    def redraw(self) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        left, right, top, bottom = 78, 16, 28, 32
        plot_w, plot_h = max(width - left - right, 1), max(height - top - bottom, 1)
        labels = {"p_uv": "P", "n_uv": "N", "conductance_us": "G"}
        for position, (channel, strong, faded) in enumerate(self.channels):
            legend_x = left + position * 90
            self.create_oval(legend_x, 10, legend_x + 5, 15, fill=faded, outline="")
            self.create_line(legend_x + 9, 12, legend_x + 23, 12, fill=strong, width=2)
            self.create_text(legend_x + 28, 12, text=labels[channel], fill=strong, anchor="w", font=("TkDefaultFont", 9))
        first = bisect.bisect_left(self.times, self.start_s)
        last = bisect.bisect_right(self.times, self.start_s + self.view_seconds)
        visible = self.samples[first:last]
        values = [value for sample in visible for channel, _, _ in self.channels if (value := self._raw_value(sample, channel)) is not None]
        values.extend(value for index in range(first, last) for channel, _, _ in self.channels if (value := self._average(index, channel)) is not None)
        if self.fixed_range is not None:
            low, high = self.fixed_range
        else:
            low, high = (min(values), max(values)) if values else (0.0, 1.0)
            span = high - low
            padding = span * (1 / 0.9 - 1) / 2 if span > 0 else max(abs(high) * 0.02, 1.0)
            low, high = low - padding, high + padding

        for tick in range(5):
            y = top + plot_h * tick / 4
            value = high - (high - low) * tick / 4
            self.create_line(left, y, width - right, y, fill="#263744")
            tick_text = f"{value / self.display_scale:,.0f}" if self.fixed_range else f"{value / self.display_scale:,.2f}"
            self.create_text(left - 6, y, text=tick_text, fill="#b6c6d2", anchor="e", font=("TkDefaultFont", 9))
        self.create_text(13, top + plot_h / 2, text=self.y_unit, angle=90, fill="#b6c6d2", font=("TkDefaultFont", 9))
        for tick in range(7):
            x = left + plot_w * tick / 6
            self.create_line(x, top, x, top + plot_h, fill="#263744")
            self.create_text(x, height - 18, text=f"{self.start_s + self.view_seconds * tick / 6:.2f}".rstrip("0").rstrip("."), fill="#b6c6d2", font=("TkDefaultFont", 9))
        self.create_text(left + plot_w / 2, height - 5, text="Time (s)", fill="#b6c6d2", font=("TkDefaultFont", 9))

        for channel, strong, faded in self.channels:
            segments = []
            points = []
            for index in range(first, last):
                sample = self.samples[index]
                x = left + plot_w * (sample.time_s - self.start_s) / self.view_seconds
                raw_value = self._raw_value(sample, channel)
                if raw_value is not None and low <= raw_value <= high:
                    raw_y = top + plot_h * (high - raw_value) / (high - low)
                    self.create_oval(x - 2, raw_y - 2, x + 2, raw_y + 2, fill=faded, outline="")
                average = self._average(index, channel)
                if average is None:
                    if points:
                        segments.append(points)
                        points = []
                    continue
                avg_y = top + plot_h * (high - min(max(average, low), high)) / (high - low)
                points.extend((x, avg_y))
            if points:
                segments.append(points)
            for segment in segments:
                if len(segment) >= 4:
                    self.create_line(*segment, fill=strong, width=2)
                else:
                    x, y = segment
                    self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=strong, outline="")

        if self.cursor_time is not None and self.start_s <= self.cursor_time <= self.start_s + self.view_seconds:
            cursor_x = left + plot_w * (self.cursor_time - self.start_s) / self.view_seconds
            self.create_line(cursor_x, top, cursor_x, top + plot_h, fill="#f4f7fa", width=2,
                             dash=(5, 3), tags="time_cursor")


class TransferPlot(tk.Canvas):
    """Nominal VCO transfer curve with averaged measured operating points."""

    def __init__(self, master: tk.Misc, curve: list[tuple[float, float]], **kwargs):
        super().__init__(master, background="#101820", highlightthickness=0, **kwargs)
        self.curve = curve
        self.latest: OperatingPoint | None = None
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_point(self, point: OperatingPoint) -> None:
        self.latest = point
        self.redraw()

    def clear_point(self) -> None:
        self.latest = None
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        if not self.curve:
            self.create_text(15, 20, text="Transfer curve unavailable", fill="#d8e2e9", anchor="w")
            return
        width, height = self.winfo_width(), self.winfo_height()
        left, right, top, bottom = 54, 18, 26, 48
        plot_w, plot_h = max(width - left - right, 1), max(height - top - bottom, 1)
        self.create_line(left, 12, left + 18, 12, fill="#9caebc", width=2)
        self.create_text(left + 24, 12, text="CSV transfer", fill="#9caebc", anchor="w", font=("TkDefaultFont", 9))
        markers = []
        if self.latest:
            for label, uv, hz, color in (("P", self.latest.p_uv, self.latest.p_hz, "#ffb000"), ("N", self.latest.n_uv, self.latest.n_hz, "#38c6d9")):
                mv = uv / 1000
                markers.append((label, mv, hz / 1000 if hz is not None else nominal_frequency_khz(self.curve, mv), color))
        x_min = min(self.curve[0][0], *(marker[1] for marker in markers)) if markers else self.curve[0][0]
        x_max = max(self.curve[-1][0], *(marker[1] for marker in markers)) if markers else self.curve[-1][0]
        y_max = max(point[1] for point in self.curve)
        if markers:
            y_max = max(y_max, *(marker[2] for marker in markers))
        x_pad = max((x_max - x_min) * 0.04, 1)
        x_min, x_max = x_min - x_pad, x_max + x_pad
        y_max = max(y_max * 1.06, 1)

        def xy(mv: float, khz: float) -> tuple[float, float]:
            return left + plot_w * (mv - x_min) / (x_max - x_min), top + plot_h * (y_max - khz) / y_max

        for tick in range(5):
            value = y_max * tick / 4
            y = xy(x_min, value)[1]
            self.create_line(left, y, left + plot_w, y, fill="#263744")
            self.create_text(left - 5, y, text=f"{value:.0f}", fill="#b6c6d2", anchor="e", font=("TkDefaultFont", 9))
            mv = x_min + (x_max - x_min) * tick / 4
            x = xy(mv, 0)[0]
            self.create_line(x, top, x, top + plot_h, fill="#263744")
            self.create_text(x, height - 30, text=f"{mv:.0f}", fill="#b6c6d2", font=("TkDefaultFont", 9))
        self.create_text(left + plot_w / 2, height - 12, text="Input voltage (mV)", fill="#b6c6d2", font=("TkDefaultFont", 9))
        self.create_text(12, top + plot_h / 2, text="kHz", angle=90, fill="#b6c6d2", font=("TkDefaultFont", 9))
        curve_points = [coordinate for point in self.curve for coordinate in xy(*point)]
        self.create_line(*curve_points, fill="#9caebc", width=2)
        for label, mv, khz, color in markers:
            x, y = xy(mv, khz)
            nominal_y = xy(mv, nominal_frequency_khz(self.curve, mv))[1]
            if abs(nominal_y - y) > 3:
                self.create_line(x, y, x, nominal_y, fill=color, dash=(2, 3))
            self.create_oval(x - 3, nominal_y - 3, x + 3, nominal_y + 3, outline=color, width=1)
            self.create_oval(x - 6, y - 6, x + 6, y + 6, fill=color, outline="#ffffff")
            compact = width < 300
            right_side = x < width - (95 if compact else 145)
            caption = f"{label} {mv:.0f} mV" if compact else f"{label}  {mv:.1f} mV  {khz:.1f} kHz"
            self.create_text(x + 10 if right_side else x - 10, y - 11 if label == "P" else y + 12,
                             text=caption, fill=color, anchor="w" if right_side else "e",
                             font=("TkDefaultFont", 8 if compact else 9, "bold"))


class TradeoffPlot(tk.Canvas):
    """Model heatmap with a marker at the averaged measured operating point."""

    def __init__(self, master: tk.Misc, model, metric: str, **kwargs):
        super().__init__(master, background="#101820", highlightthickness=0, **kwargs)
        self.model = model
        self.metric = metric
        self.current_ua = 0.52
        self.sample_hz = 10.0
        self.point: tuple[float, float, float, float, float] | None = None
        self.values = None
        self.bind("<Configure>", lambda _event: self.redraw())
        if model is not None:
            self.values = model.power_map() if metric == "power" else model.resolution_map(self.current_ua)

    @staticmethod
    def _color(fraction: float) -> str:
        stops = ((28, 67, 102), (51, 176, 167), (244, 213, 108), (228, 100, 77))
        scaled = max(0.0, min(1.0, fraction)) * (len(stops) - 1)
        index = min(int(scaled), len(stops) - 2)
        blend = scaled - index
        rgb = [round(stops[index][channel] * (1 - blend) + stops[index + 1][channel] * blend) for channel in range(3)]
        return "#" + "".join(f"{part:02x}" for part in rgb)

    def set_operating(self, g_us: float | None, p_mv: float, n_mv: float, current_ua: float, sample_hz: float) -> None:
        changed = self.metric == "resolution" and abs(current_ua - self.current_ua) > 1e-9
        self.current_ua = current_ua
        self.sample_hz = sample_hz
        self.point = (g_us, p_mv, n_mv, current_ua, sample_hz) if g_us is not None else None
        if changed and self.model is not None:
            self.values = self.model.resolution_map(current_ua)
            self.redraw()
        else:
            self._draw_marker()

    def clear_point(self) -> None:
        self.point = None
        self._draw_marker()

    def redraw(self) -> None:
        self.delete("all")
        if self.model is None or self.values is None:
            self.create_text(12, 20, text="Model unavailable", fill="#b6c6d2", anchor="w")
            return
        width, height = self.winfo_width(), self.winfo_height()
        left, right, top, bottom = 42, 68, 28, 32
        plot_w, plot_h = max(width - left - right, 1), max(height - top - bottom, 1)
        rows, columns = self.values.shape
        finite = [float(value) for row in self.values for value in row if math.isfinite(float(value))]
        low, high = (min(finite), max(finite)) if finite else (0.0, 1.0)
        span = max(high - low, 1e-9)
        for row in range(rows):
            for column in range(columns):
                value = float(self.values[row, column])
                x0 = left + plot_w * column / columns
                x1 = left + plot_w * (column + 1) / columns + 1
                y0 = top + plot_h * (rows - row - 1) / rows
                y1 = top + plot_h * (rows - row) / rows + 1
                color = self._color((value - low) / span) if math.isfinite(value) else "#303a43"
                self.create_rectangle(x0, y0, x1, y1, fill=color, outline=color)
        bar_left = width - right + 15
        bar_right = bar_left + 12
        for step in range(48):
            fraction = 1 - (step + 0.5) / 48
            y0 = top + plot_h * step / 48
            y1 = top + plot_h * (step + 1) / 48 + 1
            color = self._color(fraction)
            self.create_rectangle(bar_left, y0, bar_right, y1, fill=color, outline=color)
        self.create_rectangle(bar_left, top, bar_right, top + plot_h, outline="#d8e2e9")
        unit = "µW" if self.metric == "power" else "bits"
        self.create_text(bar_left, top - 11, text=unit, anchor="w", fill="#b6c6d2", font=("TkDefaultFont", 8))
        for fraction, value in ((0, high), (0.5, (low + high) / 2), (1, low)):
            self.create_text(bar_right + 5, top + plot_h * fraction, text=f"{value:.1f}",
                             anchor="w", fill="#b6c6d2", font=("TkDefaultFont", 8))
        for tick, label in ((0.0, "1"), (0.5, "10"), (1.0, "100")):
            self.create_text(left + plot_w * tick, height - 17, text=label, fill="#b6c6d2", font=("TkDefaultFont", 9))
        y_ticks = ((0.0, "0.04"), (0.5, "5"), (1.0, "10.2")) if self.metric == "power" else tuple((step / 5, label) for step, label in enumerate(("0.1", "1", "10", "100", "1k", "10k")))
        for fraction, label in y_ticks:
            self.create_text(left - 5, top + plot_h * (1 - fraction), text=label, anchor="e", fill="#b6c6d2", font=("TkDefaultFont", 9))
        self.create_text(left + plot_w / 2, height - 3, text="Conductance (µS)", fill="#b6c6d2", font=("TkDefaultFont", 9))
        self.create_text(12, top + plot_h / 2, text="Current (µA)" if self.metric == "power" else "Sample rate (Hz)", angle=90, fill="#b6c6d2", font=("TkDefaultFont", 9))
        self._draw_marker()

    def _draw_marker(self) -> None:
        self.delete("marker")
        if self.model is None or self.point is None:
            return
        g_us, p_mv, n_mv, current_ua, sample_hz = self.point
        if not math.isfinite(g_us) or g_us <= 0:
            return
        width, height = self.winfo_width(), self.winfo_height()
        left, right, top, bottom = 42, 68, 28, 32
        plot_w, plot_h = max(width - left - right, 1), max(height - top - bottom, 1)
        x_fraction = (math.log10(g_us) - 0.0) / 2.0
        y_value = current_ua if self.metric == "power" else sample_hz
        y_fraction = (y_value - 0.04) / (10.2 - 0.04) if self.metric == "power" else (math.log10(max(y_value, 0.1)) + 1) / 5
        x = left + plot_w * max(0.0, min(1.0, x_fraction))
        y = top + plot_h * (1 - max(0.0, min(1.0, y_fraction)))
        estimate = self.model.power_uw(g_us, current_ua, p_mv, n_mv) if self.metric == "power" else self.model.resolution_bits(g_us, sample_hz, current_ua, p_mv, n_mv)
        try:
            value = float(estimate)
        except (TypeError, ValueError):
            value = math.nan
        label = f"{value:.2f} {'µW' if self.metric == 'power' else 'bits'}" if math.isfinite(value) else "outside model range"
        self.create_line(x, top, x, top + plot_h, fill="white", dash=(3, 3), tags="marker")
        self.create_line(left, y, left + plot_w, y, fill="white", dash=(3, 3), tags="marker")
        self.create_oval(x - 5, y - 5, x + 5, y + 5, fill="white", outline="#101820", tags="marker")
        self.create_text(left + plot_w / 2, 13, text=label, fill="white",
                         font=("TkDefaultFont", 8, "bold"), tags="marker")


class VCOGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("HEEPidermis · VCO GSR monitor")
        self._configure_style()
        width = min(1800, max(1100, root.winfo_screenwidth() - 80))
        height = min(950, max(700, root.winfo_screenheight() - 100))
        self.root.geometry(f"{width}x{height}")
        self.events: queue.Queue[tuple] = queue.Queue()
        self.samples: list[Sample] = []
        self.times: list[float] = []
        self.memory_saturated = False
        self.prefix_p: list[int] = [0]
        self.prefix_n: list[int] = [0]
        self.prefix_current: list[float] = [0.0]
        self.prefix_p_hz: list[int] = [0]
        self.prefix_n_hz: list[int] = [0]
        self.prefix_p_hz_count: list[int] = [0]
        self.prefix_n_hz_count: list[int] = [0]
        self.first_sample_clock: float | None = None
        self.history_start_clock = 0.0
        self.view_start = 0.0
        self.follow_latest = True
        self.cursor_time: float | None = None
        self.cursor_follows_live = True
        self.operating_time_text = tk.StringVar(value="VCO operating points · waiting for samples")
        self.filter_size = 10
        self.view_seconds = VIEW_SECONDS
        self.zoom_text = tk.StringVar(value=f"TIME ZOOM · {VIEW_SECONDS:g} s")
        self.applied_current_ua = read_define("INJECTED_CURRENT_NA") / 1000
        self.applied_sampling_hz = read_define("VCO_SAMPLE_RATE_MILLIHZ") / 1000
        self.session_path: Path | None = None
        try:
            self.transfer_curve = load_transfer_curve()
            self.transfer_error = None
        except (OSError, ValueError) as error:
            self.transfer_curve = []
            self.transfer_error = str(error)
        try:
            from tradeoffs import TradeoffModel
            self.tradeoff_model = TradeoffModel()
            self.tradeoff_error = None
        except Exception as error:
            self.tradeoff_model = None
            self.tradeoff_error = str(error)
        self.processes: dict[str, subprocess.Popen] = {}
        self.process_lock = threading.Lock()
        self.serial_port: serial.Serial | None = None
        self.stop_reader = threading.Event()
        self.run_pending = False
        self.recording_state = "stopped"
        self.gdb_opened = False
        self.close_completed = False
        self.operation: str | None = None
        self.open_requested = False
        self.connection_generation = 0
        self.firmware_is_demo = False
        self.pending_current_na: int | None = None
        self.current_request_id = 0
        self.pending_sample_millihz: int | None = None
        self.sample_request_id = 0
        self.startup_current_pending = False
        self.shutting_down = False
        self.configured_freq = read_define("SYS_FCLK_HZ")
        self.last_message: tuple[str, str] | None = None
        self._build_layout()
        if self.transfer_error:
            self._log(f"Transfer curve unavailable: {self.transfer_error}")
        if self.tradeoff_error:
            self._log(f"Power/resolution model unavailable: {self.tradeoff_error}")
        self.root.after(50, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        self.root.configure(background="#0b131c")
        self.root.option_add("*TCombobox*Listbox.background", "#0d1923")
        self.root.option_add("*TCombobox*Listbox.foreground", "#ecf5f8")
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#30546b")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background="#111e29", foreground="#e4edf3", font=("DejaVu Sans", 10))
        style.configure("TFrame", background="#111e29")
        style.configure("TLabel", background="#111e29", foreground="#e4edf3")
        style.configure("TButton", padding=(10, 7), background="#243b4c", foreground="#e9f4f8", bordercolor="#365668")
        style.map("TButton", background=[("active", "#30546b"), ("disabled", "#1a2a36")],
                  foreground=[("disabled", "#8495a1")])
        style.configure("TEntry", fieldbackground="#0d1923", foreground="#ecf5f8", insertcolor="#ecf5f8")
        style.configure("TSpinbox", fieldbackground="#0d1923", foreground="#ecf5f8", arrowcolor="#9edfe7")
        style.configure("TCombobox", fieldbackground="#0d1923", foreground="#ecf5f8", arrowcolor="#9edfe7")
        style.map("TCombobox", fieldbackground=[("readonly", "#0d1923")],
                  foreground=[("readonly", "#ecf5f8")])
        style.configure("TLabelframe", background="#111e29", bordercolor="#365668")
        style.configure("TLabelframe.Label", background="#111e29", foreground="#c7dbe4")
        style.configure("Warning.TLabel", background="#111e29", foreground="#ffc857", font=("DejaVu Sans", 9, "bold"))
        style.configure("TScrollbar", background="#294456", troughcolor="#101d28", arrowcolor="#c7dbe4")

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, minsize=500)
        container.columnconfigure(1, minsize=680)
        container.columnconfigure(2, weight=1)
        container.rowconfigure(1, weight=1)

        header = tk.Frame(container, background="#101820", padx=18, pady=9)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)
        self.logo_image = None
        self.qr_image = None
        try:
            self.logo_image = tk.PhotoImage(file=str(LOGO_PNG)).subsample(3)
            tk.Label(header, image=self.logo_image, background="#101820").grid(row=0, column=0, sticky="w")
        except (OSError, tk.TclError):
            tk.Label(header, text="HEEPidermis", background="#101820", foreground="white",
                     font=("DejaVu Sans", 22, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="VCO GSR MONITOR", background="#101820", foreground="#9edfe7",
                 font=("DejaVu Sans", 15, "bold")).grid(row=0, column=1, sticky="e", padx=24)
        try:
            self.qr_image = tk.PhotoImage(file=str(QR_PNG)).subsample(2)
            tk.Label(header, image=self.qr_image, background="white", padx=3, pady=3).grid(row=0, column=2, sticky="e")
        except (OSError, tk.TclError):
            pass

        side = ttk.Frame(container)
        side.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        side.columnconfigure(0, weight=1)
        side.rowconfigure(0, weight=1)
        side_canvas = tk.Canvas(side, highlightthickness=0, background="#111e29")
        side_canvas.grid(row=0, column=0, sticky="nsew")
        side_scroll = ttk.Scrollbar(side, orient="vertical", command=side_canvas.yview)
        side_scroll.grid(row=0, column=1, sticky="ns")
        side_canvas.configure(yscrollcommand=side_scroll.set)
        message_frame = ttk.LabelFrame(side, text="Messages", padding=8)
        message_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        message_frame.columnconfigure(0, weight=1)
        self.messages = tk.Text(message_frame, height=7, width=1, wrap="word",
                                background="#0d1923", foreground="#e4edf3",
                                font=("DejaVu Sans", 10), relief="flat",
                                padx=8, pady=6, state="disabled")
        self.messages.grid(row=0, column=0, sticky="nsew")
        message_scroll = ttk.Scrollbar(message_frame, command=self.messages.yview)
        message_scroll.grid(row=0, column=1, sticky="ns")
        self.messages.configure(yscrollcommand=message_scroll.set)
        self.messages.tag_configure("uart", foreground="#9edfe7", spacing3=8)
        self.messages.tag_configure("gui", foreground="#ffc857", spacing3=8)
        controls = ttk.Frame(side_canvas, padding=14)
        controls_window = side_canvas.create_window((0, 0), window=controls, anchor="nw")
        controls.bind("<Configure>", lambda _event: side_canvas.configure(scrollregion=side_canvas.bbox("all")))
        side_canvas.bind("<Configure>", lambda event: side_canvas.itemconfigure(controls_window, width=event.width))
        controls.columnconfigure(0, weight=1)
        ttk.Label(controls, text="DEVICE CONTROL", font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 14))

        projects = sorted(path.parent.relative_to(APPLICATIONS).as_posix() for path in APPLICATIONS.rglob("main.c"))
        self.project = tk.StringVar(value="gsr/demo" if "gsr/demo" in projects else (projects[0] if projects else ""))
        ttk.Label(controls, text="Application").grid(row=1, column=0, sticky="w")
        ttk.Combobox(controls, textvariable=self.project, values=projects, state="readonly").grid(row=2, column=0, sticky="ew", pady=(3, 12))

        ttk.Label(controls, text="MCU frequency (Hz)").grid(row=3, column=0, sticky="w")
        self.mcu_freq = tk.StringVar(value=str(self.configured_freq))
        ttk.Entry(controls, textvariable=self.mcu_freq).grid(row=4, column=0, sticky="ew", pady=(3, 5))
        ttk.Button(controls, text="Config. board", command=self.configure_board).grid(row=5, column=0, sticky="ew", pady=(0, 4))
        self.board_reset_notice = tk.StringVar(value="")

        ttk.Button(controls, text="Build", command=self.build).grid(row=7, column=0, sticky="ew", pady=(0, 4))
        self.memory = tk.StringVar(value="Memory use appears here after Build.")
        ttk.Label(controls, textvariable=self.memory, justify="left", anchor="nw", font=("TkFixedFont", 10)).grid(row=8, column=0, sticky="ew", pady=(0, 12))
        recording_buttons = ttk.Frame(controls)
        recording_buttons.grid(row=9, column=0, sticky="ew", pady=3)
        recording_buttons.columnconfigure(0, weight=2)
        recording_buttons.columnconfigure(1, weight=1)
        self.record_button = tk.Button(recording_buttons, text="▶  START RECORDING", command=self.toggle_recording, background="#198754", foreground="white", activebackground="#157347", activeforeground="white", relief="flat", font=("TkDefaultFont", 10, "bold"), cursor="hand2")
        self.record_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(recording_buttons, text="RESET", command=self.reset_history).grid(row=0, column=1, sticky="ew")
        ttk.Label(controls, text="Sampling frequency · 0.1–10,000 Hz").grid(row=10, column=0, sticky="w", pady=(12, 0))
        initial_rate_index = min(range(len(SAMPLING_RATES_HZ)),
                                 key=lambda index: abs(math.log(SAMPLING_RATES_HZ[index] / self.applied_sampling_hz)))
        self.sampling_index = tk.IntVar(value=initial_rate_index)
        self.sampling_selection = tk.StringVar(value="")
        self.sampling_status = tk.StringVar(value="Build applies the selected startup rate; release the slider while recording to update live.")
        self.sampling_scale = tk.Scale(controls, from_=0, to=len(SAMPLING_RATES_HZ) - 1,
                                       orient="horizontal", resolution=1, showvalue=False,
                                       variable=self.sampling_index, command=self._sampling_slider_changed,
                                       background="#111e29", foreground="#e4edf3",
                                       troughcolor="#294457", activebackground="#38c6d9",
                                       highlightthickness=0, borderwidth=0, sliderlength=20)
        self.sampling_scale.grid(row=11, column=0, sticky="ew")
        self.sampling_scale.bind("<ButtonRelease-1>", self._sampling_slider_released)
        self._sampling_slider_changed(str(initial_rate_index))
        ttk.Label(controls, textvariable=self.sampling_selection, wraplength=450).grid(row=12, column=0, sticky="w", pady=(0, 8))
        ttk.Label(controls, textvariable=self.sampling_status, wraplength=450,
                  font=("TkDefaultFont", 8)).grid(row=13, column=0, sticky="w", pady=(0, 8))

        ttk.Label(controls, text="Injected current · iDAC code 1–255").grid(row=14, column=0, sticky="w")
        initial_code = max(1, min(IDAC_MAX_CODE, round(self.applied_current_ua * 1000 / IDAC_STEP_NA)))
        self.current_code = tk.IntVar(value=initial_code)
        self.current_selection = tk.StringVar(value="")
        self.current_status = tk.StringVar(value="Firmware current: waiting for device")
        self.current_scale = tk.Scale(controls, from_=1, to=IDAC_MAX_CODE, orient="horizontal",
                                      resolution=1, showvalue=False, variable=self.current_code,
                                      command=self._current_slider_changed, background="#111e29",
                                      foreground="#e4edf3", troughcolor="#294457",
                                      activebackground="#38c6d9", highlightthickness=0,
                                      borderwidth=0, sliderlength=20)
        self.current_scale.grid(row=15, column=0, sticky="ew", pady=(0, 2))
        self.current_scale.bind("<ButtonRelease-1>", self._slider_released)
        self.current_scale.bind("<KeyRelease>", self._slider_released)
        self._current_slider_changed(str(initial_code))
        current_range = ttk.Frame(controls)
        current_range.grid(row=16, column=0, sticky="ew", pady=(0, 5))
        current_range.columnconfigure(1, weight=1)
        ttk.Label(current_range, text="0.04 µA", font=("TkDefaultFont", 8)).grid(row=0, column=0, sticky="w")
        ttk.Label(current_range, text="10.20 µA", font=("TkDefaultFont", 8)).grid(row=0, column=2, sticky="e")
        current_controls = ttk.Frame(controls)
        current_controls.grid(row=17, column=0, sticky="ew", pady=(0, 4))
        current_controls.columnconfigure(0, weight=1)
        ttk.Label(current_controls, textvariable=self.current_selection).grid(row=0, column=0, sticky="w")
        ttk.Label(controls, textvariable=self.current_status, wraplength=450).grid(row=18, column=0, sticky="w", pady=(0, 10))
        ttk.Separator(controls).grid(row=19, column=0, sticky="ew", pady=14)

        ttk.Label(controls, text="Serial port").grid(row=20, column=0, sticky="w")
        self.port = tk.StringVar(value=DEFAULT_PORT)
        ttk.Entry(controls, textvariable=self.port).grid(row=21, column=0, sticky="ew", pady=(3, 9))
        ttk.Label(controls, text="Baud rate (MCU frequency ÷ 20)").grid(row=22, column=0, sticky="w")
        self.baud = tk.StringVar(value=str(self.configured_freq // 20))
        ttk.Entry(controls, textvariable=self.baud).grid(row=23, column=0, sticky="ew", pady=(3, 14))
        ttk.Label(controls, text="Moving average (samples; 1 = off)").grid(row=24, column=0, sticky="w")
        self.filter_window = tk.StringVar(value="10")
        ttk.Spinbox(controls, from_=1, to=100000, textvariable=self.filter_window).grid(row=25, column=0, sticky="ew", pady=(3, 9))
        self.filter_window.trace_add("write", self._filter_changed)
        ttk.Button(controls, text="Save CSV…", command=self.save_csv).grid(row=26, column=0, sticky="ew", pady=(0, 4))
        self.recording_path_text = tk.StringVar(value="Recording starts with the first sample.")
        ttk.Label(controls, textvariable=self.recording_path_text, wraplength=450).grid(row=27, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(controls, text="Sampling-rate estimate window (samples)").grid(row=28, column=0, sticky="w")
        self.rate_window = tk.StringVar(value="100")
        ttk.Spinbox(controls, from_=2, to=100000, textvariable=self.rate_window).grid(row=29, column=0, sticky="ew", pady=(3, 5))
        self.rate_window.trace_add("write", self._rate_window_changed)
        self.rate_estimate_text = tk.StringVar(value="Average sample rate: waiting for samples")
        ttk.Label(controls, textvariable=self.rate_estimate_text, wraplength=450).grid(row=30, column=0, sticky="w")
        operating_host = ttk.Frame(container)
        operating_host.grid(row=1, column=1, sticky="nsew", padx=(0, 12))
        operating_host.columnconfigure(0, weight=1)
        for plot_row in (1, 4, 6):
            operating_host.rowconfigure(plot_row, weight=1, uniform="operating_plots")
        ttk.Label(operating_host, textvariable=self.operating_time_text,
                  font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 3))
        self.transfer_plot = TransferPlot(operating_host, self.transfer_curve, height=1)
        self.transfer_plot.grid(row=1, column=0, sticky="nsew")
        ttk.Label(operating_host, text="Solid: averaged P/N · ring: nominal curve", wraplength=640,
                  font=("TkDefaultFont", 8)).grid(row=2, column=0, sticky="w", pady=(3, 5))
        ttk.Label(operating_host, text="Estimated sensing power · µW", wraplength=640,
                  font=("TkDefaultFont", 10, "bold")).grid(row=3, column=0, sticky="w", pady=(2, 3))
        self.power_plot = TradeoffPlot(operating_host, self.tradeoff_model, "power", height=1)
        self.power_plot.grid(row=4, column=0, sticky="nsew")
        ttk.Label(operating_host, text="Estimated resolution · bits", wraplength=640,
                  font=("TkDefaultFont", 10, "bold")).grid(row=5, column=0, sticky="w", pady=(4, 3))
        self.resolution_plot = TradeoffPlot(operating_host, self.tradeoff_model, "resolution", height=1)
        self.resolution_plot.grid(row=6, column=0, sticky="nsew")
        self.resolution_plot.set_operating(None, 0.0, 0.0, self.applied_current_ua, self.applied_sampling_hz)
        self.capacity_bar = tk.Canvas(operating_host, height=16, background="#101820", highlightthickness=0)
        self.capacity_bar.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        self.capacity_bar.bind("<Configure>", lambda _event: self._draw_capacity_bar())
        self._draw_capacity_bar()

        plots = ttk.Frame(container)
        plots.grid(row=1, column=2, sticky="nsew")
        plots.columnconfigure(0, weight=1)
        plots.rowconfigure(1, weight=3)
        plots.rowconfigure(3, weight=2)
        ttk.Label(plots, text="VCO P and VCO N · mV", font=("TkDefaultFont", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.main_plot = SignalPlot(plots, [("p_uv", "#ffb000", "#86611b"), ("n_uv", "#38c6d9", "#2c6b74")], y_unit="mV", display_scale=1000)
        self.main_plot.grid(row=1, column=0, sticky="nsew", pady=(0, 14))
        self.main_plot.bind("<Button-1>", self._select_cursor_from_event)
        self.main_plot.bind("<B1-Motion>", self._select_cursor_from_event)
        ttk.Label(plots, text="Tissue conductance · µS", font=("TkDefaultFont", 12, "bold")).grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.diff_plot = SignalPlot(plots, [("conductance_us", "#b895ff", "#68558d")], y_unit="µS")
        self.diff_plot.grid(row=3, column=0, sticky="nsew")
        self.diff_plot.bind("<Button-1>", self._select_cursor_from_event)
        self.diff_plot.bind("<B1-Motion>", self._select_cursor_from_event)
        self.timeline_scroll = ttk.Scrollbar(plots, orient="horizontal", command=self._scroll_signals)
        self.timeline_scroll.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.timeline_scroll.set(0, 1)

        terminal_frame = ttk.LabelFrame(container, text="Terminal output", padding=5)
        terminal_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        terminal_frame.columnconfigure(0, weight=1)
        terminal_frame.rowconfigure(0, weight=1)
        self.terminal = tk.Text(terminal_frame, height=3, wrap="none", background="#101820", foreground="#d8e2e9", insertbackground="#d8e2e9", state="disabled")
        self.terminal.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(terminal_frame, command=self.terminal.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.terminal.configure(yscrollcommand=scrollbar.set)
        bottom_controls = ttk.Frame(container)
        bottom_controls.grid(row=2, column=2, sticky="nsew", padx=(10, 0), pady=(12, 0))
        ttk.Label(bottom_controls, textvariable=self.zoom_text,
                  font=("TkDefaultFont", 8, "bold")).grid(row=0, column=0, columnspan=3,
                                                            sticky="w", pady=(4, 0))
        ttk.Button(bottom_controls, text="−", width=4, command=lambda: self._zoom_signals(2.0)).grid(row=1, column=0, padx=(0, 3))
        ttk.Button(bottom_controls, text="+", width=4, command=lambda: self._zoom_signals(0.5)).grid(row=1, column=1, padx=3)
        ttk.Button(bottom_controls, text="Live", command=self._go_live).grid(row=1, column=2, padx=(3, 0))
        ttk.Label(bottom_controls, text="Click/drag a plot to inspect a sample",
                  wraplength=240, font=("TkDefaultFont", 8)).grid(row=2, column=0,
                                                                   columnspan=3, sticky="w", pady=(4, 0))
    def _set_recording_state(self, state: str) -> None:
        self.recording_state = state
        settings = {
            "stopped": (("▶  CONTINUE RECORDING" if self.samples else "▶  START RECORDING"), "#198754", "#157347", "normal"),
            "starting": ("▶  STARTING…", "#6c757d", "#5c636a", "disabled"),
            "running": ("▮▮  PAUSE RECORDING", "#dc3545", "#bb2d3b", "normal"),
            "stopping": ("▮▮  STOPPING…", "#6c757d", "#5c636a", "disabled"),
        }
        label, background, active_background, enabled = settings[state]
        self.record_button.configure(text=label, background=background, activebackground=active_background, state=enabled)

    def toggle_recording(self) -> None:
        if self.recording_state == "stopped":
            if self.operation is not None:
                self._log(f"Wait for {self.operation} to finish.")
                return
            self._set_recording_state("starting")
            self.open_device()
        elif self.recording_state == "running":
            self.close_device()

    def reset_history(self) -> None:
        previous_file = self.session_path
        self.history_start_clock = time.monotonic()
        self.samples.clear()
        self.times.clear()
        self.prefix_p = [0]
        self.prefix_n = [0]
        self.prefix_current = [0.0]
        self.prefix_p_hz = [0]
        self.prefix_n_hz = [0]
        self.prefix_p_hz_count = [0]
        self.prefix_n_hz_count = [0]
        self.memory_saturated = False
        self.first_sample_clock = None
        self.cursor_time = None
        self.cursor_follows_live = True
        self.operating_time_text.set("VCO operating points · waiting for samples")
        self.session_path = None
        self.view_start = 0.0
        self.follow_latest = True
        self.transfer_plot.clear_point()
        self.power_plot.clear_point()
        self.resolution_plot.clear_point()
        self._refresh_plots()
        self.terminal.configure(state="normal")
        self.terminal.delete("1.0", "end")
        self.terminal.configure(state="disabled")
        self.recording_path_text.set("Recording starts with the next sample.")
        self.rate_estimate_text.set("Average sample rate: waiting for samples")
        self._draw_capacity_bar()
        self._set_recording_state(self.recording_state)
        self._log(f"History reset. Previous file kept: {previous_file}" if previous_file else "History reset.")

    def _filter_changed(self, *_args: str) -> None:
        try:
            window = self._positive_int(self.filter_window.get(), "Moving average window")
        except ValueError:
            return
        self.filter_size = window
        self._refresh_plots()
        self._update_operating_point()

    def _scroll_signals(self, *args: str) -> None:
        full_end = self._timeline_end()
        visible_seconds = self._visible_seconds()
        maximum = full_end - visible_seconds
        if args[0] == "moveto":
            self.view_start = float(args[1]) * full_end
        elif args[0] == "scroll":
            step = visible_seconds / 10 if args[2] == "units" else visible_seconds * 0.8
            self.view_start += int(args[1]) * step
        self.view_start = min(max(self.view_start, 0.0), maximum)
        self.follow_latest = self.view_start >= maximum - 0.01
        if self.follow_latest:
            self.cursor_follows_live = True
            self.cursor_time = self.times[-1] if self.times else None
        elif self.times:
            self.cursor_follows_live = False
            self.cursor_time = self._nearest_sample_time(self.view_start + visible_seconds / 2)
        self._update_operating_point()
        self._refresh_plots()

    def _nearest_sample_time(self, target_s: float) -> float | None:
        if not self.times:
            return None
        index = bisect.bisect_left(self.times, target_s)
        if index == 0:
            return self.times[0]
        if index >= len(self.times):
            return self.times[-1]
        before, after = self.times[index - 1], self.times[index]
        return before if target_s - before <= after - target_s else after

    def _select_cursor_from_event(self, event: tk.Event) -> None:
        if not self.times:
            return
        plot: SignalPlot = event.widget
        left, right = 78, 16
        plot_width = max(plot.winfo_width() - left - right, 1)
        fraction = min(1.0, max(0.0, (event.x - left) / plot_width))
        target_s = self.view_start + fraction * self._visible_seconds()
        selected_time = self._nearest_sample_time(target_s)
        if selected_time is None:
            return
        self.cursor_time = selected_time
        self.cursor_follows_live = False
        self.follow_latest = False
        self._update_operating_point()
        self._refresh_plots()

    def _zoom_signals(self, factor: float) -> None:
        previous_view = self._visible_seconds()
        self.view_seconds = min(3600.0, max(1.0, previous_view * factor))
        if self.follow_latest:
            self.view_start = max(0.0, self._timeline_end() - self._visible_seconds())
        else:
            center = self.cursor_time if self.cursor_time is not None else self.view_start + previous_view / 2
            self.view_start = max(0.0, center - self.view_seconds / 2)
        self._refresh_plots()

    def _go_live(self) -> None:
        self.follow_latest = True
        self.cursor_follows_live = True
        self.cursor_time = self.times[-1] if self.times else None
        self._update_operating_point()
        self._refresh_plots()

    def _timeline_end(self) -> float:
        return max(self._visible_seconds(), self.times[-1] if self.times else self.view_seconds)

    def _visible_seconds(self) -> float:
        if self.follow_latest and self.times and self.times[-1] > 0:
            return min(self.view_seconds, self.times[-1])
        return self.view_seconds

    def _refresh_plots(self) -> None:
        full_end = self._timeline_end()
        visible_seconds = self._visible_seconds()
        self.zoom_text.set(f"TIME ZOOM · {visible_seconds:g} s")
        maximum = full_end - visible_seconds
        if self.follow_latest:
            self.view_start = maximum
        else:
            self.view_start = min(self.view_start, maximum)
        self.timeline_scroll.set(self.view_start / full_end, (self.view_start + visible_seconds) / full_end)
        for plot in (self.main_plot, self.diff_plot):
            plot.set_view(self.samples, self.times, self.prefix_p, self.prefix_n, self.prefix_current,
                          self.view_start, self.filter_size, visible_seconds, self.cursor_time)

    def _update_operating_point(self) -> None:
        if not self.samples:
            self.operating_time_text.set("VCO operating points · waiting for samples")
            return
        end = len(self.samples)
        sample_index = end - 1 if self.cursor_follows_live or self.cursor_time is None else max(
            0, min(end - 1, bisect.bisect_right(self.times, self.cursor_time) - 1))
        self.cursor_time = self.times[sample_index]
        prefix_end = sample_index + 1
        # Use the moving-average window ending at the selected sample.
        start = max(0, prefix_end - self.filter_size)
        count = prefix_end - start

        def average_frequency(sums: list[int], counts: list[int]) -> float | None:
            frequency_count = counts[prefix_end] - counts[start]
            return (sums[prefix_end] - sums[start]) / count if frequency_count == count else None

        point = OperatingPoint(
            p_uv=(self.prefix_p[prefix_end] - self.prefix_p[start]) / count,
            n_uv=(self.prefix_n[prefix_end] - self.prefix_n[start]) / count,
            p_hz=average_frequency(self.prefix_p_hz, self.prefix_p_hz_count),
            n_hz=average_frequency(self.prefix_n_hz, self.prefix_n_hz_count),
        )
        self.operating_time_text.set(f"VCO operating points · t = {self.cursor_time:.2f} s")
        self.transfer_plot.set_point(point)
        current_ua = (self.prefix_current[prefix_end] - self.prefix_current[start]) / count
        g_us = conductance_us(point.p_uv - point.n_uv, current_ua)
        for plot in (self.power_plot, self.resolution_plot):
            plot.set_operating(g_us, point.p_uv / 1000, point.n_uv / 1000,
                               current_ua, self.applied_sampling_hz)

    def _record_sample(self, p_uv: int, n_uv: int, p_hz: int | None, n_hz: int | None,
                       clock: float, current_nA: int | None = None) -> None:
        if self.first_sample_clock is None:
            self.first_sample_clock = clock
        elapsed = max(0.0, clock - self.first_sample_clock)
        if self.times:
            elapsed = max(elapsed, self.times[-1])
        current_ua = current_nA / 1000 if current_nA is not None else self.applied_current_ua
        sample = Sample(elapsed, p_uv, n_uv, p_hz, n_hz, current_ua)
        self.samples.append(sample)
        self.times.append(elapsed)
        self.prefix_p.append(self.prefix_p[-1] + p_uv)
        self.prefix_n.append(self.prefix_n[-1] + n_uv)
        self.prefix_current.append(self.prefix_current[-1] + sample.current_ua)
        self.prefix_p_hz.append(self.prefix_p_hz[-1] + (p_hz or 0))
        self.prefix_n_hz.append(self.prefix_n_hz[-1] + (n_hz or 0))
        self.prefix_p_hz_count.append(self.prefix_p_hz_count[-1] + (p_hz is not None))
        self.prefix_n_hz_count.append(self.prefix_n_hz_count[-1] + (n_hz is not None))
        self.firmware_is_demo = True
        self._update_capacity()
        self._update_rate_estimate()
        if self.cursor_follows_live:
            self.cursor_time = sample.time_s
        self._persist_sample(sample)
        self._update_operating_point()
        self._refresh_plots()

    def _update_capacity(self) -> None:
        if self.memory_saturated:
            return
        used = len(self.samples) * 2
        capacity = 16 * 1024
        if used >= capacity:
            self.memory_saturated = True
        self._draw_capacity_bar()

    def _draw_capacity_bar(self) -> None:
        canvas = getattr(self, "capacity_bar", None)
        if canvas is None:
            return
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 1), max(canvas.winfo_height(), 1)
        canvas.create_rectangle(0, 0, width, height, fill="#263946", outline="")
        if self.memory_saturated:
            canvas.create_rectangle(0, 0, width, height, fill="#e74c3c", outline="")
            canvas.create_text(width / 2, height / 2, text="SAMPLE BANK FULL · 16,384 / 16,384 bytes",
                               fill="#ffffff", font=("TkDefaultFont", 8, "bold"))
            return
        fraction = min(1.0, len(self.samples) / 8192)
        colors = ("#1c4366", "#33b0a7", "#f4d56c", "#e4644d")
        scaled = fraction * (len(colors) - 1)
        segment = min(int(scaled), len(colors) - 2)
        amount = scaled - segment
        start = tuple(int(colors[segment][i:i + 2], 16) for i in (1, 3, 5))
        end = tuple(int(colors[segment + 1][i:i + 2], 16) for i in (1, 3, 5))
        fill = "#" + "".join(f"{round(a + (b - a) * amount):02x}" for a, b in zip(start, end))
        canvas.create_rectangle(0, 0, width * fraction, height, fill=fill, outline="")
        canvas.create_text(width / 2, height / 2,
                           text=f"{len(self.samples) * 2:,} / 16,384 bytes · {fraction * 100:.1f}%",
                           fill="#ffffff", font=("TkDefaultFont", 8, "bold"))

    def _update_rate_estimate(self) -> None:
        try:
            window = self._positive_int(self.rate_window.get(), "Sampling-rate window")
        except ValueError:
            return
        count = min(window, len(self.times))
        if count < 2:
            self.rate_estimate_text.set("Average sample rate: waiting for 2 samples")
            return
        elapsed = self.times[-1] - self.times[-count]
        rate_hz = (count - 1) / elapsed if elapsed > 0 else 0.0
        if rate_hz <= 0:
            self.rate_estimate_text.set("Average sample rate: measuring…")
            return
        duration_s = (16 * 1024 / 2) / rate_hz
        duration = f"{duration_s / 3600:.2f} h" if duration_s >= 3600 else f"{duration_s / 60:.1f} min"
        self.rate_estimate_text.set(
            f"Average rate (last {count}): {rate_hz:.3g} Hz · 16 KB holds about {duration}"
        )

    def _rate_window_changed(self, *_args: str) -> None:
        self._update_rate_estimate()

    def _persist_sample(self, sample: Sample) -> None:
        try:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            if self.session_path is None:
                new_path = OUT_DIR / datetime.now().strftime("session_%Y_%m_%d_%H%M%S_%f.csv")
                with new_path.open("w", newline="") as stream:
                    csv.writer(stream).writerow(("time_s", "vco_p_uV", "vco_n_uV", "delta_v_uV", "conductance_uS", "vco_p_Hz", "vco_n_Hz", "injected_current_uA"))
                self.session_path = new_path
                self.recording_path_text.set(f"Recording: {self.session_path}")
            with self.session_path.open("a", newline="") as stream:
                conductance = conductance_us(sample.delta_uv, sample.current_ua)
                csv.writer(stream).writerow((f"{sample.time_s:.6f}", sample.p_uv, sample.n_uv, sample.delta_uv, f"{conductance:.6g}" if conductance is not None else "", sample.p_hz if sample.p_hz is not None else "", sample.n_hz if sample.n_hz is not None else "", f"{sample.current_ua:g}"))
        except OSError as error:
            self._log(f"Could not write recording: {error}")

    def save_csv(self) -> None:
        if not self.samples:
            self._log("No samples to save yet.")
            return
        try:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            filename = filedialog.asksaveasfilename(
                parent=self.root,
                title="Save GSR recording",
                initialdir=str(OUT_DIR),
                initialfile=datetime.now().strftime("%Y_%m_%d_%H%M.csv"),
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv")],
            )
            if not filename:
                return
            output_path = Path(filename)
            if self.session_path and output_path.resolve() == self.session_path.resolve():
                self._log("Choose another filename; the session file is still recording.")
                return
            with output_path.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("time_s", "vco_p_uV", "vco_n_uV", "delta_v_uV", "conductance_uS", "injected_current_uA"))
                for sample in self.samples:
                    conductance = conductance_us(sample.delta_uv, sample.current_ua)
                    writer.writerow((f"{sample.time_s:.6f}", sample.p_uv, sample.n_uv, sample.delta_uv, f"{conductance:.6g}" if conductance is not None else "", f"{sample.current_ua:g}"))
            self._log(f"Saved {len(self.samples)} samples to {output_path}")
        except (OSError, tk.TclError) as error:
            self._log(f"Could not save CSV: {error}")

    def _show_message(self, message: str, source: str = "gui") -> None:
        """Keep human-facing guidance visible independently of terminal output."""
        key = (source, message)
        if key == self.last_message:
            return
        self.last_message = key
        self.messages.configure(state="normal")
        stamp = datetime.now().strftime("%H:%M:%S")
        label = "UART [i]" if source == "uart" else "GUI"
        self.messages.insert("end", f"{stamp}  {label}  {message}\n", source)
        lines = int(self.messages.index("end-1c").split(".")[0])
        if lines > MESSAGE_LINES:
            self.messages.delete("1.0", f"{lines - MESSAGE_LINES + 1}.0")
        self.messages.see("end")
        self.messages.configure(state="disabled")

    def _log(self, message: str, *, message_source: str | None = "gui") -> None:
        message = message.rstrip("\r\n")
        if not message:
            return
        if message.startswith("[i]"):
            self._show_message(message[3:].strip(), "uart")
        elif message_source is not None:
            self._show_message(message, message_source)
        self.terminal.configure(state="normal")
        self.terminal.insert("end", message + "\n")
        lines = int(self.terminal.index("end-1c").split(".")[0])
        if lines > TERMINAL_LINES:
            self.terminal.delete("1.0", f"{lines - TERMINAL_LINES + 1}.0")
        self.terminal.see("end")
        self.terminal.configure(state="disabled")

    @staticmethod
    def _positive_int(value: str, name: str) -> int:
        if not re.fullmatch(r"[0-9]+", value.strip()) or int(value) <= 0:
            raise ValueError(f"{name} must be a positive whole number.")
        return int(value)

    def _current_slider_changed(self, _value: str) -> None:
        code = int(self.current_code.get())
        self.current_selection.set(f"Code {code}  ·  {code * IDAC_STEP_NA / 1000:.2f} µA")

    def _sampling_slider_changed(self, _value: str) -> None:
        rate = SAMPLING_RATES_HZ[self.sampling_index.get()]
        serial_ceiling_hz = max(self.configured_freq // 20, 1) / 900
        suffix = " · UART may skip samples" if rate > serial_ceiling_hz else ""
        self.sampling_selection.set(f"Selected: {rate:g} Hz{suffix}")

    def _sampling_slider_released(self, _event: tk.Event) -> None:
        if self.recording_state == "running" and self.firmware_is_demo:
            self.apply_sampling_rate()
        else:
            rate = SAMPLING_RATES_HZ[self.sampling_index.get()]
            self.sampling_status.set(f"Startup rate {rate:g} Hz will be used after Build and Record.")
            self._log(self.sampling_status.get())

    def _slider_released(self, _event: tk.Event) -> None:
        if self.project.get() == "gsr/demo":
            try:
                current_na = self._requested_current_na()
                if read_define("INJECTED_CURRENT_NA") != current_na:
                    write_defines({"INJECTED_CURRENT_NA": current_na}, DEMO_SOURCE)
                if self.recording_state != "running":
                    self.current_status.set(f"Startup current saved: {current_na / 1000:.2f} µA")
                    self._log(f"Startup current saved: {current_na / 1000:.2f} µA. Build to apply it to the firmware image.")
            except (OSError, ValueError) as error:
                self._log(f"Could not save the selected startup current: {error}")
        if self.recording_state == "running" and self.firmware_is_demo:
            if self.run_pending:
                self.startup_current_pending = True
                self.current_status.set("Applying selected current when GDB finishes starting…")
                self._log(self.current_status.get())
            else:
                self.apply_current()

    def _requested_current_na(self) -> int:
        code = self.current_code.get()
        if not 1 <= code <= IDAC_MAX_CODE:
            raise ValueError("iDAC code must be between 1 and 255.")
        return code * IDAC_STEP_NA

    def _finish_current_request(self, status: str) -> None:
        self.pending_current_na = None
        self.current_scale.configure(state="normal")
        self.current_status.set(status)
        self._log(status)

    def _current_request_timeout(self, request_id: int) -> None:
        if request_id == self.current_request_id and self.pending_current_na is not None:
            self._finish_current_request("No firmware confirmation; check terminal output")
            self._log("Current change was not confirmed by the firmware.")

    def _finish_sampling_request(self, status: str) -> None:
        self.pending_sample_millihz = None
        self.sampling_scale.configure(state="normal")
        self.sampling_status.set(status)
        self._log(status)

    def _sampling_request_timeout(self, request_id: int) -> None:
        if request_id == self.sample_request_id and self.pending_sample_millihz is not None:
            rate = self.pending_sample_millihz / 1000
            self._finish_sampling_request("No firmware confirmation; check terminal output")
            self._log(f"Sampling rate {rate:g} Hz was not confirmed by the firmware.")

    def apply_sampling_rate(self) -> None:
        if self.recording_state != "running" or not self.firmware_is_demo or self.serial_port is None:
            self._log("Record with gsr/demo and wait for its startup message before applying sample rate.")
            return
        if self.run_pending or self.pending_current_na is not None or self.pending_sample_millihz is not None:
            self._log("Wait for the runtime setting operation to finish.")
            return
        rate = SAMPLING_RATES_HZ[self.sampling_index.get()]
        rate_millihz = round(rate * 1000)
        hardware_rate = max(1, int(rate))
        if hardware_rate > self.configured_freq // 100:
            self.sampling_status.set(f"{rate:g} Hz exceeds the configured MCU timing limit.")
            self._log("Sampling update rejected: period must be at least 100 MCU cycles.")
            return
        if rate < 1 and self.configured_freq * 1000 // rate_millihz > 0xFFFFFFFF:
            self.sampling_status.set("Rate is too low for the firmware cycle timer.")
            self._log("Sampling update rejected: sub-Hz period exceeds the 32-bit cycle timer.")
            return
        if rate_millihz == round(self.applied_sampling_hz * 1000):
            self.sampling_status.set(f"Firmware sampling rate: {rate:g} Hz")
            return
        self.sample_request_id += 1
        request_id = self.sample_request_id
        self.pending_sample_millihz = rate_millihz
        self.sampling_scale.configure(state="disabled")
        self.sampling_status.set(f"Applying {rate:g} Hz via JTAG…")
        self._log(f"Applying {rate:g} Hz via JTAG; waiting for the firmware to confirm it.")

        def worker() -> None:
            try:
                address = write_runtime_word("gsr_sample_rate_millihz", rate_millihz)
                self.events.put(("sampling_write_done", request_id, rate_millihz, address, None))
            except (OSError, ValueError, RuntimeError) as error:
                self.events.put(("sampling_write_done", request_id, rate_millihz, None, str(error)))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(25000, self._sampling_request_timeout, request_id)

    def apply_current(self) -> None:
        if self.recording_state != "running" or not self.firmware_is_demo or self.serial_port is None:
            self._log("Record with gsr/demo and wait for its startup message before applying current.")
            return
        if self.run_pending or self.pending_current_na is not None or self.pending_sample_millihz is not None:
            self._log("Wait for the runtime setting operation to finish.")
            return
        try:
            current_na = self._requested_current_na()
        except ValueError as error:
            self._log(f"Current change failed: {error}")
            return
        if current_na == round(self.applied_current_ua * 1000):
            self.current_status.set(f"Firmware current: {current_na / 1000:.2f} µA")
            return
        self.current_request_id += 1
        request_id = self.current_request_id
        self.pending_current_na = current_na
        self.current_scale.configure(state="disabled")
        self.current_status.set(f"Applying {current_na / 1000:.2f} µA via JTAG…")
        self._log(f"Applying {current_na / 1000:.2f} µA via JTAG; waiting for the firmware to confirm it.")

        def worker() -> None:
            try:
                address = write_runtime_word("gsr_injected_current_nA", current_na)
                self.events.put(("current_write_done", request_id, current_na, address, None))
            except (OSError, ValueError, RuntimeError) as error:
                self.events.put(("current_write_done", request_id, current_na, None, str(error)))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(25000, self._current_request_timeout, request_id)

    def _apply_startup_current_if_ready(self) -> None:
        if not self.startup_current_pending or self.run_pending or not self.gdb_opened:
            return
        if self.recording_state != "running" or not self.firmware_is_demo or self.serial_port is None:
            return
        self.startup_current_pending = False
        if self._requested_current_na() != round(self.applied_current_ua * 1000):
            self.apply_current()
        else:
            self.current_status.set(f"Firmware current: {self.applied_current_ua:.2f} µA")

    def _run_make(self, tag: str, *arguments: str, on_success=None) -> None:
        if self.shutting_down:
            return
        if tag != "run":
            if self.operation is not None:
                self._log(f"Wait for {self.operation} to finish.")
                return
            self.operation = tag
        command = ["make", *arguments]
        run_generation = self.connection_generation if tag == "run" else None
        self._log("$ " + " ".join(command), message_source=None)
        self._show_message({"build": "Building firmware…", "board": "Configuring the board clock…",
                            "open": "Opening JTAG and UART…", "run": "Loading and starting firmware…",
                            "close": "Closing the device…"}.get(tag, f"Starting {tag}…"))

        def worker() -> None:
            code = 127
            process = None
            try:
                if self.shutting_down:
                    return
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
                with self.process_lock:
                    self.processes[tag] = process
                if self.shutting_down or (tag == "run" and run_generation != self.connection_generation):
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if process.stdout:
                    for line in process.stdout:
                        self.events.put(("output", line.rstrip()))
                code = process.wait()
            except OSError as error:
                self.events.put(("output", f"Could not start make: {error}"))
            finally:
                with self.process_lock:
                    if self.processes.get(tag) is process:
                        self.processes.pop(tag, None)
                self.events.put(("make_done", tag, code, on_success))

        threading.Thread(target=worker, daemon=True).start()

    def configure_board(self) -> None:
        if self.recording_state != "stopped":
            self._log("Pause recording before configuring the board.")
            return
        try:
            frequency = self._positive_int(self.mcu_freq.get(), "MCU frequency")
            if frequency < 20:
                raise ValueError("MCU frequency must be at least 20 Hz for the UART baud rate.")
        except ValueError as error:
            self._log(str(error))
            return

        def configured() -> None:
            self.configured_freq = frequency
            self.baud.set(str(frequency // 20))
            self._sampling_slider_changed(str(self.sampling_index.get()))
            self.board_reset_notice.set("MCU frequency configured. Please reset the hardware before starting a recording.")
            self._log(self.board_reset_notice.get())
            try:
                for source in (DEMO_SOURCE, TEST_SOURCE):
                    write_defines({"SYS_FCLK_HZ": frequency}, source)
                self._log(f"Board set to {frequency} Hz; rebuild the application to apply its timer setting.")
            except (OSError, ValueError) as error:
                self._log(f"Board configured, but firmware source update failed: {error}")
            if self.serial_port:
                self._stop_serial()
                self._open_serial()

        self._run_make("board", "board_freq", f"PLL_FREQ={frequency}", on_success=configured)

    def build(self) -> None:
        if self.recording_state != "stopped":
            self._log("Pause recording before building.")
            return
        if self.operation is not None:
            self._log(f"Wait for {self.operation} to finish.")
            return
        project = self.project.get()
        if not project or not (APPLICATIONS / project).is_dir():
            self._log("Select a valid application before building.")
            return
        applied_current = None
        if project in ("gsr/demo", "test_VCO_counter"):
            try:
                sampling = SAMPLING_RATES_HZ[self.sampling_index.get()]
                if project == "test_VCO_counter" and sampling < 1:
                    raise ValueError("Fractional rates require gsr/demo; select at least 1 Hz for test_VCO_counter.")
                hardware_rate = max(1, int(sampling))
                if hardware_rate > self.configured_freq // 100:
                    raise ValueError("Sampling period must be at least 100 MCU cycles.")
                if sampling < 1 and self.configured_freq * 1000 // round(sampling * 1000) > 0xFFFFFFFF:
                    raise ValueError("The sub-Hz period exceeds the firmware's 32-bit cycle timer.")
                changes = {"VCO_FS_HZ": hardware_rate, "SYS_FCLK_HZ": self.configured_freq}
                if project == "gsr/demo":
                    changes["VCO_SAMPLE_RATE_MILLIHZ"] = round(sampling * 1000)
                    changes["INJECTED_CURRENT_NA"] = self._requested_current_na()
                    applied_current = changes["INJECTED_CURRENT_NA"] / 1000
                write_defines(changes, DEMO_SOURCE if project == "gsr/demo" else TEST_SOURCE)
                if project == "gsr/demo" and sampling < 1:
                    self._log(f"Sub-Hz output: VCO refreshes at 1 Hz; UART emits about {sampling:g} samples/s.")
                if sampling > max(self.configured_freq // 20, 1) / 900:
                    self._log("Selected rate may exceed UART throughput; measurements can be skipped.")
            except (OSError, ValueError) as error:
                self._log(f"Build not started: {error}")
                return

        def built() -> None:
            if project in ("gsr/demo", "test_VCO_counter"):
                self.applied_sampling_hz = sampling
            if applied_current is not None:
                self.applied_current_ua = applied_current
                self.current_status.set(f"Built startup current: {applied_current:.2f} µA")
                self._log(f"Startup current set to {applied_current * 1000:.0f} nA ({int(applied_current * 1000 / IDAC_STEP_NA)} iDAC steps).")
            self._show_memory()

        self.memory.set("Building…")
        self._run_make("build", "jtag_build", f"PROJECT={project}", on_success=built)

    def _show_memory(self) -> None:
        def worker() -> None:
            try:
                result = subprocess.run(
                    [sys.executable, str(MEMORY_SCRIPT)],
                    cwd=ROOT / "hw" / "vendor" / "x-heep",
                    capture_output=True, text=True, timeout=15, check=True,
                )
                banks = [line.strip() for line in result.stdout.splitlines() if MEMORY_BANK.fullmatch(line.strip())]
                summary = "\n".join(banks) if banks else "Memory bank report unavailable; see terminal output."
            except (OSError, subprocess.SubprocessError) as error:
                summary = f"Memory bank report unavailable: {error}"
            self.events.put(("memory", summary))

        threading.Thread(target=worker, daemon=True).start()

    def open_device(self) -> None:
        if self.operation is not None:
            self._log(f"Wait for {self.operation} to finish.")
            return
        if self.serial_port and self.serial_port.is_open:
            self._log("Serial port is already open.")
            return
        self.open_requested = True
        self.firmware_is_demo = False
        self.current_status.set("Firmware current: connecting…")
        self.connection_generation += 1
        generation = self.connection_generation
        self._run_make("open", "jtag_open", "GUI_SERIAL=1", on_success=lambda: self.root.after(600, self._finish_open, generation))

    def _finish_open(self, generation: int) -> None:
        if self.shutting_down or generation != self.connection_generation or self.recording_state != "starting":
            return
        log_path = ROOT / ".openocd.log"
        if log_path.is_file():
            try:
                for line in log_path.read_text(errors="replace").splitlines()[-8:]:
                    self._log("OpenOCD: " + line, message_source=None)
            except OSError as error:
                self._log(f"Could not read OpenOCD output: {error}")
        try:
            pid = int((ROOT / ".openocd.pid").read_text().strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            self._log("OpenOCD exited; check its output above.")
            self.close_device()
            return
        if self._open_serial():
            self.run()
        else:
            self.close_device()

    def _open_serial(self) -> bool:
        try:
            baud = self._positive_int(self.baud.get(), "Baud rate")
            port = serial.Serial(self.port.get().strip(), baud, timeout=0.2, write_timeout=1)
            self.serial_port = port
        except (serial.SerialException, ValueError, OSError) as error:
            self._log(f"Serial open failed: {error}")
            return False
        self.stop_reader = threading.Event()
        threading.Thread(target=self._read_serial, args=(port, self.stop_reader), daemon=True).start()
        self._log(f"Serial connected: {port.port} at {port.baudrate} baud")
        return True

    def _read_serial(self, port: serial.Serial, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                raw = port.readline()
            except (serial.SerialException, OSError, TypeError) as error:
                if not stop_event.is_set():
                    self.events.put(("output", f"Serial read failed: {error}"))
                break
            if not raw or stop_event.is_set():
                continue
            line = raw.decode("utf-8", errors="replace").strip().strip("\x00")
            if line:
                received_at = time.monotonic()
                self.events.put(("serial_output", line, received_at))
                record = SAMPLE_RECORD.fullmatch(line)
                if record:
                    _sample_number, p_hz, n_hz, current_na = map(int, record.groups())
                    p_hz, n_hz = int(p_hz), int(n_hz)
                    p_uv = nominal_voltage_uv(self.transfer_curve, p_hz)
                    n_uv = nominal_voltage_uv(self.transfer_curve, n_hz)
                    self.events.put(("sample", p_uv, n_uv, p_hz, n_hz, received_at, int(current_na)))

    def _stop_serial(self) -> None:
        self.stop_reader.set()
        if self.serial_port:
            try:
                self.serial_port.close()
            except (serial.SerialException, OSError):
                pass
            self.serial_port = None

    def run(self) -> None:
        if self.operation is not None:
            self._log(f"Wait for {self.operation} to finish.")
            self.close_device()
            return
        with self.process_lock:
            running = "run" in self.processes
        if running or self.run_pending:
            self._log("GDB is already running.")
            self.close_device()
            return
        self.run_pending = True
        self.gdb_opened = False
        self.startup_current_pending = self.project.get() == "gsr/demo"
        self._run_make("run", "jtag_run", "GUI_MODE=1")
        self._set_recording_state("running")

    def _terminate_processes(self) -> None:
        with self.process_lock:
            processes = list(self.processes.values())
        for process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass

    def close_device(self) -> None:
        if self.operation is not None:
            self._log(f"Wait for {self.operation} to finish.")
            return
        self._set_recording_state("stopping")
        self.close_completed = False
        self.current_request_id += 1
        self.sample_request_id += 1
        if self.pending_current_na is not None:
            self._finish_current_request("Firmware current: device stopped")
        else:
            self.current_status.set("Firmware current: device stopped")
        if self.pending_sample_millihz is not None:
            self._finish_sampling_request("Firmware sampling rate: device stopped")
        else:
            self.sampling_status.set("Firmware sampling rate: device stopped")
        self._stop_serial()
        self.firmware_is_demo = False
        self.gdb_opened = False
        self.startup_current_pending = False
        self.connection_generation += 1
        self._terminate_processes()
        self._run_make("close", "jtag_close")

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "shutdown_done":
                self.root.destroy()
                return
            if self.shutting_down:
                continue
            if event[0] == "output":
                self._log(event[1], message_source=None)
            elif event[0] == "serial_output":
                _, line, received_at = event
                if received_at >= self.history_start_clock:
                    if SAMPLE_RECORD.fullmatch(line):
                        continue
                    uart_line = line
                    if line.startswith("[i]"):
                        line = line[3:].strip()
                    if line == "GSR demo ready.":
                        self.firmware_is_demo = True
                        self._apply_startup_current_if_ready()
                    banner = GSR_BANNER.match(line)
                    if banner:
                        self.firmware_is_demo = True
                        self.applied_sampling_hz = float(banner.group(1))
                        self.sampling_index.set(min(range(len(SAMPLING_RATES_HZ)),
                                                    key=lambda index: abs(math.log(SAMPLING_RATES_HZ[index] / self.applied_sampling_hz))))
                        self._sampling_slider_changed(str(self.sampling_index.get()))
                        self.sampling_status.set(f"Firmware sampling rate: {self.applied_sampling_hz:g} Hz")
                        self.applied_current_ua = int(banner.group(2)) / 1000
                        requested_current_na = self._requested_current_na()
                        self._current_slider_changed(str(self.current_code.get()))
                        self.startup_current_pending = requested_current_na != int(banner.group(2))
                        self.current_status.set(
                            f"Applying saved startup current: {requested_current_na / 1000:.2f} µA"
                            if self.startup_current_pending else
                            f"Firmware current: {self.applied_current_ua:.2f} µA")
                        self._apply_startup_current_if_ready()
                        if not self.samples:
                            self.resolution_plot.set_operating(None, 0.0, 0.0,
                                                               self.applied_current_ua, self.applied_sampling_hz)
                    current_ack = CURRENT_ACK.match(line)
                    if current_ack:
                        current_na = int(current_ack.group(1))
                        self.applied_current_ua = current_na / 1000
                        if self.startup_current_pending:
                            self._apply_startup_current_if_ready()
                        elif self.pending_current_na == current_na:
                            self._finish_current_request(f"Applied: {current_na / 1000:.2f} µA · code {current_na // IDAC_STEP_NA}")
                        elif self.pending_current_na is None:
                            self.current_code.set(current_na // IDAC_STEP_NA)
                            self._current_slider_changed(str(self.current_code.get()))
                            self.current_status.set(f"Firmware current: {current_na / 1000:.2f} µA")
                    if line.startswith("Current change failed:") and self.pending_current_na is not None:
                        self._finish_current_request(line)
                    sampling_ack = SAMPLING_ACK.match(line)
                    if sampling_ack:
                        self.applied_sampling_hz = float(sampling_ack.group(1)) / (1000 if sampling_ack.group(2) == "mHz" else 1)
                        self.sampling_index.set(min(range(len(SAMPLING_RATES_HZ)),
                                                    key=lambda index: abs(math.log(SAMPLING_RATES_HZ[index] / self.applied_sampling_hz))))
                        self._sampling_slider_changed(str(self.sampling_index.get()))
                        if self.pending_sample_millihz == round(self.applied_sampling_hz * 1000):
                            self._finish_sampling_request(f"Applied: {self.applied_sampling_hz:g} Hz")
                        elif self.pending_sample_millihz is None:
                            self.sampling_status.set(f"Firmware sampling rate: {self.applied_sampling_hz:g} Hz")
                    if line.startswith("Sampling change failed:") and self.pending_sample_millihz is not None:
                        self._finish_sampling_request(line)
                        self.sampling_index.set(min(range(len(SAMPLING_RATES_HZ)),
                                                    key=lambda index: abs(math.log(SAMPLING_RATES_HZ[index] / self.applied_sampling_hz))))
                        self._sampling_slider_changed(str(self.sampling_index.get()))
                    self._log(uart_line, message_source=None)
            elif event[0] == "sample":
                _, p_uv, n_uv, p_hz, n_hz, clock, current_na = event
                if clock >= self.history_start_clock:
                    self.firmware_is_demo = True
                    if current_na is not None and self.startup_current_pending:
                        self.applied_current_ua = current_na / 1000
                        self._apply_startup_current_if_ready()
                    elif current_na is not None and self.pending_current_na == current_na:
                        self.applied_current_ua = current_na / 1000
                        self._finish_current_request(f"Applied: {current_na / 1000:.2f} µA · code {current_na // IDAC_STEP_NA}")
                    elif current_na is not None and self.pending_current_na is None:
                        if current_na != round(self.applied_current_ua * 1000):
                            self.applied_current_ua = current_na / 1000
                            self.current_code.set(current_na // IDAC_STEP_NA)
                            self._current_slider_changed(str(self.current_code.get()))
                        self.current_status.set(f"Firmware current: {current_na / 1000:.2f} µA")
                    self._record_sample(p_uv, n_uv, p_hz, n_hz, clock, current_na)
            elif event[0] == "current_write_done":
                _, request_id, current_na, address, error = event
                if request_id == self.current_request_id and self.pending_current_na == current_na:
                    if error:
                        self._finish_current_request("Current change failed; see terminal output")
                        self._log(f"JTAG current write failed: {error}")
                    else:
                        self._log(f"JTAG wrote {current_na} nA to 0x{address:08x}; waiting for firmware confirmation.")
                        self.root.after(6000, self._current_request_timeout, request_id)
            elif event[0] == "sampling_write_done":
                _, request_id, rate_millihz, address, error = event
                if request_id == self.sample_request_id and self.pending_sample_millihz == rate_millihz:
                    if error:
                        self._finish_sampling_request("Sampling update failed; see terminal output")
                        self._log(f"JTAG sampling-rate write failed: {error}")
                    else:
                        self._log(f"JTAG wrote {rate_millihz / 1000:g} Hz to 0x{address:08x}; waiting for firmware confirmation.")
                        self.root.after(6000, self._sampling_request_timeout, request_id)
            elif event[0] == "memory":
                self.memory.set(event[1])
                self._log(event[1].replace("\n", " | "), message_source=None)
            elif event[0] == "make_done":
                _, tag, code, on_success = event
                if tag == "run":
                    self.run_pending = False
                else:
                    self.operation = None
                if tag == "run" and code == 0:
                    self.gdb_opened = True
                    self.board_reset_notice.set("")
                    self._log("Firmware started; waiting for UART feedback.")
                    self._apply_startup_current_if_ready()
                else:
                    if tag == "run":
                        self.gdb_opened = False
                    self._log(f"{tag.capitalize()} {'finished' if code == 0 else f'exited with status {code}'}")
                if tag == "build" and code != 0:
                    self.memory.set("Build failed; see terminal output below.")
                if tag == "open" and code != 0 and self.recording_state == "starting":
                    self.close_device()
                if tag == "run" and code != 0 and self.recording_state == "running":
                    self.close_device()
                if tag == "close":
                    self.close_completed = True
                    if not self.run_pending:
                        self._set_recording_state("stopped")
                if tag == "run" and self.recording_state == "stopping" and self.close_completed:
                    self._set_recording_state("stopped")
                if code == 0 and on_success:
                    on_success()
        self.root.after(50, self._drain_events)

    def _on_close(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        self._stop_serial()
        self._terminate_processes()

        def cleanup() -> None:
            if self.open_requested:
                try:
                    subprocess.run(["make", "jtag_close"], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, start_new_session=True)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self.events.put(("shutdown_done",))

        threading.Thread(target=cleanup, daemon=True).start()


def main() -> None:
    root = tk.Tk()
    VCOGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
