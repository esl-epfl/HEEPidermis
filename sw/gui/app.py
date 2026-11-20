#!/usr/bin/env python3
"""Multi-device acquisition and comparison workspace.

Layout (wide-screen, left to right):

* A resizable left pane with collapsible menus: device selection, the signal
  table (which device signal goes to which plot row/axis and with which
  style), and one configuration menu per device.
* A resizable right pane with stacks of time plots that share one time axis
  and cursor.
* A bottom bar with per-device reports on the left and shared navigation
  (time zoom, live, save) on the right.

Device connection, interrogation and configuration knowledge lives in
``sw/gui/sdk/<device>``; each SDK exposes a panel that renders its own
configuration menu and report widgets into this workspace and provides its
signals through ``signals()``/``get_series()``.

Launch from the repository root with ``make gui``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import bisect
import csv
import json
import math
from pathlib import Path
import sys
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
import traceback
import time

from .common.plots import TimePlot, TimeViewport, Trace
from .common import features
from .common.session import read_session, write_session
from .common.timing import AlignedSnapshot
from .common.background import submit, compact_series
from .common.eda import ALGORITHMS
from .common.theme import (UI_BACKGROUND, UI_BORDER, UI_MUTED, UI_PLOT, UI_SURFACE,
                           UI_SURFACE_RAISED, UI_TEXT, configure_style)
from .common.widgets import Collapsible, ScrollPane, Tooltip
from .sdk.heepidermis.panel import VCOGui
from .sdk.biopac.panel import BiopacGUI
from .sdk.tests.panel import TestsPanel
from .sdk.tests.recording import MARKER_COLORS  # Standard event-marker palette.

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_CONFIG = ROOT / "sw" / "gui" / "configs" / "workspace" / "last.json"

AMPLIFY_CHOICES = ("0.01x", "0.1x", "1x", "10x", "100x", "normalize", "normalize row/axis")
TRANSFORM_CHOICES = ("line", "markers", "steps")
COMPARISON_CHOICES = ("NRMSE (A vs B)", "Correlation (A vs B)", "Difference (A − B)", "Ratio (A / B)")
FEATURE_CHOICES = ("Phasic peak detection", "Peak-to-peak amplitude",
                   "Peak rising time", "Peak falling time")
PLOT_ROWS_MAX = 16
REFRESH_MS = 200
STATUS_COLORS = {"ok": "#2ea043", "warning": "#f2bd62", "off": "#dc3545"}


@dataclass
class SignalRow:
    """One row of the signal-selection table."""

    device: str = "heepidermis"
    signal: str = ""
    stack: int = 1
    axis: str = "Left"
    transform: str = "line"
    amplify: str = "1x"
    color: str = ""  # Assigned on creation; as distinct as possible per row.
    alpha: float = 1.0
    zorder: int = 0
    hidden: bool = False  # Display-only: hidden rows stay configured and saved.
    comparison: str = COMPARISON_CHOICES[0]
    source_a: str = ""
    source_b: str = ""
    # Y-axis scaling: "Auto" fits the visible data, "Fixed" pins the axis to
    # [y_min, y_max]. Defaults depend on the row kind (see _range_default).
    y_scale: str = "Auto"
    y_min: float = 0.0
    y_max: float = 1.0
    range_set: bool = False  # True once the user edits any range field.
    # Feature-extraction settings (device == "features"); the algorithm and
    # peak parameters can override the shared decomposition for this feature.
    algorithm: str = ""
    smoothing: float = 1.0
    prominence: float = 0.01


def range_default(row: SignalRow):
    """Sensible axis range for a row kind; device signals auto-range."""
    if row.device == "comparison":
        if "NRMSE" in row.signal:
            return "Fixed", 0.0, 100.0  # dB error metric, non-negative.
        if "Correlation" in row.signal:
            return "Fixed", -1.0, 1.0  # Pearson coefficient bounds.
    return "Auto", 0.0, 1.0


DEVICES = (
    {"key": "heepidermis", "name": "HEEPidermis", "panel": VCOGui,
     "description": "X-HEEP VCO GSR board over UART/JTAG (dLC, iDAC, sampling)."},
    {"key": "biopac", "name": "BIOPAC", "panel": BiopacGUI,
     "description": "MP160 + EDA100C / BioNomadix over native Linux UDP."},
    {"key": "tests", "name": "Tests", "panel": TestsPanel,
     "description": "Cognitive tests (sw/gui/tests): events as green/red/white markers on all plots."},
    {"key": "shimmer", "name": "Shimmer", "panel": None,
     "description": "Not implemented yet; SDK slot reserved in sw/gui/sdk/shimmer."},
    {"key": "empatica", "name": "Empatica", "panel": None,
     "description": "Not implemented yet; SDK slot reserved in sw/gui/sdk/empatica."},
)


def amplify_values(values, mode):
    """Apply the per-signal amplification chosen in the signal table."""
    if not mode or mode == "1x":
        return values, False
    if mode == "normalize":
        peak = 0.0
        for v in values:
            try:
                value = abs(float(v))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                peak = max(peak, value)
        if peak <= 0:
            return values, False
        return [v / peak for v in values], True
    if mode == "normalize row/axis":
        return values, True  # Resolved per stack+axis group after all traces exist.
    try:
        factor = float(mode.rstrip("x"))
    except ValueError:
        return values, False
    return [v * factor for v in values], False


def _channels(color: str):
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _next_color(used_colors) -> str:
    """Pick a random color as different as possible from the ones already used.

    Random HSV candidates are scored by their smallest RGB distance to the
    used colors; the most distant candidate wins, so rows stay distinguishable
    while the palette varies from session to session.
    """
    import colorsys
    import random

    used = [_channels(color) for color in used_colors if color]
    if not used:
        hue = random.random()
    else:
        best, best_distance = None, -1.0
        for _ in range(64):
            hue = random.random()
            rgb = colorsys.hsv_to_rgb(hue, random.uniform(0.55, 0.85),
                                      random.uniform(0.75, 0.95))
            candidate = tuple(round(channel * 255) for channel in rgb)
            distance = min(sum((a - b) ** 2 for a, b in zip(candidate, other))
                           for other in used)
            if distance > best_distance:
                best, best_distance = candidate, distance
        if best is not None:
            return "#{:02x}{:02x}{:02x}".format(*best)
    rgb = colorsys.hsv_to_rgb(hue, random.uniform(0.55, 0.85), random.uniform(0.75, 0.95))
    return "#{:02x}{:02x}{:02x}".format(*(round(channel * 255) for channel in rgb))


def derived_series(op, a, b, window_s=2.0, step_s=0.1):
    """Compare two series on a common grid; returns (times, values).

    Both inputs are linearly interpolated onto a 20 Hz grid over their shared
    span, then a sliding window produces the requested comparison. Segments
    with insufficient valid data are skipped rather than fabricated.
    """
    import numpy as np

    def clean(data):
        times = np.asarray(data["times"], dtype=float)
        values = np.asarray(data["values"], dtype=float)
        finite = np.isfinite(values)
        return times[finite], values[finite]

    ta, va = clean(a)
    tb, vb = clean(b)
    if len(ta) < 2 or len(tb) < 2:
        return [], []
    start, end = max(ta[0], tb[0]), min(ta[-1], tb[-1])
    if end <= start:
        return [], []
    grid = np.arange(start, end, 0.05)
    ag = np.interp(grid, ta, va)
    bg = np.interp(grid, tb, vb)
    window = max(4, int(round(window_s / 0.05)))
    stride = max(1, int(round(step_s / 0.05)))
    out_t, out_v = [], []
    for first in range(0, max(1, len(grid) - window), stride):
        aw, bw = ag[first:first + window], bg[first:first + window]
        if len(aw) < window:
            break
        valid = np.isfinite(aw) & np.isfinite(bw)
        if valid.sum() < window * 0.5:
            continue
        aw, bw = aw[valid], bw[valid]
        if op.startswith("NRMSE"):
            reference_range = float(np.ptp(bw))
            value = float(np.sqrt(np.mean((aw - bw) ** 2)) / reference_range) if reference_range > 0 else math.nan
            if math.isfinite(value) and value > 0.0:
                value = 20.0 * math.log10(value)  # Express the NRMSE in dB.
            else:
                value = -math.inf
        elif op.startswith("Correlation"):
            value = (float(np.corrcoef(aw, bw)[0, 1])
                     if np.std(aw) > 0 and np.std(bw) > 0 else math.nan)
        elif op.startswith("Ratio"):
            mean_b = float(np.mean(bw))
            value = float(np.mean(aw)) / mean_b if abs(mean_b) > 1e-12 else math.nan
        else:
            value = float(np.mean(aw - bw))
        out_t.append(float(grid[first + window // 2]))
        out_v.append(value)
    return out_t, out_v


class Workspace:
    """Tk owns the layout; device panels own their hardware and data."""

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("HEEPidermis · multi-device acquisition workspace")
        configure_style(root)
        width = min(1900, max(1200, root.winfo_screenwidth() - 60))
        height = min(1040, max(760, root.winfo_screenheight() - 90))
        root.geometry(f"{width}x{height}")
        root.minsize(1150, 700)

        self.viewport = TimeViewport(span=30.0)
        self.clock_origin = time.monotonic()
        self.timeline_offsets = {}
        self.panels: dict[str, object] = {}
        self.saved_device_configurations = {}
        self.device_frames: dict[str, dict] = {}
        self.signal_rows: list[SignalRow] = []
        self.row_plots: dict[int, TimePlot] = {}
        self.comparison_headers: list[tk.Widget] = []
        self.feature_cache: dict[tuple, object] = {}
        self.save_job = None
        self.error_seen: set[str] = set()
        self.closing = False
        self.plot_future = None
        self.plot_generation = 0
        self.refresh_job = None
        self.file_future = None
        self.file_action = None
        self.recording_directories = []

        self._build()
        self._load_state()
        self.refresh_job = self.root.after(REFRESH_MS, self._refresh)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ layout

    def _build(self):
        self.panes = ttk.Panedwindow(self.root, orient="horizontal")
        self.panes.pack(fill="both", expand=True)

        # Left: scrollable configuration column with collapsible menus.
        left = ttk.Frame(self.panes, style="App.TFrame")
        self.panes.add(left, weight=0)
        self.config_pane = ScrollPane(left, width=800)
        self.config_pane.pack(fill="both", expand=True)
        config = self.config_pane.body
        config.columnconfigure(0, weight=1)

        actions = ttk.Frame(config, style="App.TFrame")
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="Save configuration…",
                   command=self.save_configuration).pack(side="left")
        ttk.Button(actions, text="Load configuration…",
                   command=self.load_configuration).pack(side="left", padx=(8, 0))

        devices = Collapsible(config, "DEVICES", opened=True)
        devices.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.devices_section = devices.body
        self.config_pane.canvas.bind("<Configure>", self._pin_connect_clusters, add="+")
        self.devices_section.bind("<Configure>", self._pin_connect_clusters, add="+")
        self.devices_section.columnconfigure(0, weight=1)
        self.devices_section.columnconfigure(1, weight=0)
        for index, device in enumerate(DEVICES):
            key = device["key"]
            enabled = tk.BooleanVar(value=False)
            enabled.trace_add("write", lambda *_a, key=key: self._device_toggled(key))
            check = ttk.Checkbutton(self.devices_section, text=device["name"],
                                    variable=enabled, style="Workspace.TCheckbutton")
            check.grid(row=index * 2, column=0, sticky="w", pady=(4, 0))
            ttk.Label(self.devices_section, text=device["description"],
                      style="Muted.TLabel", wraplength=660).grid(row=index * 2 + 1,
                                                                 column=0, columnspan=2, sticky="w")
            # Connect button + status circle + concise status message, pinned
            # to the visible end of the DEVICES column (place, re-pinned on
            # pane resize) so it is reachable without horizontal scrolling.
            cluster = ttk.Frame(self.devices_section, style="App.TFrame")
            dot = tk.Canvas(cluster, width=11, height=11, highlightthickness=0,
                            background=UI_SURFACE)
            dot.pack(side="left")
            dot_id = dot.create_oval(1, 1, 10, 10, fill=STATUS_COLORS["off"], outline="")
            status_text = tk.StringVar(value="Disconnected")
            ttk.Label(cluster, textvariable=status_text, style="Muted.TLabel").pack(
                side="left", padx=(5, 8))
            button = ttk.Button(cluster, text="Connect", width=9, state="disabled",
                                command=lambda k=key: self._connect_device(k))
            button.pack(side="left")
            cluster.place_forget()
            self.device_frames[key] = {"variable": enabled, "cluster": cluster,
                                       "dot": dot, "dot_id": dot_id,
                                       "status_text": status_text,
                                       "connect_button": button, "check": check,
                                       "level": None, "message": None}

        signals = Collapsible(config, "SIGNALS", opened=True)
        signals.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.signals_section = signals.body
        self.signals_section.columnconfigure(0, weight=1)
        # The table keeps its natural width and scrolls horizontally inside
        # its own viewport; the foldable device menus then only need to be as
        # wide as the visible configuration column (no horizontal scrolling
        # to read them).
        holder = ttk.Frame(self.signals_section, style="App.TFrame")
        holder.grid(row=0, column=0, sticky="ew")
        holder.columnconfigure(0, weight=1)
        self.table_view = tk.Canvas(holder, highlightthickness=0, background=UI_SURFACE)
        self.table_view.grid(row=0, column=0, sticky="ew")
        # The table must be a child of the canvas: as a window item it is then
        # clipped to the viewport and stacks above the canvas surface. A table
        # parented outside the canvas would sit *below* this opaque canvas and
        # become invisible (only a strip sticking out past it would show).
        self.signal_table = ttk.Frame(self.table_view)
        self.table_bar = ttk.Scrollbar(holder, orient="horizontal", command=self.table_view.xview)
        self.table_bar.grid(row=1, column=0, sticky="ew")
        self.table_bar.grid_remove()
        self.table_view.configure(xscrollcommand=self._table_xview)
        self.table_window = self.table_view.create_window((0, 0), window=self.signal_table,
                                                          anchor="nw")
        self.signal_table.bind("<Configure>", self._track_table, add="+")
        self.table_view.bind("<Configure>", self._track_table, add="+")
        # Weighted, but with a guaranteed minimum so no column crops its
        # contents; overflow scrolls horizontally inside the table viewport.
        # Hide/color/remove sit right next to the signal so they are reachable
        # without horizontal scrolling.
        for column, (weight, minimum) in enumerate(zip(
                (2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1),
                (80, 120, 42, 32, 36, 40, 48, 58, 60, 60, 52, 68, 40, 36, 195, 195, 48))):
            self.signal_table.columnconfigure(column, weight=weight, minsize=minimum)
        headers = ("Device", "Signal", "Hide", "", "X", "St", "Y", "Range", "Min", "Max",
                   "Style", "Amplify", "α", "Z", "A", "B", "Conf.")
        for column, title in enumerate(headers):
            label = ttk.Label(self.signal_table, text=title, style="Muted.TLabel")
            label.grid(row=0, column=column, sticky="w", padx=2)
            if column == 16:
                self.params_header = label
            elif column in (14, 15):
                self.comparison_headers.append(label)
        self.params_header.grid_remove()
        add_bar = ttk.Frame(self.signals_section)
        add_bar.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(add_bar, text="+ Add signal row",
                   command=lambda: self.add_signal_row()).pack(side="left")
        ttk.Button(add_bar, text="+ Add comparison (A vs B)",
                   command=lambda: self.add_signal_row(device="comparison")).pack(side="left", padx=(8, 0))
        ttk.Button(add_bar, text="+ Add feature extraction",
                   command=lambda: self.add_signal_row(device="features")).pack(side="left", padx=(8, 0))
        self.signal_hint = ttk.Label(
            self.signals_section,
            text="Stack chooses the plot row (all rows share the time axis). "
                 "Range sets the y-axis: Auto fits the visible data, Fixed pins "
                 "it to Min–Max (NRMSE defaults to 0–100, correlation to −1–1; "
                 "device signals default to Auto). "
                 "'normalize row/axis' overrides the amplification of every other "
                 "signal in the same stack and y-axis. Feature rows (conf.) extract "
                 "peaks and per-peak measures from a source signal; put them in "
                 "the same stack as the peaks to see the white annotations.",
            style="Muted.TLabel", wraplength=680)
        self.signal_hint.grid(row=2, column=0, sticky="w", pady=(6, 0))

        decomposition = Collapsible(config, "DECOMPOSITION · ALL DEVICES", opened=False)
        decomposition.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.decomposition_variables = {
            "algorithm": tk.StringVar(value=ALGORITHMS[1]),
            "smoothing": tk.StringVar(value="1"),
            "prominence": tk.StringVar(value="0.01"),
        }
        for column, (key, label) in enumerate((("algorithm", "Tonic/phasic algorithm"),
                                               ("smoothing", "Smoothing λ"),
                                               ("prominence", "Peak prominence (µS)"))):
            ttk.Label(decomposition.body, text=label).grid(row=0, column=column, sticky="w", padx=4)
            variable = self.decomposition_variables[key]
            widget = (ttk.Combobox(decomposition.body, textvariable=variable, values=ALGORITHMS,
                                   state="readonly", width=24) if key == "algorithm" else
                      ttk.Entry(decomposition.body, textvariable=variable, width=12))
            widget.grid(row=1, column=column, sticky="ew", padx=4, pady=4)
            variable.trace_add("write", self._decomposition_changed)
        ttk.Label(decomposition.body, text="Applies to tonic/phasic signals from every device. "
                  "Feature rows retain their own conf. settings.", style="Muted.TLabel").grid(
                      row=2, column=0, columnspan=3, sticky="w", padx=4)
        self.file_status = tk.StringVar()
        ttk.Label(actions, textvariable=self.file_status, style="Muted.TLabel").pack(side="left", padx=8)

        self.device_config_row = 4
        self.device_sections: dict[str, Collapsible] = {}
        self.device_bodies: dict[str, ttk.Frame] = {}

        # Right: stacked plot rows sharing one time viewport.
        right = ttk.Frame(self.panes, style="Workspace.TFrame")
        self.panes.add(right, weight=1)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.plot_status = tk.StringVar(value="Waiting for samples")
        ttk.Label(right, textvariable=self.plot_status, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=6, pady=3)
        self.plot_area = ttk.Frame(right, style="Workspace.TFrame")
        self.plot_area.grid(row=0, column=0, sticky="nsew")
        self.plot_area.columnconfigure(0, weight=1)
        self.time_scroll = ttk.Scrollbar(right, orient="horizontal", command=self._scroll)
        self.time_scroll.grid(row=1, column=0, sticky="ew")

        # Bottom: device reports left, shared navigation right.
        bottom = ttk.Frame(self.root, style="Workspace.TFrame", height=190)
        bottom.pack(fill="x", side="bottom")
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)
        self.reports = ttk.Notebook(bottom)
        self.reports.grid(row=0, column=0, sticky="nsew", padx=(6, 6), pady=(6, 6))
        placeholder = ttk.Frame(self.reports)
        ttk.Label(placeholder, text="Enable a device to see its output, messages and status reports.",
                  style="Muted.TLabel").pack(anchor="center", pady=20)
        self.reports.add(placeholder, text="Reports")
        self._reports_placeholder = placeholder
        self.report_tabs: dict[str, ttk.Frame] = {}
        nav = ttk.Frame(bottom, style="Workspace.TFrame", padding=(4, 6))
        nav.grid(row=0, column=1, sticky="ns")
        self.zoom_text = tk.StringVar(value="TIME ZOOM · 30 s")
        ttk.Label(nav, textvariable=self.zoom_text, font=("Helvetica", 9, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        # Common transport: drives every enabled device from one place.
        self.transport_buttons = {}
        for column, (action, text) in enumerate((("start", "▶ Start"), ("pause", "▮▮ Pause"),
                                                 ("stop", "■ Stop"))):
            button = ttk.Button(nav, text=text, width=9,
                                command=lambda a=action: self._transport(a))
            button.grid(row=1, column=column, padx=(0 if column == 0 else 3, 0))
            self.transport_buttons[action] = button
        self.transport_text = tk.StringVar(value="No device enabled")
        ttk.Label(nav, textvariable=self.transport_text, style="Muted.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Button(nav, text="−", width=4, command=lambda: self._zoom(2.0)).grid(row=3, column=0, padx=(0, 3), pady=(6, 0))
        ttk.Button(nav, text="+", width=4, command=lambda: self._zoom(0.5)).grid(row=3, column=1, padx=3, pady=(6, 0))
        self.live_button = ttk.Button(nav, text="Live", command=self._go_live, style="Accent.TButton")
        self.live_button.grid(row=3, column=2, padx=(3, 0), pady=(6, 0))
        ttk.Button(nav, text="Save CSV…", command=self.save_all).grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(nav, text="Load CSV…", command=self.load_csv).grid(row=5, column=0, columnspan=3, sticky="ew")
        ttk.Label(nav, text="Click/drag a plot to inspect;\nwheel moves through time.",
                  style="Muted.TLabel", justify="left").grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))

    # ----------------------------------------------------------------- devices

    def _device_toggled(self, key: str):
        device = next(item for item in DEVICES if item["key"] == key)
        enabled = self.device_frames[key]["variable"].get()
        if enabled and key not in self.panels:
            self._enable_device(device)
        elif not enabled:
            self._disable_device(device)
        self._state_changed()

    def _pin_connect_cluster(self, key):
        """Place one device's Connect cluster at the visible end of the
        DEVICES column: the pane's content can be wider than the viewport
        (the signal table scrolls horizontally), but Connect must not hide
        behind that scroll when there is room on screen."""
        frame = self.device_frames.get(key)
        if frame is None or not frame["cluster"].winfo_exists():
            return
        visible = self.config_pane.canvas.winfo_width()
        section = self.devices_section.winfo_width()
        edge = max(frame["cluster"].winfo_reqwidth(), min(visible, section) - 8)
        frame["cluster"].place(anchor="ne", x=edge,
                               y=frame["check"].winfo_y() + 1)

    def _track_table(self, _event=None):
        """Size the table viewport to the table and scroll only when needed."""
        width = self.signal_table.winfo_reqwidth()
        height = self.signal_table.winfo_reqheight()
        self.table_view.configure(height=height, scrollregion=(0, 0, width, height))
        self.table_view.itemconfigure(self.table_window,
                                      width=max(self.table_view.winfo_width(), width))

    def _table_xview(self, first, last):
        self.table_bar.set(first, last)
        # Only when the table is wider than its viewport.
        (self.table_bar.grid if float(last) - float(first) < 0.999
         else self.table_bar.grid_remove)()

    def _pin_connect_clusters(self, _event=None):
        for key in self.device_frames:
            if self.device_frames[key]["cluster"].winfo_ismapped():
                self._pin_connect_cluster(key)

    def _device_section(self, device) -> Collapsible:
        key = device["key"]
        if key in self.device_sections:
            return self.device_sections[key]
        section = Collapsible(self.config_pane.body, device["name"].upper(), opened=False)
        section.grid(row=self.device_config_row, column=0, sticky="ew", pady=(0, 8))
        self.device_config_row += 1
        # Opening a device's configuration also focuses its report tab.
        section.button.configure(command=lambda s=section, k=key: (s.toggle(), self._show_report(k)))
        self.device_sections[key] = section
        self.device_bodies[key] = section.body
        return section

    def _enable_device(self, device):
        key = device["key"]
        section = self._device_section(device)
        body = self.device_bodies[key]
        if device["panel"] is None:
            ttk.Label(body, text="Not implemented yet. The SDK folder for this device is "
                                 f"reserved at sw/gui/sdk/{key}; its configuration menu will "
                                 "appear here once the connection layer exists.",
                      style="Muted.TLabel", wraplength=520).pack(fill="x", pady=8)
            return
        tab = ttk.Frame(self.reports)
        tab.columnconfigure(0, weight=3, uniform="reports")
        tab.columnconfigure(1, weight=2, uniform="reports")
        tab.rowconfigure(0, weight=1)
        self.reports.add(tab, text=device["name"])
        self.report_tabs[key] = tab
        if self._reports_placeholder is not None and self._reports_placeholder.winfo_exists():
            self.reports.forget(self._reports_placeholder)
            self._reports_placeholder = None
        try:
            panel = device["panel"](self.root, host=body, reports=tab, on_closed=lambda k=key: self._device_closed(k))
        except Exception as error:
            traceback.print_exc()
            for child in body.winfo_children():
                child.destroy()
            ttk.Label(body, text=f"Could not start the {device['name']} panel: {error}",
                      style="Muted.TLabel", wraplength=520).pack(fill="x", pady=8)
            self.device_frames[key]["variable"].set(False)
            if key in self.report_tabs:
                self.reports.forget(self.report_tabs.pop(key))
            return
        self.panels[key] = panel
        if hasattr(panel, "set_workspace_clock"):
            panel.set_workspace_clock(self.clock_origin)
        if key in self.saved_device_configurations:
            panel.apply_configuration(self.saved_device_configurations[key])
        self._sync_decomposition()
        frame = self.device_frames[key]
        self.root.after_idle(lambda k=key: self._pin_connect_cluster(k))
        frame["connect_button"].configure(state="normal")
        self.reports.select(tab)
        self._rebuild_signal_choices()

    def _disable_device(self, device):
        key = device["key"]
        panel = self.panels.get(key)
        snapshot = getattr(panel, "configuration_snapshot", None)
        if snapshot is not None:
            self.saved_device_configurations[key] = snapshot()
        self.panels.pop(key, None)
        if panel is not None:
            try:
                panel.close()
            except Exception as error:
                traceback.print_exc()
                self._log_error(f"Closing {device['name']} failed: {error}")
        if key in self.report_tabs:
            self.reports.forget(self.report_tabs.pop(key))
        body = self.device_bodies.get(key)
        if body is not None and device["panel"] is None:
            for child in body.winfo_children():
                child.destroy()
        frame = self.device_frames.get(key)
        if frame is not None:
            frame["cluster"].place_forget()
            frame["connect_button"].configure(state="disabled")
            frame["level"] = frame["message"] = None
            frame["dot"].itemconfigure(frame["dot_id"], fill=STATUS_COLORS["off"])
            frame["status_text"].set("Disconnected")
        self._rebuild_signal_choices()

    def _connect_device(self, key: str):
        """The DEVICES-line Connect/Disconnect button for one device."""
        if self.file_action == "load":
            return
        panel = self.panels.get(key)
        if panel is None:
            return
        self._show_report(key)
        try:
            panel.connect_toggle()
        except Exception as error:
            self._log_error(f"Connect toggle for {key} failed: {error}")
        self._update_device_status()

    def _show_report(self, key: str):
        """Switch the bottom notebook to the device's report tab."""
        tab = self.report_tabs.get(key)
        if tab is not None and tab.winfo_exists():
            self.reports.select(tab)

    def _update_device_status(self):
        """Refresh each device's status circle and concise message."""
        for key, panel in self.panels.items():
            frame = self.device_frames.get(key)
            getter = getattr(panel, "connection", None)
            if frame is None or getter is None:
                continue
            try:
                level, message = getter()
            except Exception:
                continue
            if level != frame.get("level"):
                frame["level"] = level
                frame["dot"].itemconfigure(frame["dot_id"], fill=STATUS_COLORS.get(level, STATUS_COLORS["off"]))
            if message != frame.get("message"):
                frame["message"] = message
                frame["status_text"].set(message)
            labels = getattr(panel, "connect_labels", ("Connect", "Disconnect"))
            label = labels[0] if level == "off" else labels[1]
            if str(frame["connect_button"].cget("text")) != label:
                frame["connect_button"].configure(text=label)

    def _device_closed(self, key: str):
        """A panel closed itself (window shutdown or fatal acquisition error)."""
        self.panels.pop(key, None)
        variable = self.device_frames.get(key, {}).get("variable")
        if variable is not None:
            variable.set(False)

    def close(self):
        self.closing = True
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        if self.plot_future is not None:
            self.plot_future.cancel()
        if self.file_future is not None and self.file_action == "load":
            def cleanup_loaded(future):
                if not future.cancelled() and future.exception() is None:
                    future.result()[0].cleanup()
            self.file_future.add_done_callback(cleanup_loaded)
            self.file_future.cancel()
        for key in list(self.panels):
            device = next(item for item in DEVICES if item["key"] == key)
            self._disable_device(device)
        for directory in self.recording_directories:
            directory.cleanup()

    def _decomposition_settings(self):
        values = {key: variable.get() for key, variable in self.decomposition_variables.items()}
        values["smoothing"] = float(values["smoothing"])
        values["prominence"] = float(values["prominence"])
        if (values["algorithm"] not in ALGORITHMS or
                not math.isfinite(values["smoothing"]) or not 0 <= values["smoothing"] <= 1000 or
                not math.isfinite(values["prominence"]) or values["prominence"] <= 0):
            raise ValueError("Choose a valid decomposition algorithm, smoothing (0–1000), and positive prominence.")
        return values

    def _sync_decomposition(self):
        values = self._decomposition_settings()
        for panel in self.panels.values():
            setter = getattr(panel, "set_decomposition", None)
            if setter is not None:
                setter(**values)

    def _decomposition_changed(self, *_args):
        try:
            self._sync_decomposition()
        except ValueError:
            return  # An entry may be incomplete while being edited.
        self._state_changed()

    # ----------------------------------------------------------- signal table

    def add_signal_row(self, device: str = "heepidermis", signal: str = "", **overrides):
        row = SignalRow(device=device, signal=signal, **overrides)
        row.signal = signal or self._default_signal(row)
        if not row.color:
            row.color = _next_color(existing.color for existing in self.signal_rows)
        self._apply_range_default(row)
        self.signal_rows.append(row)
        self._add_table_row(row)
        self._state_changed()

    @staticmethod
    def _apply_range_default(row: SignalRow):
        """Give a new row kind its default range unless the user set one."""
        if row.range_set:
            return
        row.y_scale, row.y_min, row.y_max = range_default(row)

    def _default_signal(self, row: SignalRow) -> str:
        return self._primary_signal(row.device)

    def _primary_signal(self, device: str) -> str:
        """The device's main magnitude (e.g. conductance), not just the first
        catalogue entry; used for new rows and after loading a recording."""
        panel = self.panels.get(device)
        if panel is not None:
            try:
                catalogue = panel.signals()
                for signal, entry in catalogue.items():
                    if isinstance(entry, dict) and entry.get("primary"):
                        return signal
            except Exception:
                pass
        choices = self._signal_choices(device)
        return choices[0] if choices else ""

    def _device_keys(self):
        keys = [device["key"] for device in DEVICES if device["key"] in self.panels]
        return keys or ["heepidermis", "biopac"]

    def _signal_choices(self, device: str):
        if device == "comparison":
            return list(COMPARISON_CHOICES)
        if device == "features":
            return list(FEATURE_CHOICES)
        panel = self.panels.get(device)
        if panel is None:
            return []
        try:
            return list(panel.signals().keys())
        except Exception:
            return []

    def _signal_unit(self, key: str) -> str:
        """Unit of a 'device:signal' series, for the stack axis labels."""
        device, _, signal = key.partition(":")
        panel = self.panels.get(device)
        if panel is not None:
            try:
                return str(panel.signals().get(signal, {}).get("unit", ""))
            except Exception:
                pass
        return ""

    def _signal_label(self, key: str) -> str:
        """Human magnitude name of a 'device:signal' series, for axes/legend."""
        device, _, signal = key.partition(":")
        panel = self.panels.get(device)
        if panel is not None:
            try:
                return str(panel.signals().get(signal, {}).get("label", "")) or signal
            except Exception:
                pass
        return signal

    def _all_series_keys(self):
        keys = []
        for device in DEVICES:
            for signal in self._signal_choices(device["key"]):
                panel = self.panels.get(device["key"])
                if panel is not None and panel.signals().get(signal, {}).get("kind") == "events":
                    continue
                keys.append(f"{device['key']}:{signal}")
        return keys

    def _validate_source(self, source, series_keys):
        """Validate a 'device:signal' source, tolerating disabled devices.

        A saved source whose device panel is not enabled yet cannot be
        validated, so it is kept as-is until that panel provides its signals
        (same keep-until-validatable rule as the device/signal rows).
        """
        if source in series_keys:
            return source
        device, _, _signal = source.partition(":")
        if device in {entry["key"] for entry in DEVICES} and device not in self.panels:
            return source
        return series_keys[0] if series_keys else ""

    def _rebuild_signal_choices(self):
        for widgets in self.table_rows:
            device_box, signal_box = widgets["device"], widgets["signal"]
            current_device = device_box.get() or widgets["row"].device
            # Every known device stays selectable; rows for a device whose
            # panel is not enabled yet keep their saved selection until that
            # panel provides its signals (they must never be reassigned).
            devices = [device["key"] for device in DEVICES] + ["comparison", "features"]
            device_box.configure(values=devices)
            if current_device not in devices:
                widgets["row"].device = devices[0] if devices else ""
                device_box.set(widgets["row"].device)
                widgets["row"].signal = self._default_signal(widgets["row"])
            self._update_signal_values(widgets)

    def _update_signal_values(self, widgets):
        row = widgets["row"]
        if row.device == "features":
            choices = FEATURE_CHOICES
            widgets["signal"].configure(values=choices)
            if row.signal not in choices:
                row.signal = choices[0]
            widgets["signal"].set(row.signal)
            series_keys = self._all_series_keys()
            box = widgets["source_a"]
            box.configure(values=series_keys, state="readonly" if series_keys else "disabled")
            row.source_a = self._validate_source(row.source_a, series_keys)
            box.set(row.source_a)
            widgets["source_b"].set("—")
            widgets["source_b"].configure(state="disabled")
            widgets["source_a"].grid()
            widgets["source_b"].grid_remove()
            widgets["params"].grid()
        elif row.device == "comparison":
            choices = COMPARISON_CHOICES
            widgets["signal"].configure(values=choices)
            if row.signal not in choices:
                row.signal = choices[0]
            widgets["signal"].set(row.signal)
            series_keys = self._all_series_keys()
            for key in ("source_a", "source_b"):
                box = widgets[key]
                box.configure(values=series_keys, state="readonly" if series_keys else "disabled")
                setattr(row, key, self._validate_source(getattr(row, key), series_keys))
                box.set(getattr(row, key))
            widgets["source_a"].grid()
            widgets["source_b"].grid()
            widgets["params"].grid_remove()
        else:
            choices = self._signal_choices(row.device)
            widgets["signal"].configure(values=choices)
            if choices:
                if row.signal not in choices:
                    row.signal = choices[0]
                widgets["signal"].set(row.signal)
            else:
                # Device not enabled (yet): keep the saved selection visible;
                # it is validated when the device panel provides its signals.
                widgets["signal"].set(row.signal)
            for key in ("source_a", "source_b"):
                widgets[key].set("—")
                widgets[key].configure(state="disabled")
            widgets["source_a"].grid_remove()
            widgets["source_b"].grid_remove()
            widgets["params"].grid_remove()

    def _add_table_row(self, row: SignalRow):
        table_row = len(self.signal_rows)
        widgets = {"row": row}
        device_box = ttk.Combobox(self.signal_table, width=9, state="readonly",
                                  values=[device["key"] for device in DEVICES]
                                          + ["comparison", "features"])
        device_box.set(row.device)
        device_box.grid(row=table_row, column=0, sticky="ew", padx=2, pady=2)
        device_box.bind("<<ComboboxSelected>>",
                        lambda _e, w=widgets: self._device_changed(w))
        signal_box = ttk.Combobox(self.signal_table, width=14, state="readonly")
        signal_box.grid(row=table_row, column=1, sticky="ew", padx=2, pady=2)
        signal_box.bind("<<ComboboxSelected>>",
                        lambda _e, w=widgets, box=signal_box: self._value_changed(w, "signal", box.get()))
        hide = ttk.Button(self.signal_table, text="Show" if row.hidden else "Hide", width=5,
                          command=lambda w=widgets: self._toggle_row_hidden(w))
        hide.grid(row=table_row, column=2, sticky="ew", padx=2, pady=2)
        color = tk.Button(self.signal_table, background=row.color, width=3, relief="flat",
                          activebackground=row.color, cursor="hand2",
                          state="disabled" if row.hidden else "normal",
                          command=lambda w=widgets: self._choose_color(w))
        color.grid(row=table_row, column=3, sticky="ew", padx=2, pady=2)
        remove = ttk.Button(self.signal_table, text="X", width=3,
                            command=lambda w=widgets: self._remove_row(w))
        remove.grid(row=table_row, column=4, sticky="ew", padx=2, pady=2)
        stack = ttk.Spinbox(self.signal_table, from_=1, to=PLOT_ROWS_MAX, width=3,
                            command=lambda w=widgets, b=None: None)
        stack.set(row.stack)
        stack.grid(row=table_row, column=5, sticky="ew", padx=2, pady=2)
        stack.bind("<Return>", lambda _e, w=widgets, b=stack: self._value_changed(w, "stack", b.get()))
        stack.bind("<FocusOut>", lambda _e, w=widgets, b=stack: self._value_changed(w, "stack", b.get()))
        axis = ttk.Combobox(self.signal_table, values=("Left", "Right"), width=4, state="readonly")
        axis.set(row.axis)
        axis.grid(row=table_row, column=6, sticky="ew", padx=2, pady=2)
        axis.bind("<<ComboboxSelected>>", lambda _e, w=widgets, b=axis: self._value_changed(w, "axis", b.get()))
        y_scale = ttk.Combobox(self.signal_table, values=("Auto", "Fixed"), width=5, state="readonly")
        y_scale.set(row.y_scale)
        y_scale.grid(row=table_row, column=7, sticky="ew", padx=2, pady=2)
        y_scale.bind("<<ComboboxSelected>>",
                     lambda _e, w=widgets, b=y_scale: self._range_mode_changed(w, b.get()))
        y_min = ttk.Spinbox(self.signal_table, from_=-1e12, to=1e12, increment=0.1, width=7)
        y_min.set(f"{row.y_min:g}")
        y_min.grid(row=table_row, column=8, sticky="ew", padx=2, pady=2)
        y_min.bind("<Return>", lambda _e, w=widgets, b=y_min: self._value_changed(w, "y_min", b.get()))
        y_min.bind("<FocusOut>", lambda _e, w=widgets, b=y_min: self._value_changed(w, "y_min", b.get()))
        y_max = ttk.Spinbox(self.signal_table, from_=-1e12, to=1e12, increment=0.1, width=7)
        y_max.set(f"{row.y_max:g}")
        y_max.grid(row=table_row, column=9, sticky="ew", padx=2, pady=2)
        y_max.bind("<Return>", lambda _e, w=widgets, b=y_max: self._value_changed(w, "y_max", b.get()))
        y_max.bind("<FocusOut>", lambda _e, w=widgets, b=y_max: self._value_changed(w, "y_max", b.get()))
        transform = ttk.Combobox(self.signal_table, values=TRANSFORM_CHOICES, width=4, state="readonly")
        transform.set(row.transform)
        transform.grid(row=table_row, column=10, sticky="ew", padx=2, pady=2)
        transform.bind("<<ComboboxSelected>>", lambda _e, w=widgets, b=transform: self._value_changed(w, "transform", b.get()))
        amplify = ttk.Combobox(self.signal_table, values=AMPLIFY_CHOICES, width=6, state="readonly")
        amplify.set(row.amplify)
        amplify.grid(row=table_row, column=11, sticky="ew", padx=2, pady=2)
        amplify.bind("<<ComboboxSelected>>", lambda _e, w=widgets, b=amplify: self._value_changed(w, "amplify", b.get()))
        alpha = ttk.Spinbox(self.signal_table, from_=0.0, to=1.0, increment=0.05, width=4)
        alpha.set(row.alpha)
        alpha.grid(row=table_row, column=12, sticky="ew", padx=2, pady=2)
        alpha.bind("<Return>", lambda _e, w=widgets, b=alpha: self._value_changed(w, "alpha", b.get()))
        alpha.bind("<FocusOut>", lambda _e, w=widgets, b=alpha: self._value_changed(w, "alpha", b.get()))
        zorder = ttk.Spinbox(self.signal_table, from_=-10, to=10, width=3)
        zorder.set(row.zorder)
        zorder.grid(row=table_row, column=13, sticky="ew", padx=2, pady=2)
        zorder.bind("<Return>", lambda _e, w=widgets, b=zorder: self._value_changed(w, "zorder", b.get()))
        zorder.bind("<FocusOut>", lambda _e, w=widgets, b=zorder: self._value_changed(w, "zorder", b.get()))
        source_a = ttk.Combobox(self.signal_table, width=26, state="disabled")
        source_a.grid(row=table_row, column=14, sticky="ew", padx=2, pady=2)
        source_a.bind("<<ComboboxSelected>>", lambda _e, w=widgets, b=source_a: self._value_changed(w, "source_a", b.get()))
        source_b = ttk.Combobox(self.signal_table, width=26, state="disabled")
        source_b.grid(row=table_row, column=15, sticky="ew", padx=2, pady=2)
        source_b.bind("<<ComboboxSelected>>", lambda _e, w=widgets, b=source_b: self._value_changed(w, "source_b", b.get()))
        params = ttk.Button(self.signal_table, text="conf.", width=6,
                            command=lambda w=widgets: self._configure_feature(w))
        params.grid(row=table_row, column=16, sticky="ew", padx=2, pady=2)
        params.grid_remove()
        widgets.update(device=device_box, signal=signal_box, hide=hide, stack=stack, axis=axis,
                       y_scale=y_scale, y_min=y_min, y_max=y_max, transform=transform,
                       amplify=amplify, color=color, alpha=alpha, zorder=zorder,
                       source_a=source_a, source_b=source_b, params=params)
        self.table_rows.append(widgets)
        self._update_signal_values(widgets)
        self._update_range_widgets(widgets)
        self._update_comparison_columns()
        Tooltip(hide, "Hide this signal from the plots without removing it; "
                      "its settings and saved data are kept.")
        Tooltip(amplify, "Scales the signal, or normalizes it to ±1. "
                         "'normalize row/axis' applies one common scale to every "
                         "signal in the same stack and y-axis, overriding their "
                         "individual amplification.")
        Tooltip(y_scale, "Y-axis scaling for this row's axis side: 'Auto' fits "
                         "the visible data; 'Fixed' pins the axis to Min–Max. "
                         "Comparison rows default to fixed (NRMSE 0–100, "
                         "correlation −1–1); device signals default to Auto.")
        Tooltip(y_min, "Lower axis limit used when Range is Fixed.")
        Tooltip(y_max, "Upper axis limit used when Range is Fixed.")
        Tooltip(stack, "Row of stacked plots (all share the same time axis).")
        Tooltip(params, "Algorithm and peak parameters for this feature row.")

    def _update_comparison_columns(self):
        """Show the A/B pickers and ⚙ button only for the rows that use them."""
        has_comparison = any(row.device == "comparison" for row in self.signal_rows)
        has_features = any(row.device == "features" for row in self.signal_rows)
        header_a, header_b = self.comparison_headers
        (header_a.grid if has_comparison or has_features else header_a.grid_remove)()
        (header_b.grid if has_comparison else header_b.grid_remove)()
        (self.params_header.grid if has_features else self.params_header.grid_remove)()

    def _configure_feature(self, widgets):
        """Open the algorithm/parameter dialog for one feature row."""
        row = widgets["row"]
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Feature parameters · {row.signal}")
        dialog.configure(background=UI_SURFACE)
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text=f"Source: {row.source_a or '—'}",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=2,
                                             sticky="w", pady=(0, 8))
        is_phasic = "phasic" in row.source_a.lower()
        ttk.Label(body, text="Decomposition algorithm" if not is_phasic
                  else "Decomposition algorithm (source is already phasic — peaks are found directly)"
                  ).grid(row=1, column=0, sticky="w", pady=(0, 4))
        algorithm = ttk.Combobox(body, values=ALGORITHMS, state="readonly", width=34)
        algorithm.set(row.algorithm or ALGORITHMS[1])
        algorithm.grid(row=1, column=1, sticky="ew", pady=(0, 4))
        Tooltip(algorithm, "Tonic/phasic solver applied to a conductance source, "
                           "as in the HEEPidermis PROCESSING section. Ignored when "
                           "the source is already a phasic signal.")
        ttk.Label(body, text="Smoothing λ (0–1000)").grid(row=2, column=0, sticky="w", pady=(0, 4))
        smoothing = ttk.Spinbox(body, from_=0.0, to=1000.0, increment=0.5, width=8)
        smoothing.set(row.smoothing)
        smoothing.grid(row=2, column=1, sticky="w", pady=(0, 4))
        Tooltip(smoothing, "Larger λ gives a smoother tonic baseline and a smaller "
                           "phasic residual, as in the HEEPidermis GUI.")
        ttk.Label(body, text="Peak prominence (µS)" if not is_phasic
                  else "Peak prominence (source units)").grid(row=3, column=0, sticky="w", pady=(0, 4))
        prominence = ttk.Spinbox(body, from_=0.001, to=10.0, increment=0.01, width=8)
        prominence.set(row.prominence)
        prominence.grid(row=3, column=1, sticky="w", pady=(0, 4))
        Tooltip(prominence, "Minimum phasic prominence required to mark a response "
                            "peak (width ≥ 0.5 s, separation ≥ 1 s, as in the GUI).")

        def apply(_event=None):
            try:
                row.smoothing = min(1000.0, max(0.0, float(smoothing.get())))
                row.prominence = min(10.0, max(0.001, float(prominence.get())))
            except (TypeError, ValueError):
                return
            row.algorithm = algorithm.get()
            self.feature_cache.clear()
            self._state_changed()

        for widget in (algorithm, smoothing, prominence):
            widget.bind("<<ComboboxSelected>>", apply)
            widget.bind("<Return>", apply)
            widget.bind("<FocusOut>", apply)

        def accept(_event=None):
            apply()
            dialog.destroy()

        ttk.Button(body, text="OK", command=accept).grid(row=4, column=1, sticky="e", pady=(10, 0))
        # Enter accepts the parameters and closes; Escape closes without an
        # extra apply (edits are already applied on FocusOut).
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()
        dialog.wait_window(dialog)

    def _device_changed(self, widgets):
        row = widgets["row"]
        row.device = widgets["device"].get()
        row.signal = self._default_signal(row)
        self._apply_range_default(row)
        self._update_range_widgets(widgets)
        self._update_signal_values(widgets)
        self._update_comparison_columns()
        self._state_changed()

    def _value_changed(self, widgets, name, value):
        row = widgets["row"]
        try:
            if name in ("stack", "zorder"):
                value = int(float(value))
                if name == "stack":
                    value = min(PLOT_ROWS_MAX, max(1, value))
                setattr(row, name, value)
            elif name == "alpha":
                row.alpha = min(1.0, max(0.0, float(value)))
            elif name in ("y_min", "y_max"):
                value = float(value)
                if not math.isfinite(value):
                    return
                setattr(row, name, value)
                row.range_set = True
            else:
                setattr(row, name, value)
        except (TypeError, ValueError):
            return
        if name == "color":
            widgets["color"].configure(background=row.color, activebackground=row.color)
        if name == "signal":
            # NRMSE/correlation defaults follow the chosen metric.
            self._apply_range_default(row)
            self._update_range_widgets(widgets)
        self._state_changed()

    def _range_mode_changed(self, widgets, value):
        row = widgets["row"]
        row.y_scale = value
        row.range_set = True
        self._update_range_widgets(widgets)
        self._state_changed()

    @staticmethod
    def _update_range_widgets(widgets):
        fixed = widgets["row"].y_scale == "Fixed"
        for name in ("y_min", "y_max"):
            widget = widgets[name]
            widget.state(["!disabled"] if fixed else ["disabled"])
        if fixed:
            widget = widgets["y_min"]
            widget.set(f"{widgets['row'].y_min:g}")
            widgets["y_max"].set(f"{widgets['row'].y_max:g}")

    @staticmethod
    def _row_range(row: SignalRow):
        """(lo, hi) for a fixed row, else None; invalid values fall back to auto."""
        if row.y_scale != "Fixed":
            return None
        try:
            lo, hi = float(row.y_min), float(row.y_max)
        except (TypeError, ValueError):
            return None
        if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
            return lo, hi
        return None

    def _choose_color(self, widgets):
        row = widgets["row"]
        chosen = colorchooser.askcolor(color=row.color, parent=self.root)
        if chosen and chosen[1]:
            row.color = chosen[1]
            widgets["color"].configure(background=row.color, activebackground=row.color)
            self._state_changed()

    def _toggle_row_hidden(self, widgets):
        """Hide a row from the plots without removing or unconfiguring it."""
        row = widgets["row"]
        row.hidden = not row.hidden
        widgets["hide"].configure(text="Show" if row.hidden else "Hide")
        widgets["color"].configure(state="disabled" if row.hidden else "normal")
        self._state_changed()

    def _remove_row(self, widgets):
        row = widgets["row"]
        self.signal_rows.remove(row)
        self.table_rows.remove(widgets)
        for widget in widgets.values():
            if isinstance(widget, tk.Misc) and widget.winfo_exists():
                widget.destroy()
        self._regrid_table()
        self._update_comparison_columns()
        self._state_changed()

    def _regrid_table(self):
        for index, widgets in enumerate(self.table_rows, start=1):
            for widget in widgets.values():
                if isinstance(widget, tk.Misc) and widget is not widgets["row"] and widget.winfo_exists():
                    info = widget.grid_info()
                    if info:
                        widget.grid(row=index)

    # --------------------------------------------------------------- plotting

    def _collect_series(self):
        series = {}
        latest = 0.0
        # Displayed window plus one window to each side, at most: panels only
        # compute (and the workspace only keeps) the data that can be plotted.
        window = self.viewport.span
        low = self.viewport.start - window
        high = self.viewport.start + 2.0 * window
        for device in DEVICES:
            panel = self.panels.get(device["key"])
            if panel is None:
                continue
            try:
                try:
                    device_series = panel.get_series(low, high)
                except TypeError:
                    device_series = panel.get_series()
            except Exception as error:
                raise RuntimeError(f"{device['name']} series failed: {error}") from error
            for name, data in device_series.items():
                if data.get("times"):
                    latest = max(latest, data["times"][-1])
                series[f"{device['key']}:{name}"] = self._trim_series(data, low, high)
        # The newest sample ever seen stays the navigation reference even when
        # the retained window is scrolled away from it.
        return series, max(latest, self.viewport.latest)

    @staticmethod
    def _trim_series(data, low, high):
        """Keep only the samples inside [low, high]; shares lists when possible."""
        times = data.get("times")
        if not times:
            return data
        first = bisect.bisect_left(times, low)
        last = bisect.bisect_right(times, high)
        if first <= 0 and last >= len(times):
            return data
        trimmed = dict(data)
        trimmed["times"] = times[first:last]
        if data.get("values") is not None:
            trimmed["values"] = data["values"][first:last]
        if data.get("segments") is not None:
            trimmed["segments"] = data["segments"][first:last]
        if data.get("labels") is not None:
            trimmed["labels"] = data["labels"][first:last]
        return trimmed

    def _cached_peaks(self, row: SignalRow, data):
        """Peak detection with a cache; recomputed only on new data or params."""
        times = data["times"]
        if len(times) < 3:
            return None
        key = (row.source_a, row.algorithm or ALGORITHMS[1],
               round(float(row.smoothing), 6), round(float(row.prominence), 6),
               len(times), round(float(times[-1]), 6))
        cached = self.feature_cache.get(key)
        if cached is None:
            try:
                cached = features.detect_peaks(
                    times, data["values"], data.get("segments"),
                    is_phasic="phasic" in row.source_a.lower(),
                    algorithm=row.algorithm or ALGORITHMS[1],
                    smoothing=row.smoothing, prominence=row.prominence)
            except Exception as error:
                raise RuntimeError(f"Feature {row.signal} failed: {error}") from error
            self.feature_cache[key] = cached
            while len(self.feature_cache) > 12:
                self.feature_cache.pop(next(iter(self.feature_cache)))
        return cached

    def _build_feature_trace(self, row: SignalRow, series):
        """Feature rows yield markers at the peaks plus per-peak annotations."""
        data = series.get(row.source_a)
        if not data or not row.signal:
            return None
        peaks = self._cached_peaks(row, data)
        if peaks is None or not len(peaks[0]):
            return None
        peak_t, peak_v = peaks
        unit = self._signal_unit(row.source_a)
        right = row.axis == "Right"
        name = f"{row.signal} · {row.source_a}"
        trace = {"times": list(peak_t), "values": list(peak_v), "segments": None,
                 "name": name, "right": right, "color": row.color, "alpha": row.alpha,
                 "zorder": row.zorder, "markers": True, "steps": False, "line": False,
                 "row_key": (row.stack, right), "amplify": row.amplify,
                 "unit": unit if "time" not in row.signal.lower() else "s",
                 "label": row.signal, "peaks": list(peak_t), "annotations": [],
                 "y_range": self._row_range(row)}
        if row.signal != "Phasic peak detection":
            measures = features.peak_measures(data["times"], data["values"], peak_t, peak_v)
            suffix = f" {unit}" if unit and row.signal == "Peak-to-peak amplitude" else ""
            for measure in measures:
                if row.signal == "Peak-to-peak amplitude":
                    label = f"{measure['amplitude']:.3g}{suffix}"
                elif row.signal == "Peak rising time":
                    label = f"↑{measure['rise_s']:.2g}s"
                else:
                    label = f"↓{measure['fall_s']:.2g}s"
                trace["annotations"].append((measure["time"], measure["value"], label, right))
        return trace

    def _build_trace(self, row: SignalRow, series):
        if row.device == "features":
            return self._build_feature_trace(row, series)
        if row.device == "comparison":
            if not row.signal or row.source_a not in series or row.source_b not in series:
                return None
            times, values = derived_series(row.signal, series[row.source_a], series[row.source_b])
            if not times:
                return None
            segments = None
            name = f"{row.signal} · {row.source_a} / {row.source_b}"
            label = row.signal
            unit = "" if row.signal.startswith(("NRMSE", "Correlation")) \
                else self._signal_unit(row.source_a)
        else:
            data = series.get(f"{row.device}:{row.signal}")
            if not data:
                return None
            if data.get("kind") == "events":
                return None  # Event devices overlay every stack via get_markers().
            times, values = data["times"], list(data["values"])
            segments = data.get("segments")
            name = f"{row.device}:{row.signal}"
            unit = self._signal_unit(f"{row.device}:{row.signal}")
            label = self._signal_label(f"{row.device}:{row.signal}") or row.signal
        values, _ = amplify_values(values, row.amplify)
        return {"times": times, "values": values, "segments": segments, "name": name,
                "right": row.axis == "Right", "color": row.color, "alpha": row.alpha,
                "zorder": row.zorder, "markers": row.transform == "markers",
                "steps": row.transform == "steps", "row_key": (row.stack, row.axis == "Right"),
                "amplify": row.amplify, "unit": unit, "label": label,
                "peaks": [], "annotations": [], "y_range": self._row_range(row)}

    def _refresh(self):
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        if self.closing:
            return
        try:
            self._finish_file_operation()
            self._update_transport()
            self._update_device_status()
            self._refresh_display()
        except Exception as error:
            self.plot_key = None
            self.plot_next = time.monotonic() + 1
            self.plot_status.set(f"Display error: {error} · retrying; capture is independent")
            self._log_error(f"Display refresh failed: {error}")
        finally:
            if not self.closing:
                self.refresh_job = self.root.after(50, self._refresh)

    def _refresh_display(self):
        now = time.monotonic()
        if self.plot_future is not None:
            if not self.plot_future.done():
                self.plot_status.set(f"Loading visualization… {now - self.plot_started:.1f}s · capture continues")
                return
            future, self.plot_future = self.plot_future, None
            built, invalid = future.result()
            if self.plot_request_generation == self.plot_generation:
                self._render_frame(built, invalid)
                self.plot_status.set("Visualization up to date")
            self.plot_next = now + REFRESH_MS / 1000
        if now < getattr(self, "plot_next", 0):
            return
        # Snapshots contain plain Python data only. No Tk variables or widgets
        # are accessed by the computation thread.
        requested = {f"{row.device}:{row.signal}" for row in self.signal_rows}
        requested.update(source for row in self.signal_rows for source in (row.source_a, row.source_b) if source)
        panels = {key: self._aligned_snapshot(key, panel.plot_snapshot({name.split(":", 1)[1] for name in requested
                                           if name.startswith(key + ":")}))
                  for key, panel in self.panels.items()}
        latest = max((panel.latest for panel in panels.values()), default=0.0)
        self.viewport.update(latest)
        self.time_scroll.set(*self.viewport.fractions())
        self.zoom_text.set(f"TIME ZOOM · {self.viewport.span:g} s")
        key = (self.plot_generation, self.viewport.start, self.viewport.span,
               tuple((name, panel.revision) for name, panel in panels.items()))
        if key == getattr(self, "plot_key", None):
            return
        self.plot_key = key
        renderer = Workspace.__new__(Workspace)
        renderer.panels = panels
        renderer.viewport = replace(self.viewport)
        renderer.signal_rows = [replace(row) for row in self.signal_rows]
        renderer.feature_cache, renderer.error_seen = {}, set()
        self.plot_request_generation = self.plot_generation
        self.plot_started = now
        self.plot_future = submit(renderer._compute_frame)

    def _aligned_snapshot(self, key, snapshot):
        origin = getattr(snapshot, "clock_origin", None)
        if origin is not None:
            self.timeline_offsets[key] = origin - self.clock_origin
        return AlignedSnapshot(snapshot, self.timeline_offsets.get(key, 0.0))

    def _compute_frame(self):
        series, latest = self._collect_series()
        built = [trace for row in self.signal_rows if not row.hidden
                 for trace in [self._build_trace(row, series)] if trace]

        # "normalize row/axis": one common scale per stack+axis group,
        # overriding every other amplification in that group.
        groups = {}
        for trace in built:
            groups.setdefault(trace["row_key"], []).append(trace)
        for group in groups.values():
            if any(trace["amplify"] == "normalize row/axis" for trace in group):
                peak = max((max((abs(v) for v in trace["values"]
                                 if isinstance(v, float) and math.isfinite(v)), default=0.0)
                            for trace in group), default=0.0)
                if peak > 0:
                    for trace in group:
                        trace["values"] = [v / peak for v in trace["values"]]

        window = self.viewport.span
        low, high = self.viewport.start - window, self.viewport.start + 2 * window
        invalid = []
        for panel in self.panels.values():
            invalid.extend(panel.invalid_spans(low, high))
        # Merge adjacent exclusions before sending them to Tk.
        merged = []
        for first, last in sorted(invalid):
            if merged and first <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
            else:
                merged.append((first, last))
        for trace in built:
            trace.update(compact_series(trace))
            for key in ("annotations", "peaks"):
                values = trace[key]
                trace[key] = values[::max(1, (len(values) + 199) // 200)]
        markers = []
        test_rows = [row for row in self.signal_rows if row.device == "tests"]
        marker_zorder = min((row.zorder for row in test_rows), default=0)
        hide_markers = bool(test_rows) and all(row.hidden for row in test_rows)
        for panel in self.panels.values():
            method = getattr(panel, "get_markers", None)
            if method is None or hide_markers:
                continue
            for marker in method(self.viewport.start, self.viewport.start + window):
                # Events carry their own color (palette name or hex); 2-tuples
                # (legacy snapshots) fall back to neutral white.
                color = marker[2] if len(marker) > 2 else "white"
                if not color.startswith("#"):
                    color = MARKER_COLORS.get(color, "#ffffff")
                markers.append((marker[0], marker[1], color, marker_zorder))
        # Dense views aggregate nearby markers; the full event history stays in
        # the recording. Bound Tk work regardless of session length/zoom.
        if len(markers) > 400:
            buckets = {}
            for stamp, label, color, zorder in sorted(markers, key=lambda m: m[0]):
                bucket = min(399, int((stamp - self.viewport.start) / window * 400))
                first, labels, colors, count = buckets.get(bucket, (stamp, [], set(), 0))
                if label not in labels:
                    labels.append(label)
                colors.add(color)
                buckets[bucket] = first, labels, colors, count + 1
            markers = [(stamp, "/".join(labels) + (f" ×{count}" if count > 1 else ""),
                        next(iter(colors)) if len(colors) == 1 else "#ffffff", zorder)
                       for stamp, labels, colors, count in buckets.values()]
        if markers:
            built.append({"event_markers": sorted(markers)})
        return built, merged[::max(1, (len(merged) + 399) // 400)]

    def _render_frame(self, built, invalid):
        markers = [marker for trace in built for marker in trace.get("event_markers", [])]
        built = [trace for trace in built if "event_markers" not in trace]
        traces_per_stack = {}
        annotations_per_stack = {}
        peaks_per_stack = {}
        axis_labels_per_stack = {}
        fixed_per_stack = {}
        for trace in built:
            stack = trace["row_key"][0]
            traces_per_stack.setdefault(stack, []).append(Trace(
                trace["times"], trace["values"], trace["color"], name=trace["name"],
                right=trace["right"], segments=trace["segments"], alpha=trace["alpha"],
                zorder=trace["zorder"], markers=trace["markers"], steps=trace["steps"],
                line=trace.get("line", True)))
            y_range = trace.get("y_range")
            if y_range:
                # Several fixed rows may share one axis: pin it to their union.
                lo, hi = fixed_per_stack.get((stack, trace["right"]), y_range)
                fixed_per_stack[(stack, trace["right"])] = (min(lo, y_range[0]),
                                                            max(hi, y_range[1]))
            if trace.get("annotations"):
                annotations_per_stack.setdefault(stack, []).extend(trace["annotations"])
            if trace.get("peaks"):
                peaks_per_stack.setdefault(stack, []).extend(trace["peaks"])
            unit = trace.get("unit", "")
            axis_label = f"{trace.get('label', '')} ({unit})" if unit else trace.get("label", "")
            if axis_label:
                axis_labels = axis_labels_per_stack.setdefault(stack, {False: [], True: []})
                if axis_label not in axis_labels[trace["right"]]:
                    axis_labels[trace["right"]].append(axis_label)
        stacks = sorted(traces_per_stack) or [1]
        self._ensure_stack_plots(stacks)
        for number, plot in self.row_plots.items():
            plot.grid()
            axis_labels = axis_labels_per_stack.get(number, {})
            left_label = " · ".join(dict.fromkeys(axis_labels.get(False, [])))
            right_label = " · ".join(dict.fromkeys(axis_labels.get(True, [])))
            plot.set_units(left_label, right_label, redraw=False)
            fixed = {axis: fixed_per_stack.get((number, axis)) for axis in (False, True)}
            plot.set_data(traces_per_stack.get(number, []), invalid=invalid,
                          markers=markers,
                          peaks=peaks_per_stack.get(number, []),
                          annotations=annotations_per_stack.get(number, []),
                          fixed_bounds=fixed)
        for number, plot in self.row_plots.items():
            if number not in stacks:
                plot.grid_remove()
        for key, panel in self.panels.items():
            if hasattr(panel, "cursor_time"):
                panel.cursor_time = (None if self.viewport.cursor is None else
                                     self.viewport.cursor - self.timeline_offsets.get(key, 0.0))
                panel.cursor_follows_live = self.viewport.cursor is None
        self.time_scroll.set(*self.viewport.fractions())
        self.zoom_text.set(f"TIME ZOOM · {self.viewport.span:g} s")

    def _ensure_stack_plots(self, stacks):
        for number in stacks:
            if number not in self.row_plots:
                plot = TimePlot(self.plot_area, self.viewport, f"Row {number}", "",
                                self._navigate, height=200)
                plot.grid(row=number, column=0, sticky="nsew", pady=(0, 6))
                self.row_plots[number] = plot
        self.plot_area.rowconfigure(0, weight=0)
        for number in self.row_plots:
            self.plot_area.rowconfigure(number, weight=1, uniform="stacks")

    def _navigate(self):
        self.plot_generation += 1
        self.plot_next = 0
        self.plot_status.set("Loading visualization… · capture continues")

    def _scroll(self, action, value, *_args):
        if action == "moveto":
            self.viewport.moveto(float(value))
        else:
            self.viewport.scroll(float(value))
        self._navigate()

    def _zoom(self, factor):
        self.viewport.zoom(factor)
        self._navigate()

    def _go_live(self):
        self.viewport.live()
        self._navigate()

    # --------------------------------------------------------------- transport

    def _transport(self, action):
        if self.file_action == "load":
            return
        """Apply a common start/pause/stop command to every enabled device."""
        targets = [panel for panel in self.panels.values()
                   if hasattr(panel, "transport")]
        if not targets:
            self.transport_text.set("No device enabled")
            return
        for panel in targets:
            try:
                panel.transport(action)
            except Exception as error:
                self._log_error(f"Transport {action} failed: {error}")
        self._update_transport()

    def _update_transport(self):
        """Reflect the common transport state across all enabled devices."""
        states = []
        for key, panel in self.panels.items():
            getter = getattr(panel, "transport_state", None)
            if getter is None:
                continue
            try:
                states.append(getter())
            except Exception:
                states.append("unknown")
        running = [state for state in states if state == "running"]
        busy = [state for state in states if state in ("busy", "starting", "stopping")]
        if not states:
            text, enabled = "No device enabled", ()
        elif running:
            text, enabled = "Recording all devices", ("pause", "stop")
        elif busy:
            text, enabled = "Waiting for device(s)…", ()
        else:
            text, enabled = "Ready · all devices idle", ("start",)
        self.transport_text.set(text)
        for action, button in self.transport_buttons.items():
            button.state(["!disabled"] if action in enabled or not states else ["disabled"])
        if states and not enabled and not busy:
            # Keep Start available so a stopped device can be relaunched.
            self.transport_buttons["start"].state(["!disabled"])

    # --------------------------------------------------------------- sessions

    def save_all(self):
        if self.file_future is not None:
            return
        try:
            configuration = self._configuration_bundle()
            recordings = {key: panel.recording_snapshot() for key, panel in self.panels.items()}
            recordings = {key: data for key, data in recordings.items() if data["count"]}
            if not recordings:
                messagebox.showinfo(parent=self.root, title="Save workspace CSV", message="No recorded samples to save yet.")
                return
            renderer = Workspace.__new__(Workspace)
            renderer.panels = {key: self._aligned_snapshot(key, self.panels[key].plot_snapshot(self.panels[key].signals()))
                               for key in recordings}
            configuration["time_alignment"] = {"offsets_s": dict(self.timeline_offsets),
                                               "clock": "host monotonic", "origin_monotonic_s": self.clock_origin}
            renderer.signal_rows = [replace(row) for row in self.signal_rows]
            renderer.feature_cache, renderer.error_seen = {}, set()
            path = filedialog.asksaveasfilename(
                parent=self.root, title="Save all devices and signals", defaultextension=".csv",
                filetypes=[("Workspace CSV", "*.csv")])
            if not path:
                return
            self.file_future = submit(write_session, path, configuration, recordings, renderer._export_signals())
            self.file_action = "save"
            self.file_status.set("Saving all devices and signals…")
        except (OSError, ValueError) as error:
            messagebox.showerror(parent=self.root, title="Save workspace CSV", message=str(error))

    def _export_signals(self):
        """Full-resolution, full-history series; called only on worker snapshots."""
        series = {}
        for device, panel in self.panels.items():
            catalogue = panel.signals()
            for name, data in panel.get_series().items():
                series[f"{device}:{name}"] = data
                yield {"device": device, "signal": name, "unit": catalogue[name].get("unit", ""), "data": data}
        # Include every configured row, including comparisons and feature measures.
        # Values use physical units; display-only amplification is saved in JSON.
        for index, row in enumerate(self.signal_rows):
            if row.device == "features":
                data = series.get(row.source_a)
                if data is None:
                    continue
                peaks = self._cached_peaks(row, data)
                if peaks is None:
                    continue
                times, values = peaks
                unit = self._signal_unit(row.source_a)
                if row.signal != "Phasic peak detection":
                    measures = features.peak_measures(data["times"], data["values"], times, values)
                    field = {"Peak-to-peak amplitude": "amplitude", "Peak rising time": "rise_s",
                             "Peak falling time": "fall_s"}[row.signal]
                    times, values = [m["time"] for m in measures], [m[field] for m in measures]
                    if field != "amplitude":
                        unit = "s"
                data = {"times": times, "values": values}
            else:
                trace = self._build_trace(replace(row, amplify="1x"), series)
                if trace is None:
                    continue
                data, unit = trace, trace["unit"]
            yield {"device": row.device, "signal": row.signal, "row": index + 1, "unit": unit, "data": data}

    def _can_load_workspace(self):
        for key, panel in self.panels.items():
            if key == "biopac":
                busy = getattr(panel, "state", "disconnected") != "disconnected" or panel.pending
            else:
                busy = getattr(panel, "recording_state", "stopped") != "stopped" or panel.operation is not None
            busy = busy or getattr(panel, "save_future", None) is not None
            if busy:
                messagebox.showinfo(parent=self.root, title="Load workspace",
                                    message="Stop/disconnect all devices and wait for pending operations before loading a workspace.")
                return False
        return True

    def _finish_file_operation(self):
        if self.file_future is None or not self.file_future.done():
            return
        future, action = self.file_future, self.file_action
        self.file_future = self.file_action = None
        try:
            result = future.result()
            if action == "save":
                self.file_status.set(f"Saved workspace: {result.name}")
                return
            directory, configuration, files = result
            try:
                self._validate_configuration_bundle(configuration)
                if not self._can_load_workspace():
                    directory.cleanup()
                    self.file_status.set("Load cancelled: devices are busy")
                    return
                # Every recording was validated before touching any panel.
                self._apply_configuration_bundle(configuration)
                for device, panel in self.panels.items():
                    if device not in files:
                        clear = getattr(panel, "clear_recording", None)
                        if clear is not None:
                            clear()
                for device, path in files.items():
                    if device not in self.panels:
                        self.device_frames[device]["variable"].set(True)
                    panel = self.panels[device]
                    loaded = panel.load_csv(path) if device == "biopac" else panel.load_recording(path)
                    if loaded is None:
                        raise ValueError(f"Could not load {device} recording")
                self._sync_decomposition()
                self.timeline_offsets = {key: float(value) for key, value in
                                         configuration.get("time_alignment", {}).get("offsets_s", {}).items()}
            except BaseException:
                directory.cleanup()
                raise
            self.recording_directories.append(directory)
            self.viewport.latest = 0
            self.viewport.live()
            self._navigate()
            self.file_status.set("Loaded all devices and signals")
        except Exception as error:
            self.file_status.set(f"Workspace {action} failed")
            messagebox.showerror(parent=self.root, title=f"Workspace {action}", message=str(error))

    @staticmethod
    def _recording_device(path):
        """Which device SDK owns this CSV, from its header columns."""
        try:
            with Path(path).open(encoding="utf-8-sig") as stream:
                header = stream.readline()
        except OSError:
            return None
        if "vco_p_uV" in header:
            return "heepidermis"
        if "adc_count" in header:
            return "biopac"
        if "event_label" in header:
            return "tests"
        return None

    def load_csv(self):
        """Restore a workspace CSV, or import a legacy single-device recording."""
        if self.file_future is not None:
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Load recording into the workspace",
            filetypes=[("CSV recordings", "*.csv"), ("All files", "*")])
        if not path:
            return
        try:
            with Path(path).open(newline="", encoding="utf-8-sig") as stream:
                header = next(csv.reader(stream), [])
            if "workspace_kind" in header:
                if not self._can_load_workspace():
                    return
                from .sdk.heepidermis.recording import read_recording as read_heepidermis
                from .sdk.biopac.recording import load_recording as read_biopac
                from .sdk.tests.recording import read_recording as read_tests
                self.file_future = submit(read_session, path,
                                          {"heepidermis": read_heepidermis,
                                           "biopac": read_biopac, "tests": read_tests},
                                          {"stroop": "tests"})
                self.file_action = "load"
                self.file_status.set("Loading all devices and signals…")
                return
        except (OSError, ValueError) as error:
            messagebox.showerror(parent=self.root, title="Load CSV", message=str(error))
            return
        device = self._recording_device(path)
        if device is None:
            messagebox.showerror(parent=self.root, title="Load CSV",
                                 message="Unrecognized recording: the file has neither HEEPidermis "
                                         "(vco_p_uV), BIOPAC (adc_count), nor Tests (event_label) columns.")
            return
        frame = self.device_frames.get(device)
        if device not in self.panels and frame is not None and not frame["variable"].get():
            frame["variable"].set(True)  # Constructs the panel synchronously.
        panel = self.panels.get(device)
        if panel is None:
            messagebox.showerror(parent=self.root, title="Load CSV",
                                 message=f"The {device} panel could not be started.")
            return
        if device == "biopac":
            if getattr(panel, "state", "disconnected") != "disconnected" or panel.pending:
                messagebox.showinfo(parent=self.root, title="Load CSV",
                                    message="Disconnect BIOPAC (and wait for pending commands) "
                                            "before loading a recording into it.")
                return
            panel.load_csv(path)
        else:
            if getattr(panel, "recording_state", "stopped") != "stopped" or panel.operation is not None:
                messagebox.showinfo(parent=self.root, title="Load CSV",
                                    message="Pause the HEEPidermis recording and wait for hardware "
                                            "operations before loading a recording into it.")
                return
            if panel.load_recording(path) is None:
                return  # The panel log explains why the file was refused.
        self.timeline_offsets.pop(device, None)
        # Make the loaded recording visible right away: follow its newest
        # samples, and add a row if the table has none for this device. The
        # row shows the device's primary magnitude; if the top stack already
        # has a Left-axis trace, the new one takes the Right axis so the two
        # devices read side by side instead of overlapping.
        if not any(row.device == device for row in self.signal_rows):
            axis = ("Right" if any(row.stack == 1 and row.axis == "Left"
                                   for row in self.signal_rows) else "Left")
            self.add_signal_row(device, self._primary_signal(device), axis=axis)
        self.viewport.live()
        self._sync_decomposition()
        self._show_report(device)
        self._navigate()

    def _log_error(self, message):
        if message in self.error_seen:
            return
        self.error_seen.add(message)
        print(f"[workspace] {message}", file=sys.stderr)

    # ----------------------------------------------------------- persistence

    def _state_changed(self):
        self._navigate()
        if self.save_job is not None:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(400, self._save_state)

    def _save_state(self):
        self.save_job = None
        try:
            state = self._configuration_bundle()
            WORKSPACE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            temporary = WORKSPACE_CONFIG.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary.replace(WORKSPACE_CONFIG)
        except (OSError, ValueError):
            pass

    def _workspace_state(self):
        return {
            "devices": {key: frame["variable"].get() for key, frame in self.device_frames.items()},
            "span": self.viewport.span,
            "rows": [asdict(row) for row in self.signal_rows],
            "decomposition": self._decomposition_settings(),
        }

    def _apply_workspace_state(self, state):
        if not isinstance(state, dict):
            return
        state = self._migrate_legacy_state(state)
        for key, value in state.get("decomposition", {}).items():
            if key in self.decomposition_variables:
                self.decomposition_variables[key].set(str(value))
        try:
            self.viewport.span = max(1.0, min(7200.0, float(state.get("span", self.viewport.span))))
        except (TypeError, ValueError):
            pass
        for key, enabled in state.get("devices", {}).items():
            frame = self.device_frames.get(key)
            if frame is not None:
                frame["variable"].set(bool(enabled))
        for widgets in list(self.table_rows):
            self._remove_row(widgets)
        for values in state.get("rows", []):
            try:
                row = SignalRow(**{k: v for k, v in values.items() if k in SignalRow.__dataclass_fields__})
                self.signal_rows.append(row)
                self._add_table_row(row)
            except (TypeError, ValueError):
                continue

    @staticmethod
    def _migrate_legacy_state(state):
        """Rename the pre-refactor Stroop device to the generic Tests device."""
        devices = state.get("devices")
        if isinstance(devices, dict) and "stroop" in devices and "tests" not in devices:
            devices["tests"] = devices.pop("stroop")
        rows = state.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("device") == "stroop":
                    row["device"] = "tests"
        return state

    def _load_state(self):
        self.table_rows = []
        if not WORKSPACE_CONFIG.exists():
            return
        try:
            state = json.loads(WORKSPACE_CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(state, dict) or state.get("version") != 1:
            return
        self.viewport.span = 30.0
        if "workspace" in state:
            try:
                self._validate_configuration_bundle(state)
                self._apply_configuration_bundle(state)
            except (ValueError, TypeError) as error:
                self._log_error(f"Last workspace configuration not restored: {error}")
        else:
            self._apply_workspace_state(state)  # Older layout-only autosaves.

    # ------------------------------------------------- workspace configuration

    def save_configuration(self):
        """Save everything: signal table, devices, span and each device panel's
        own configuration (acquisition, processing, hardware settings)."""
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save workspace configuration",
            initialdir=str(WORKSPACE_CONFIG.parent), defaultextension=".json",
            filetypes=[("JSON configurations", "*.json")])
        if not path:
            return
        try:
            bundle = self._configuration_bundle()
            Path(path).write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
        except (OSError, ValueError) as error:
            messagebox.showerror(parent=self.root, title="Save configuration",
                                 message=f"Configuration not saved: {error}")
            return
        print(f"[workspace] configuration saved: {path}")

    def load_configuration(self):
        if self.file_future is not None or not self._can_load_workspace():
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Load workspace configuration",
            initialdir=str(WORKSPACE_CONFIG.parent), filetypes=[("JSON configurations", "*.json")])
        if not path:
            return
        try:
            bundle = json.loads(Path(path).read_text(encoding="utf-8"))
            self._validate_configuration_bundle(bundle)
        except (OSError, ValueError, TypeError) as error:
            messagebox.showerror(parent=self.root, title="Load configuration",
                                 message=f"Configuration not loaded: {error}")
            return
        self._apply_configuration_bundle(bundle)
        print(f"[workspace] configuration loaded: {path}")

    def _configuration_bundle(self):
        return {"version": 1, "workspace": self._workspace_state(),
                "devices": self._device_configurations()}

    @staticmethod
    def _validate_configuration_bundle(bundle):
        if (not isinstance(bundle, dict) or bundle.get("version") != 1 or
                not isinstance(bundle.get("workspace"), dict) or not isinstance(bundle.get("devices"), dict)):
            raise ValueError("Unsupported workspace configuration file")
        Workspace._migrate_legacy_bundle(bundle)
        state = bundle["workspace"]
        if not isinstance(state.get("devices", {}), dict) or not isinstance(state.get("rows", []), list):
            raise ValueError("Invalid workspace device selection or signal table")
        for row in state.get("rows", []):
            if not isinstance(row, dict):
                raise ValueError("Invalid signal row")
        from .sdk.biopac.configuration import Configuration, ProcessingSettings
        ProcessingSettings.parse(state.get("decomposition", {}))
        from .sdk.heepidermis.configuration import validate_configuration
        from .sdk.heepidermis.panel import SAMPLING_RATES_HZ
        for key, values in bundle["devices"].items():
            if key == "biopac":
                Configuration.parse(values)
            elif key == "heepidermis":
                validate_configuration(values, VCOGui._configuration_choices(None), len(SAMPLING_RATES_HZ))
            elif key == "tests":
                from .sdk.tests.panel import validate_configuration as validate_tests
                validate_tests(values)
        offsets = bundle.get("time_alignment", {}).get("offsets_s", {})
        if not isinstance(offsets, dict) or any(not math.isfinite(float(value)) for value in offsets.values()):
            raise ValueError("Invalid recording time alignment")

    @staticmethod
    def _migrate_legacy_bundle(bundle):
        """Rename the pre-refactor Stroop device to the generic Tests device."""
        devices = bundle.get("devices")
        if isinstance(devices, dict) and "stroop" in devices and "tests" not in devices:
            devices["tests"] = devices.pop("stroop")
        workspace = bundle.get("workspace")
        if isinstance(workspace, dict):
            Workspace._migrate_legacy_state(workspace)

    def _apply_configuration_bundle(self, bundle):
        Workspace._migrate_legacy_bundle(bundle)
        self.saved_device_configurations = dict(bundle["devices"])
        self._apply_workspace_state(bundle["workspace"])
        # Disabling an old panel can cache its current settings. The loaded
        # bundle is authoritative, including settings for disabled devices.
        self.saved_device_configurations = dict(bundle["devices"])
        for key, values in self.saved_device_configurations.items():
            apply = getattr(self.panels.get(key), "apply_configuration", None)
            if apply is not None:
                apply(values)
        self._sync_decomposition()
        self._state_changed()

    def _device_configurations(self):
        snapshots = dict(self.saved_device_configurations)
        for device in DEVICES:
            panel = self.panels.get(device["key"])
            snapshot = getattr(panel, "configuration_snapshot", None)
            if snapshot is None:
                continue
            try:
                values = snapshot()
            except Exception as error:
                self._log_error(f"{device['key']} configuration not saved: {error}")
                continue
            if values:
                snapshots[device["key"]] = values
        return snapshots

    def _on_close(self):
        self._save_state()
        self.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    Workspace(root)
    root.mainloop()


if __name__ == "__main__":
    main()
