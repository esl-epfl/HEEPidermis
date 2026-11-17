#!/usr/bin/env python3
"""Native Linux MP160 live monitor; amplifier gain and filters are manual declarations."""

from __future__ import annotations

import argparse
import bisect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import queue
import signal
import socket
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from gui_common.eda import ALGORITHMS
    from gui_common.plots import TimePlot, TimeViewport, Trace
    from gui_common.theme import (UI_ACCENT, UI_BACKGROUND, UI_BORDER, UI_MUTED,
                                  UI_PLOT, UI_TEXT, configure_style)
    from gui_common.widgets import Tooltip
    from biopac.acquire import RATES
    from biopac.configuration import (Configuration, ProcessingSettings, default_path,
                                      read_configuration, write_configuration)
    from biopac.controller import AcquisitionWorker
    from biopac.processing import analyze_recording
    from biopac.profiles import PROFILES, get_profile
    from biopac.recording import Recording, Settings, export_recording, load_recording, moving_average
except ImportError as error:
    raise SystemExit("GUI dependencies missing. Run: python3 -m pip install -r sw/biopac/requirements.txt\n"
                     f"{error}") from error


PROFILE_CHOICES = {profile.name: key for key, profile in PROFILES.items()}


class BiopacGUI:
    """Tk owns history and widgets; the acquisition thread alone owns the device."""

    def __init__(self, root, configuration=None, *, config_path=None, demo=False, worker=None):
        self.root, self.demo = root, demo
        self.config_path = Path(config_path or default_path())
        self.configuration = configuration or Configuration()
        self.recording, self.metadata = Recording(), {}
        self.viewport = TimeViewport(span=self.configuration.span)
        self.processing = self.configuration.processing
        self.applied = None
        self.device = {}
        self.state, self.pending, self.closing, self.closed = "disconnected", False, False, False
        self.dirty, self.epoch = True, 0
        self.analysis, self.analysis_future = {}, None
        self.last_analysis, self.last_render = 0.0, 0.0
        self.analysis_key = None
        self.processing_job = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="biopac-analysis")
        # ThreadPoolExecutor.shutdown(cancel_futures=...) requires Python 3.9+.
        self.new_executor_api = sys.version_info >= (3, 9)
        self.worker = worker or AcquisitionWorker(demo=demo)
        self.hardware_widgets, self.network_widgets = [], []
        configure_style(root)
        root.title("BIOPAC · MP160 live monitor" + (" — DEMO / SIMULATED" if demo else ""))
        width, height = min(1540, root.winfo_screenwidth() - 60), min(1000, root.winfo_screenheight() - 80)
        root.geometry(f"{max(1050, width)}x{max(720, height)}")
        root.minsize(1050, 720)
        self._build()
        self._set_configuration(self.configuration)
        for variable in self.hardware_variables.values():
            variable.trace_add("write", self._settings_changed)
        for variable in self.processing_variables.values():
            variable.trace_add("write", self._processing_changed)
        self._settings_changed()
        self._update_controls()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<space>", self._space, add="+")
        self.worker.start()
        self.pump_job = root.after(40, self._pump)
        self._log("SIMULATED DATA ONLY — no BIOPAC hardware is accessed." if demo else
                  "Native Linux UDP. Connect to the dedicated Ethernet interface; no network settings are changed.")
        self._log("Pause before changing physical gain/filter switches, then enter the new settings and Apply.")

    def _build(self):
        header = ttk.Frame(self.root, style="App.TFrame", padding=(18, 12))
        header.pack(fill="x")
        ttk.Label(header, text="BIOPAC", style="PlotTitle.TLabel", font=("Helvetica", 19, "bold")).pack(side="left")
        ttk.Label(header, text="MP160 · EDA100C / BioNomadix EDA · native Linux",
                  style="WorkspaceMuted.TLabel").pack(side="left", padx=18)
        self.status = tk.StringVar(value="Disconnected")
        ttk.Label(header, textvariable=self.status, style="Workspace.TLabel").pack(side="right")

        body = ttk.Frame(self.root, style="App.TFrame", padding=(12, 0, 12, 10))
        body.pack(fill="both", expand=True)
        sidebar_host = ttk.Frame(body)
        sidebar_host.pack(side="left", fill="y", padx=(0, 12))
        sidebar_canvas = tk.Canvas(sidebar_host, width=334, background=UI_BACKGROUND, highlightthickness=0)
        sidebar_scroll = ttk.Scrollbar(sidebar_host, orient="vertical", command=sidebar_canvas.yview)
        sidebar_scroll.pack(side="right", fill="y")
        sidebar_canvas.pack(fill="both", expand=True)
        sidebar_canvas.configure(yscrollcommand=sidebar_scroll.set)
        sidebar = ttk.Frame(sidebar_canvas, style="App.TFrame")
        sidebar_window = sidebar_canvas.create_window(0, 0, window=sidebar, anchor="nw")
        sidebar.bind("<Configure>", lambda _e: sidebar_canvas.configure(scrollregion=sidebar_canvas.bbox("all")))
        sidebar_canvas.bind("<Configure>", lambda e: sidebar_canvas.itemconfigure(sidebar_window, width=e.width))

        network = self._section(sidebar, "01  MP160 connection")
        self.interface, self.ip, self.output_dir = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._field(network, "Dedicated Ethernet interface", self.interface, 0,
                    values=[name for _, name in socket.if_nameindex() if name != "lo"], group=self.network_widgets)
        self._field(network, "MP160 IPv4 address (blank = discover)", self.ip, 1, group=self.network_widgets)
        connection_buttons = ttk.Frame(network)
        connection_buttons.grid(row=4, column=0, sticky="ew", pady=(7, 2))
        self.connect_button = ttk.Button(connection_buttons, text="Connect", command=self.connect, style="Accent.TButton")
        self.connect_button.pack(side="left", fill="x", expand=True)
        self.disconnect_button = ttk.Button(connection_buttons, text="Disconnect", command=lambda: self._send("disconnect"))
        self.disconnect_button.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.device_text = tk.StringVar(value="No device connected")
        ttk.Label(network, textvariable=self.device_text, style="Muted.TLabel", wraplength=298).grid(row=5, column=0, sticky="w", pady=(6, 0))

        hardware = self._section(sidebar, "02  Acquisition + manual hardware settings")
        self.profile_var = tk.StringVar()
        self.profile_combo = self._field(hardware, "Hardware profile (manual preset)", self.profile_var, 0,
                                        values=tuple(PROFILE_CHOICES), readonly=True, group=self.hardware_widgets)
        self.profile_combo.bind("<<ComboboxSelected>>", self._profile_selected)
        Tooltip(self.profile_combo, "Loads channel, rate, sensitivity, zero and filter defaults. Review them, then Start/Apply. "
                                   "This does not detect, pair or configure the receiver or amplifier.")
        self.profile_hint = tk.StringVar()
        ttk.Label(hardware, textvariable=self.profile_hint, style="Muted.TLabel", wraplength=298).grid(
            row=2, column=0, sticky="w", pady=(0, 8))
        acquisition = ttk.Frame(hardware)
        acquisition.grid(row=3, column=0, sticky="ew")
        acquisition.columnconfigure(0, weight=1)
        self.hardware_variables = {name: tk.StringVar() for name in
                                   ("channel", "rate", "sensitivity", "zero_voltage", "filter_1", "filter_2", "filter_3")}
        pair = ttk.Frame(acquisition)
        pair.grid(row=0, column=0, sticky="ew")
        pair.columnconfigure((0, 1), weight=1)
        for column, (name, label, choices) in enumerate((("channel", "Analog channel (1–16)", list(range(1, 17))), ("rate", "Rate (Hz)", RATES))):
            ttk.Label(pair, text=label).grid(row=0, column=column, sticky="w")
            widget = ttk.Combobox(pair, textvariable=self.hardware_variables[name], values=choices, width=10, state="readonly")
            widget.grid(row=1, column=column, sticky="ew", padx=(0, 6) if column == 0 else (0, 0), pady=(3, 7))
            self.hardware_widgets.append(widget)
        sensitivity = self._field(acquisition, "Sensitivity (µS/V) — physical gain", self.hardware_variables["sensitivity"], 1, group=self.hardware_widgets)
        Tooltip(sensitivity, "Enter the physical amplifier/receiver sensitivity in µS/V (EDA100C default 2; BioNomadix EDA nominal 5). "
                             "Conductance = (voltage − calibrated zero) × sensitivity. Raw ADC counts and volts are unchanged.")
        zero = self._field(acquisition, "Calibrated zero voltage (V)", self.hardware_variables["zero_voltage"], 2, group=self.hardware_widgets)
        Tooltip(zero, "Use a known, calibrated electrical zero; not the subject's starting baseline. Signed and negative values are retained.")
        filters = ttk.Frame(acquisition)
        filters.grid(row=6, column=0, sticky="ew", pady=(0, 7))
        filters.columnconfigure((0, 1, 2), weight=1)
        self.filter_labels = []
        for i in range(3):
            label = ttk.Label(filters, text=f"Filter {i + 1}")
            label.grid(row=0, column=i, sticky="w")
            self.filter_labels.append(label)
            widget = ttk.Entry(filters, textvariable=self.hardware_variables[f"filter_{i + 1}"], width=8)
            widget.grid(row=1, column=i, sticky="ew", padx=(0, 5) if i < 2 else 0, pady=(3, 0))
            self.hardware_widgets.append(widget)
            Tooltip(widget, "Manual physical filter metadata, not a software filter. For BioNomadix, slot 1 is overall EDA bandwidth "
                            "(DC to 3 Hz); slots 2/3 are N/A, not additional stages. Nothing here changes receiver switches.")
        ttk.Label(acquisition, text="MANUAL: pause before moving hardware switches.\nGain and filters cannot be read or set remotely.",
                  style="Muted.TLabel", wraplength=298).grid(row=7, column=0, sticky="w", pady=(0, 7))
        self.apply_button = ttk.Button(acquisition, text="Apply settings", command=self.apply_settings)
        self.apply_button.grid(row=8, column=0, sticky="ew")
        self.settings_status = tk.StringVar()
        ttk.Label(acquisition, textvariable=self.settings_status, style="Muted.TLabel", wraplength=298).grid(row=9, column=0, sticky="w", pady=(6, 0))

        processing = self._section(sidebar, "03  Software processing (does not alter raw data)")
        self.processing_variables = {name: tk.StringVar() for name in ("average", "smoothing", "prominence", "algorithm")}
        self._field(processing, "Moving average (samples; 1 = off)", self.processing_variables["average"], 0)
        self._field(processing, "Tonic smoothing (λ)", self.processing_variables["smoothing"], 1)
        self._field(processing, "Peak prominence (µS)", self.processing_variables["prominence"], 2)
        self._field(processing, "Shared HEEPidermis EDA solver", self.processing_variables["algorithm"], 3, values=ALGORITHMS, readonly=True)
        ttk.Label(processing, text="Anti-alias resampling → 10 Hz analysis.\nLive right-edge results are provisional; not a clinical or proprietary BIOPAC algorithm.",
                  style="Muted.TLabel", wraplength=298).grid(row=8, column=0, sticky="w", pady=(4, 0))

        files = self._section(sidebar, "04  Files")
        ttk.Label(files, text="Autosave folder (next connection)", style="Muted.TLabel").pack(anchor="w")
        output_entry = ttk.Entry(files, textvariable=self.output_dir)
        output_entry.pack(fill="x", pady=(3, 6))
        self.network_widgets.append(output_entry)
        file_buttons = ttk.Frame(files)
        file_buttons.pack(fill="x")
        self.load_button = ttk.Button(file_buttons, text="Load CSV…", command=self.load_csv)
        self.load_button.pack(side="left", expand=True, fill="x")
        self.export_button = ttk.Button(file_buttons, text="Export CSV…", command=self.export_csv)
        self.export_button.pack(side="left", expand=True, fill="x", padx=(5, 0))
        config_buttons = ttk.Frame(files)
        config_buttons.pack(fill="x", pady=(6, 0))
        self.load_config_button = ttk.Button(config_buttons, text="Load config…", command=self.load_config)
        self.load_config_button.pack(side="left", expand=True, fill="x")
        ttk.Button(config_buttons, text="Save config…", command=self.save_config).pack(side="left", expand=True, fill="x", padx=(5, 0))

        workspace = ttk.Frame(body, style="Workspace.TFrame")
        workspace.pack(side="left", fill="both", expand=True)
        toolbar = ttk.Frame(workspace, style="Workspace.TFrame")
        toolbar.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(toolbar, text="Start recording", command=self.start, style="Accent.TButton")
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(toolbar, text="Pause + verify", command=lambda: self._send("pause"))
        self.pause_button.pack(side="left", padx=6)
        self.new_button = ttk.Button(toolbar, text="New session", command=self.new_session)
        self.new_button.pack(side="left")
        mode = "DEMO · SIMULATED DATA" if self.demo else "REAL DEVICE · INPUT ONLY"
        ttk.Label(toolbar, text=mode, style="WorkspaceMuted.TLabel").pack(side="right")
        self.notice = tk.StringVar(value="Live data is provisional until Pause/Disconnect stops and verifies the segment.")
        ttk.Label(workspace, textvariable=self.notice, wraplength=1040, style="WorkspaceMuted.TLabel").pack(fill="x", pady=(0, 5))
        self.readout = tk.StringVar(value="No samples")
        ttk.Label(workspace, textvariable=self.readout, style="Workspace.TLabel", font=("Helvetica", 11, "bold")).pack(fill="x", pady=(2, 5))
        self.history_status = tk.StringVar(value="0 samples · signed nominal conversion · no baseline subtraction")
        ttk.Label(workspace, textvariable=self.history_status, style="WorkspaceMuted.TLabel").pack(fill="x", pady=(0, 8))
        plots = ttk.Frame(workspace, style="Workspace.TFrame")
        plots.pack(fill="both", expand=True)
        plots.columnconfigure(0, weight=1)
        for row in range(3):
            plots.rowconfigure(row, weight=1, uniform="plots")
        self.voltage_plot = TimePlot(plots, self.viewport, "01  Amplifier voltage", "V", self._navigate, height=180)
        self.conductance_plot = TimePlot(plots, self.viewport, "02  Skin conductance", "µS", self._navigate, height=180)
        self.analysis_plot = TimePlot(plots, self.viewport, "03  Tonic / phasic · 10 Hz", "tonic µS", self._navigate,
                                      right_unit="phasic µS", height=180)
        for row, plot in enumerate((self.voltage_plot, self.conductance_plot, self.analysis_plot)):
            plot.grid(row=row, column=0, sticky="nsew", pady=(0, 7))
        self.time_scroll = ttk.Scrollbar(workspace, orient="horizontal", command=self._scroll)
        self.time_scroll.pack(fill="x")
        navigation = ttk.Frame(workspace, style="Workspace.TFrame")
        navigation.pack(fill="x", pady=(6, 6))
        ttk.Button(navigation, text="− Zoom", command=lambda: self._zoom(2)).pack(side="left")
        ttk.Button(navigation, text="+ Zoom", command=lambda: self._zoom(0.5)).pack(side="left", padx=5)
        self.live_button = ttk.Button(navigation, text="Follow live", command=self.follow)
        self.live_button.pack(side="left")
        self.view_status = tk.StringVar()
        ttk.Label(navigation, textvariable=self.view_status, style="WorkspaceMuted.TLabel").pack(side="left", padx=10)
        ttk.Label(navigation, text="Click: cursor · wheel: history · time in seconds", style="WorkspaceMuted.TLabel").pack(side="right")
        self.source_text = tk.StringVar(value="No recording — data is autosaved when acquisition starts")
        ttk.Label(workspace, textvariable=self.source_text, style="WorkspaceMuted.TLabel", wraplength=1040).pack(fill="x", pady=(0, 5))
        self.log = tk.Text(workspace, height=4, background=UI_PLOT, foreground=UI_MUTED, insertbackground=UI_TEXT,
                           relief="flat", highlightthickness=1, highlightbackground=UI_BORDER, font=("Helvetica", 9), state="disabled")
        self.log.pack(fill="x")

    @staticmethod
    def _section(parent, text):
        section = ttk.LabelFrame(parent, text=text, padding=10)
        section.pack(fill="x", pady=(0, 10))
        section.columnconfigure(0, weight=1)
        return section

    @staticmethod
    def _field(parent, label, variable, row, *, values=None, readonly=False, group=None):
        ttk.Label(parent, text=label).grid(row=2 * row, column=0, sticky="w")
        widget = (ttk.Combobox(parent, textvariable=variable, values=values,
                               state="readonly" if readonly else "normal") if values is not None else
                  ttk.Entry(parent, textvariable=variable))
        widget.grid(row=2 * row + 1, column=0, sticky="ew", pady=(3, 7))
        if group is not None:
            group.append(widget)
        return widget

    def _settings(self):
        values = {name: variable.get() for name, variable in self.hardware_variables.items()}
        values["profile"] = PROFILE_CHOICES.get(self.profile_var.get(), "")
        values["filters"] = [values[f"filter_{i}"] for i in range(1, 4)]
        return Settings.parse(values)

    def _configuration(self):
        return Configuration.parse({"interface": self.interface.get(), "ip": self.ip.get(),
                                    "output_dir": self.output_dir.get(), "acquisition": self._settings().mapping(),
                                    "processing": asdict(ProcessingSettings.parse(
                                        {name: value.get() for name, value in self.processing_variables.items()})),
                                    "span": self.viewport.span})

    def _set_configuration(self, configuration):
        self.interface.set(configuration.interface or next((name for _, name in socket.if_nameindex()
                                                           if name.startswith(("en", "eth"))), ""))
        self.ip.set(configuration.ip)
        self.output_dir.set(configuration.output_dir)
        self._set_acquisition(configuration.acquisition)
        for name, value in asdict(configuration.processing).items():
            self.processing_variables[name].set(str(value))
        self.processing = configuration.processing
        self.viewport.span = configuration.span
        self.dirty, self.analysis_key = True, None

    def _set_acquisition(self, settings):
        profile = get_profile(settings.profile)
        self.profile_var.set(profile.name)
        for name in ("channel", "rate", "sensitivity", "zero_voltage"):
            self.hardware_variables[name].set(str(getattr(settings, name)))
        for i, value in enumerate(settings.filters, 1):
            self.hardware_variables[f"filter_{i}"].set(value)
        self.profile_hint.set(profile.notes)
        labels = ("Bandwidth", "Unused", "Unused") if settings.profile == "bionomadix-ppged-r" else ("Filter 1", "Filter 2", "Filter 3")
        for label, text in zip(self.filter_labels, labels):
            label.configure(text=text)
        self._settings_changed()

    def _profile_selected(self, _event=None):
        # Only explicit user selection loads defaults; saved overrides and past
        # sample calibration must survive configuration/recording reloads.
        self._set_acquisition(Settings.from_profile(PROFILE_CHOICES[self.profile_var.get()]))
        self.notice.set("Profile defaults loaded (including zero = 0 V). Review the physical setup, then Start/Apply; "
                        "previous samples retain their calibration. Verify BioNomadix pairing on the receiver before interpreting EDA.")

    def _settings_changed(self, *_args):
        try:
            requested = self._settings()
            if self.applied is None:
                text = "Not applied. Start uses the settings shown above."
            else:
                current = self.applied
                text = ("Applied" if requested == current else "PENDING CHANGES — not yet applied")
                text += f": {get_profile(current.profile).name}\nch {current.channel} · {current.rate} Hz · {current.sensitivity:g} µS/V\n"
                text += f"zero {current.zero_voltage:g} V · {' / '.join(current.filters)}"
        except (ValueError, TypeError) as error:
            text = f"Pending settings are invalid: {error}"
        self.settings_status.set(text)

    def _processing_changed(self, *_args):
        if self.processing_job is not None:
            self.root.after_cancel(self.processing_job)
        self.processing_job = self.root.after(300, self._apply_processing)

    def _apply_processing(self):
        self.processing_job = None
        try:
            settings = ProcessingSettings.parse({name: value.get() for name, value in self.processing_variables.items()})
        except (ValueError, TypeError) as error:
            self.notice.set(f"Software settings not applied: {error}")
            return
        self.processing = settings
        self.analysis, self.analysis_key, self.last_analysis = {}, None, 0.0
        self.dirty = True
        self.notice.set("Software settings updated; original ADC samples and hardware declarations are unchanged.")

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 250:
            self.log.delete("1.0", "50.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _send(self, command, **values):
        if self.closing or self.pending:
            return
        self.pending = True
        self._update_controls()
        self.worker.submit(command, **values)

    def connect(self):
        try:
            configuration = self._configuration()
        except (ValueError, TypeError) as error:
            self.notice.set(str(error))
            return
        self.notice.set("Connecting… No network configuration or amplifier switches are changed.")
        self._send("connect", interface=configuration.interface, ip=configuration.ip, output_dir=configuration.output_dir)

    def start(self):
        try:
            settings = self._settings()
        except (ValueError, TypeError) as error:
            self.notice.set(str(error))
            return
        self.follow()
        self.notice.set("Recording. Live samples are provisional until the stopped buffer is verified.")
        self._send("start", settings=settings)

    def apply_settings(self):
        try:
            settings = self._settings()
        except (ValueError, TypeError) as error:
            self.notice.set(str(error))
            return
        if self.state == "recording" and not messagebox.askokcancel(
                "Apply to a new segment", "This stops and verifies the current segment, then starts another with the new settings.\n\n"
                "For physical switch changes, pause FIRST, move the switches, update these fields, then resume. "
                "Samples already recorded keep their previous calibration.", parent=self.root):
            return
        self._send("apply", settings=settings)

    def new_session(self):
        if self.recording.samples and not messagebox.askokcancel("New session", "Clear the displayed history and begin a new autosave file next time you record?\n\nExisting autosave files are kept.", parent=self.root):
            return
        self._send("new")

    def _space(self, event):
        if isinstance(event.widget, (ttk.Entry, ttk.Combobox, tk.Text)):
            return
        if self.state == "recording":
            self._send("pause")
        elif self.state in ("connected", "paused"):
            self.start()
        return "break"

    def _update_controls(self):
        busy = self.pending or self.closing or self.state in ("connecting", "verifying")
        disconnected = self.state == "disconnected"
        idle = self.state in ("connected", "paused")
        running = self.state == "recording"
        for widget, enabled in ((self.connect_button, disconnected), (self.disconnect_button, not disconnected),
                                (self.start_button, idle), (self.pause_button, running), (self.apply_button, idle or running),
                                (self.new_button, idle or disconnected), (self.load_button, disconnected),
                                (self.export_button, bool(self.recording.samples) and not running),
                                (self.load_config_button, disconnected)):
            widget.state(["!disabled"] if enabled and not busy else ["disabled"])
        for widget in self.network_widgets:
            widget.state(["!disabled"] if disconnected and not busy else ["disabled"])
        for widget in self.hardware_widgets:
            widget.state(["!disabled"] if not busy else ["disabled"])
        self.start_button.configure(text="Resume recording" if self.state == "paused" else "Start recording")
        self.apply_button.configure(text="Apply + restart segment" if running else "Apply settings")
        labels = {"disconnected": "Disconnected", "connecting": "Connecting…", "connected": "Connected · ready",
                  "recording": "● Recording · provisional", "verifying": "Stopping + verifying…", "paused": "Paused · saved"}
        self.status.set("Closing · stopping and saving…" if self.closing else labels.get(self.state, self.state))

    def _reset_history(self):
        self.recording, self.metadata, self.analysis = Recording(), {}, {}
        self.epoch += 1
        self.viewport.latest, self.viewport.start = 0, 0
        self.viewport.live()
        self.analysis_key, self.last_analysis, self.dirty = None, 0.0, True

    def _event(self, kind, payload):
        if kind == "state":
            self.state = payload
            if payload not in ("connecting", "verifying"):
                self.pending = False
            self._update_controls()
        elif kind == "device":
            self.device = payload
            self.device_text.set(f"{payload['serial']}\n{payload['ip']} · firmware {payload['firmware']}")
            self._log(f"Connected: {payload['serial']} at {payload['ip']}.")
            self.notice.set("Connected. Match the manual gain and filters to the physical amplifier before recording.")
        elif kind == "session":
            self._reset_history()
            self.metadata = {"device": self.device.copy(), "simulated": self.demo}
            self.source_text.set(f"Autosave: {payload}")
            self._log(f"Autosaving CSV + raw ADC + segment metadata + UDP trace: {payload}")
        elif kind == "reset":
            self._reset_history()
            self.source_text.set("New session — next acquisition creates a new autosave file")
        elif kind == "segment":
            self.recording.begin(payload)
            self._log(f"Segment {payload.number + 1}: {get_profile(payload.settings.profile).name}, "
                      f"ch {payload.settings.channel}, {payload.settings.rate} Hz, "
                      f"{payload.settings.sensitivity:g} µS/V, {' / '.join(payload.settings.filters)} ({payload.reason}).")
        elif kind == "samples":
            self.recording.append(*payload)
            self.viewport.update(self.recording.times[-1])
            self.dirty = True
        elif kind == "finished":
            self.recording.finish(*payload)
            self.dirty = True
        elif kind == "applied":
            self.applied = payload
            self._settings_changed()
        elif kind == "log":
            self._log(payload)
        elif kind == "error":
            self._log(f"ERROR: {payload}")
            self.notice.set(f"ERROR: {payload} — if the network failed, check that the MP160 has stopped.")
        elif kind == "closed":
            self.closed = True
            if self.new_executor_api:
                self.executor.shutdown(wait=False, cancel_futures=True)
            else:
                self.executor.shutdown(wait=False)
            self.root.destroy()

    def _pump(self):
        self.pump_job = None
        # Bounded draining prevents a backlog from monopolizing the Tk event loop.
        for _ in range(150):
            try:
                kind, payload = self.worker.events.get_nowait()
            except queue.Empty:
                break
            self._event(kind, payload)
            if self.closed:
                return
        if self.analysis_future is not None and self.analysis_future[1].done():
            key, future = self.analysis_future
            self.analysis_future = None
            try:
                result = future.result()
                if key[:2] == (self.epoch, self.processing):
                    self.analysis, self.analysis_key, self.dirty = result, key, True
            except Exception as error:
                self._log(f"Analysis failed (raw acquisition unaffected): {error}")
        now = time.monotonic()
        if not self.closing and self.dirty and now - self.last_render >= 0.2:
            self._render()
            self.last_render, self.dirty = now, False
        if not self.closing and self.analysis_future is None and self.recording.samples and now - self.last_analysis >= 1:
            self._request_analysis()
        self.pump_job = self.root.after(40, self._pump)

    def _request_analysis(self):
        times = self.recording.times
        first = max(0, bisect.bisect_left(times, self.viewport.start - 40) - self.processing.average)
        last = bisect.bisect_right(times, self.viewport.start + self.viewport.span + 40)
        key = self.epoch, self.processing, first, last
        self.last_analysis = time.monotonic()
        if key == self.analysis_key:
            return
        samples = self.recording.samples[first:last]
        self.analysis_future = key, self.executor.submit(analyze_recording, samples, **asdict(self.processing))

    def _render(self):
        samples, times = self.recording.samples, self.recording.times
        first = max(0, bisect.bisect_left(times, self.viewport.start) - self.processing.average - 1)
        last = min(len(samples), bisect.bisect_right(times, self.viewport.start + self.viewport.span) + 1)
        selected = samples[first:last]
        selected_times = times[first:last]
        volts, conductance, identities = moving_average(selected, self.processing.average)
        raw_v, raw_g = [sample.voltage for sample in selected], [sample.conductance for sample in selected]
        invalid = [(sample.time_s, sample.time_s + 1 / sample.segment.settings.rate) for sample in selected if sample.clipped]
        invalid.extend((s.start_s, s.start_s + s.samples / s.settings.rate) for s in self.recording.segments.values() if s.error)
        markers = self.recording.markers()
        voltage_traces = [Trace(selected_times, raw_v, "#526c78" if self.processing.average > 1 else UI_ACCENT,
                                "raw signed ADC" if self.processing.average == 1 else "raw", segments=identities)]
        conductance_traces = [Trace(selected_times, raw_g, "#526c78" if self.processing.average > 1 else "#f2bd62",
                                    "nominal µS" if self.processing.average == 1 else "raw", segments=identities)]
        if self.processing.average > 1:
            voltage_traces.append(Trace(selected_times, volts, UI_ACCENT, f"MA {self.processing.average}", segments=identities))
            conductance_traces.append(Trace(selected_times, conductance, "#f2bd62", f"MA {self.processing.average}", segments=identities))
        self.voltage_plot.set_data(voltage_traces, markers, invalid)
        self.conductance_plot.set_data(conductance_traces, markers, invalid)
        analysis = self.analysis
        self.analysis_plot.set_data([
            Trace(analysis.get("times", []), analysis.get("tonic", []), "#6edba4", "tonic (left)", segments=analysis.get("segments")),
            Trace(analysis.get("times", []), analysis.get("phasic", []), "#8eafff", "phasic (right)", right=True, segments=analysis.get("segments")),
        ], markers, invalid, analysis.get("peaks", []))
        self.time_scroll.set(*self.viewport.fractions())
        self.view_status.set(f"{self.viewport.span:g} s window · {'LIVE' if self.viewport.follow else 'HISTORY'}")
        if samples:
            cursor = self.viewport.cursor
            index = len(samples) - 1 if cursor is None else min(len(samples) - 1, bisect.bisect_left(times, cursor))
            if cursor is not None and index and abs(times[index - 1] - cursor) < abs(times[index] - cursor):
                index -= 1
            sample = samples[index]
            suffix = " · ADC CLIPPED" if sample.clipped else " · INVALID SEGMENT" if sample.segment.error else ""
            self.readout.set(f"t {sample.time_s:.3f} s   |   {sample.voltage:.6f} V   |   {sample.conductance:.5f} µS   |   ch {sample.segment.settings.channel}{suffix}")
            verified = sum(segment.samples for segment in self.recording.segments.values() if segment.verified)
            failed = sum(segment.samples for segment in self.recording.segments.values() if segment.error)
            self.history_status.set(f"{len(samples):,} samples · {len(self.recording.segments)} segments · "
                                    f"{verified:,} {'simulated / finalized' if self.metadata.get('simulated') else 'verified'} · "
                                    f"{len(samples) - verified - failed:,} provisional · {failed:,} invalid")
        else:
            self.readout.set("No samples")
            self.history_status.set("0 samples · signed nominal conversion · no baseline subtraction")
        self._update_controls()

    def _navigate(self):
        self.dirty = True
        self.last_analysis = 0.0

    def follow(self):
        self.viewport.live()
        self._navigate()

    def _zoom(self, factor):
        self.viewport.zoom(factor)
        self._navigate()

    def _scroll(self, action, value, *_args):
        if action == "moveto":
            self.viewport.moveto(float(value))
        else:
            self.viewport.scroll(float(value))
        self._navigate()

    def load_csv(self, path=None):
        if self.state != "disconnected" or self.pending:
            return
        path = path or filedialog.askopenfilename(parent=self.root, title="Load BIOPAC recording", filetypes=[("CSV", "*.csv"), ("All files", "*")])
        if not path:
            return
        try:
            recording, metadata = load_recording(path)
        except (OSError, ValueError, TypeError) as error:
            self.notice.set(f"Load failed: {error}")
            return
        self._reset_history()
        self.recording, self.metadata = recording, metadata
        self.viewport.update(recording.times[-1])
        self.source_text.set(f"Loaded: {path}")
        self.notice.set("Loaded a saved recording. Hardware controls are for future acquisition; imported sample calibration is unchanged.")
        self._log(f"Loaded {len(recording.samples):,} samples from {path}.")

    def export_csv(self, path=None):
        if not self.recording.samples or self.state in ("recording", "verifying") or self.pending:
            return
        path = path or filedialog.asksaveasfilename(parent=self.root, title="Export original samples + segment settings", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            export_recording(path, self.recording, asdict(self.processing), self.metadata)
        except (OSError, ValueError) as error:
            self.notice.set(f"Export failed: {error}")
            return
        self._log(f"Exported original samples and per-segment calibration to {path}. Processing settings are in the JSON sidecar.")

    def save_config(self, path=None):
        path = path or filedialog.asksaveasfilename(parent=self.root, title="Save BIOPAC configuration", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            write_configuration(path, self._configuration())
        except (OSError, ValueError, TypeError) as error:
            self.notice.set(f"Configuration not saved: {error}")
            return
        self._log(f"Saved configuration: {path}")

    def load_config(self, path=None):
        if self.state != "disconnected" or self.pending:
            return
        path = path or filedialog.askopenfilename(parent=self.root, title="Load BIOPAC configuration", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            configuration = read_configuration(path)
        except (OSError, ValueError, TypeError) as error:
            self.notice.set(f"Configuration not loaded: {error}")
            return
        self._set_configuration(configuration)
        self.notice.set("Configuration loaded. Manual gain/filters still need to match the physical amplifier.")

    def close(self):
        if self.closing:
            return
        self.closing = True
        if self.processing_job is not None:
            self.root.after_cancel(self.processing_job)
            self.processing_job = None
        try:
            write_configuration(self.config_path, self._configuration())
        except (OSError, ValueError, TypeError) as error:
            self._log(f"Last settings not saved: {error}")
        self.notice.set("Closing: waiting for the acquisition worker to stop, verify and flush the recording. Do not unplug the MP160 yet.")
        self._update_controls()
        self.worker.submit("close")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Explicitly display simulated data; never accesses the device")
    parser.add_argument("--interface", help="Dedicated MP160 Ethernet interface")
    parser.add_argument("--ip", help="Known MP160 IPv4 address (otherwise discover)")
    parser.add_argument("--output-dir", help="Directory for automatically saved recordings")
    parser.add_argument("--config", type=Path, default=default_path(), help="Last-used GUI settings (JSON)")
    parser.add_argument("--profile", choices=tuple(PROFILES),
                        help="Load hardware preset defaults, overriding saved acquisition settings (including zero)")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux") and not args.demo:
        parser.error("Native MP160 acquisition currently supports Linux only")
    warning = None
    try:
        configuration = read_configuration(args.config) if args.config.exists() else Configuration()
    except (OSError, ValueError, TypeError) as error:
        warning = f"Could not load {args.config}: {error}. Using defaults; check settings before recording."
        configuration = Configuration()
    values = configuration.mapping()
    if args.profile is not None:
        values["acquisition"] = Settings.from_profile(args.profile).mapping()
    for name in ("interface", "ip", "output_dir"):
        if getattr(args, name) is not None:
            values[name] = getattr(args, name)
    try:
        configuration = Configuration.parse(values)
    except (ValueError, TypeError) as error:
        parser.error(str(error))
    try:
        root = tk.Tk()
    except tk.TclError as error:
        parser.exit(1, f"Could not open a desktop display: {error}\nInstall python3-tk and run in a graphical Linux session.\n")
    app = BiopacGUI(root, configuration, config_path=args.config, demo=args.demo)
    if warning:
        app._log(warning)
        app.notice.set(warning)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _sig, _frame: app.close())
    root.mainloop()
    app.worker.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
