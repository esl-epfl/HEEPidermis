#!/usr/bin/env python3
"""Shimmer3 GSR+ live monitor for the HEEPidermis workspace.

Connection is fully automatic: dock UART first, then a Bluetooth scan with
pairing (legacy PIN) and RFCOMM binding.  Privilege escalation for the bind
uses a desktop polkit popup; only if that fails does a dialog show the exact
command, with an Accept button to continue once it has been run.
"""

from __future__ import annotations

import argparse
import bisect
from dataclasses import asdict, replace
from datetime import datetime
from functools import partial
from pathlib import Path
import queue
import signal
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace

try:
    from ...common.background import submit
    from ...common.recording import rename_recording
    from ...common.theme import (UI_ACCENT, UI_BACKGROUND, UI_BORDER, UI_MUTED,
                                 UI_PLOT, UI_TEXT, configure_style)
    from ...common.plots import TimePlot, TimeViewport, Trace
    from ...common.widgets import Tooltip, submenu
    from .acquire import AcquisitionError, RATES, DEFAULT_RATE
    from .configuration import (CONNECTION_LABELS, CONNECTION_MODES, CONNECTION_VALUES,
                                Configuration, default_path, read_configuration,
                                write_configuration)
    from .controller import AcquisitionWorker
    from .recording import (CSV_COLUMNS, Recording, Settings, csv_row, export_recording,
                            load_recording, moving_average)
except ImportError as error:
    raise SystemExit("GUI dependencies missing. Run: python3 -m pip install -r sw/gui/requirements.txt\n"
                     f"{error}") from error


RATE_CHOICES = tuple(f"{rate:g}" for rate in RATES)


class ShimmerGUI:
    """Tk owns history and widgets; the acquisition thread alone owns the device."""

    def __init__(self, root, configuration=None, *, config_path=None, demo=False, worker=None,
                 host=None, reports=None, on_closed=None):
        self.root, self.demo = root, demo
        self.host, self.reports, self.on_closed = host, reports, on_closed
        self.embedded = host is not None
        self.config_path = Path(config_path or default_path())
        self.configuration = configuration or Configuration()
        self.recording, self.metadata = Recording(), {}
        self.viewport = TimeViewport(span=self.configuration.span)
        self.applied = None
        self.device = {}
        self.session_path = None
        self.save_future = None
        self.state, self.pending, self.closing, self.closed = "disconnected", False, False, False
        self.dirty, self.epoch = True, 0
        self.worker = worker or AcquisitionWorker(demo=demo)
        self.connect_labels = ("Connect", "Disconnect")
        configure_style(root)
        if not self.embedded:
            root.title("Shimmer · GSR+ live monitor" + (" — DEMO / SIMULATED" if demo else ""))
            width, height = min(1540, root.winfo_screenwidth() - 60), min(1000, root.winfo_screenheight() - 80)
            root.geometry(f"{max(1050, width)}x{max(720, height)}")
            root.minsize(1050, 720)
        self._build()
        self._set_configuration(self.configuration)
        self._update_controls()
        if not self.embedded:
            root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker.start()
        self.pump_job = root.after(40, self._pump)
        self._log("DEMO — simulated data only." if demo else
                  "Connection is automatic: the Shimmer dock UART is tried first, then a "
                  "Bluetooth scan (pairing uses the Shimmer PIN 1234 and an RFCOMM bind).")

    # ------------------------------------------------------------ UI build

    def _build(self):
        parent = self.host or self.root
        # The workspace panels (HEEPidermis, BIOPAC) show no embedded header;
        # keep the title bar for the standalone window only.
        self.status = tk.StringVar(value="Disconnected")
        if not self.embedded:
            header = ttk.Frame(parent, style="App.TFrame", padding=(18, 12))
            header.pack(fill="x")
            ttk.Label(header, text="Shimmer", style="PlotTitle.TLabel",
                      font=("Helvetica", 19, "bold")).pack(side="left")
            ttk.Label(header, text="Shimmer3 GSR+ · skin conductance · Bluetooth SPP / dock",
                      style="WorkspaceMuted.TLabel").pack(side="left", padx=18)
            ttk.Label(header, textvariable=self.status, style="Workspace.TLabel").pack(side="right")

        body = ttk.Frame(parent, style="App.TFrame", padding=(12, 0, 12, 10))
        body.pack(fill="both", expand=True)
        if self.embedded:
            sidebar = ttk.Frame(self.host, style="App.TFrame")
            sidebar.pack(fill="x")
        else:
            sidebar_host = ttk.Frame(body)
            sidebar_host.pack(side="left", fill="y", padx=(0, 12))
            canvas = tk.Canvas(sidebar_host, width=334, background=UI_BACKGROUND, highlightthickness=0)
            scroll = ttk.Scrollbar(sidebar_host, orient="vertical", command=canvas.yview)
            scroll.pack(side="right", fill="y")
            canvas.pack(fill="both", expand=True)
            sidebar = ttk.Frame(canvas, style="App.TFrame")
            window = canvas.create_window(0, 0, window=sidebar, anchor="nw")
            sidebar.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

        link = self._section(sidebar, "Connection")
        self.address, self.name, self.output_dir = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.connection_variable = tk.StringVar()
        ttk.Label(link, text="Connection").grid(row=0, column=0, sticky="w")
        connection_box = ttk.Combobox(link, textvariable=self.connection_variable, state="readonly",
                                      values=tuple(CONNECTION_LABELS[key] for key in CONNECTION_MODES))
        connection_box.grid(row=1, column=0, sticky="ew", pady=(3, 7))
        connection_box.set(CONNECTION_LABELS["auto"])
        Tooltip(connection_box, "Transport used to reach the Shimmer. 'Auto' probes the USB dock "
                                "first and then scans Bluetooth. Choose one transport to skip "
                                "the other entirely. Applied on Connect.")
        address_field = self._field(link, "Device (blank = discover any Shimmer)", self.address, 1)
        Tooltip(address_field, "Bluetooth address (00:06:66:…) or dock serial path. Leave blank to "
                               "scan on the selected transport.")
        ttk.Label(link, text="Discovered device", style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        self.device_text = tk.StringVar(value="No device connected")
        ttk.Label(link, textvariable=self.device_text, style="Muted.TLabel",
                  wraplength=298).grid(row=5, column=0, sticky="w", pady=(0, 6))
        connection_buttons = ttk.Frame(link)
        connection_buttons.grid(row=6, column=0, sticky="ew", pady=(2, 2))
        self.connect_button = ttk.Button(connection_buttons, text="Connect",
                                         command=self.connect, style="Accent.TButton")
        self.connect_button.pack(side="left", fill="x", expand=True)
        self.disconnect_button = ttk.Button(connection_buttons, text="Disconnect",
                                            command=lambda: self._send("disconnect"))
        self.disconnect_button.pack(side="left", fill="x", expand=True, padx=(6, 0))
        ttk.Label(link, text="Pairing and the Bluetooth serial bind are handled for you. "
                             "If Linux asks for administrator rights once, approve the popup.",
                  style="Muted.TLabel", wraplength=298).grid(row=7, column=0, sticky="w", pady=(6, 0))

        hardware = self._section(sidebar, "Acquisition")
        self.rate_variables = {"rate": tk.StringVar(), "output_rate": tk.StringVar()}
        self.moving_average_variable = tk.StringVar()
        pair = ttk.Frame(hardware)
        pair.grid(row=0, column=0, sticky="ew")
        pair.columnconfigure((0, 1), weight=1)
        self.rate_widgets = []
        for column, (key, label) in enumerate((("rate", "Device rate (Hz)"),
                                               ("output_rate", "Output rate (Hz)"))):
            ttk.Label(pair, text=label).grid(row=0, column=column, sticky="w")
            widget = ttk.Combobox(pair, textvariable=self.rate_variables[key], values=RATE_CHOICES,
                                  width=10, state="readonly")
            widget.grid(row=1, column=column, sticky="ew", padx=(0, 6) if column == 0 else (0, 0), pady=(3, 7))
            self.rate_widgets.append(widget)
        Tooltip(self.rate_widgets[0], "Shimmer3 GSR+ rates: the 32768 Hz clock divided by an integer. "
                                      "EDA needs little bandwidth; 10–52 Hz is plenty. Applied on Start/Apply.")
        Tooltip(self.rate_widgets[1], "Samples kept in the CSV and plots. Decimation is evenly spaced "
                                      "without an anti-alias filter.")
        processing = ttk.Frame(hardware)
        processing.grid(row=1, column=0, sticky="ew")
        processing.columnconfigure(0, weight=1)
        ttk.Label(processing, text="Moving average (samples)").grid(row=0, column=0, sticky="w")
        spin = ttk.Spinbox(processing, from_=1, to=10001, width=7,
                           textvariable=self.moving_average_variable,
                           command=self._display_changed)
        spin.grid(row=1, column=0, sticky="ew", pady=(3, 7))
        spin.bind("<Return>", lambda _e: self._display_changed())
        spin.bind("<FocusOut>", lambda _e: self._display_changed())
        Tooltip(spin, "Number of conductance samples averaged for the displayed and exported "
                      "signal, like the HEEPidermis panel. 1 = raw. Applies immediately, "
                      "no device change.")
        self.apply_button = ttk.Button(hardware, text="Apply settings", command=self.apply_settings)
        self.apply_button.grid(row=2, column=0, sticky="ew")
        self.settings_status = tk.StringVar()
        ttk.Label(hardware, textvariable=self.settings_status, style="Muted.TLabel",
                  wraplength=298).grid(row=3, column=0, sticky="w", pady=(6, 0))

        files = ttk.Frame(sidebar) if self.embedded else self._section(sidebar, "Files")
        ttk.Label(files, text="Autosave folder (next connection)", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(files, textvariable=self.output_dir).pack(fill="x", pady=(3, 6))
        file_buttons = ttk.Frame(files)
        file_buttons.pack(fill="x")
        self.load_button = ttk.Button(file_buttons, text="Load CSV…", command=self.load_csv)
        self.load_button.pack(side="left", expand=True, fill="x")
        self.export_button = ttk.Button(file_buttons, text="Save CSV…", command=self.export_csv)
        self.export_button.pack(side="left", expand=True, fill="x", padx=(5, 0))

        workspace = ttk.Frame(body, style="Workspace.TFrame")
        workspace.pack(side="left", fill="both", expand=True)
        toolbar = ttk.Frame(parent if self.embedded else workspace, style="Workspace.TFrame")
        toolbar.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(toolbar, text="Start recording", command=self.start, style="Accent.TButton")
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(toolbar, text="Pause + verify", command=lambda: self._send("pause"))
        self.pause_button.pack(side="left", padx=6)
        self.new_button = ttk.Button(toolbar, text="New session", command=self.new_session)
        self.new_button.pack(side="left")
        ttk.Label(toolbar, text="REAL DEVICE · GSR" if not self.demo else "DEMO · SIMULATED DATA",
                  style="WorkspaceMuted.TLabel").pack(side="right")
        if self.embedded:
            toolbar.pack_forget()  # The workspace's shared transport drives this panel.
        status_host = workspace if not self.embedded else self.reports
        self.notice = tk.StringVar(value="Press Connect — no terminal commands are needed.")
        ttk.Label(status_host, textvariable=self.notice, wraplength=1040,
                  style="WorkspaceMuted.TLabel").pack(fill="x", pady=(0, 5))
        self.readout = tk.StringVar(value="No samples")
        ttk.Label(status_host, textvariable=self.readout, style="Workspace.TLabel",
                  font=("Helvetica", 11, "bold")).pack(fill="x", pady=(2, 5))
        self.history_status = tk.StringVar(value="0 samples")
        ttk.Label(status_host, textvariable=self.history_status, style="WorkspaceMuted.TLabel").pack(fill="x", pady=(0, 8))
        if self.embedded:
            self.log_host, self.conductance_plot, self.time_scroll = self.reports, None, None
            self.view_status = tk.StringVar()
        else:
            self.log_host = workspace
            plots = ttk.Frame(workspace, style="Workspace.TFrame")
            plots.pack(fill="both", expand=True)
            plots.columnconfigure(0, weight=1)
            plots.rowconfigure(0, weight=1)
            self.conductance_plot = TimePlot(plots, self.viewport, "01  Skin conductance", "µS",
                                             self._navigate, height=300)
            self.conductance_plot.grid(row=0, column=0, sticky="nsew")
            self.time_scroll = ttk.Scrollbar(workspace, orient="horizontal", command=self._scroll)
            self.time_scroll.pack(fill="x")
        self.source_text = tk.StringVar(value="No recording — data is autosaved when acquisition starts")
        ttk.Label(status_host, textvariable=self.source_text, style="WorkspaceMuted.TLabel",
                  wraplength=1040).pack(fill="x", pady=(0, 5))
        self.log = tk.Text(self.log_host, height=4, background=UI_PLOT, foreground=UI_MUTED,
                           insertbackground=UI_TEXT, relief="flat", highlightthickness=1,
                           highlightbackground=UI_BORDER, font=("Helvetica", 9), state="disabled")
        self.log.pack(fill="both", expand=True)

    @staticmethod
    def _section(parent, text):
        # Shared nested-section look, defined once in common.widgets so every
        # device menu is built from the same code.
        return submenu(parent, text)

    @staticmethod
    def _field(parent, label, variable, row):
        ttk.Label(parent, text=label).grid(row=2 * row, column=0, sticky="w")
        widget = ttk.Entry(parent, textvariable=variable)
        widget.grid(row=2 * row + 1, column=0, sticky="ew", pady=(3, 7))
        return widget

    # ----------------------------------------------------------- settings

    def _settings(self):
        rate = float(self.rate_variables["rate"].get() or DEFAULT_RATE)
        output = float(self.rate_variables["output_rate"].get() or rate)
        return Settings.parse({"rate": rate, "output_rate": None if output == rate else output,
                               "device_name": self.name.get(), "address": self.address.get()})

    def _configuration(self):
        return Configuration.parse({"address": self.address.get(), "name": self.name.get(),
                                    "connection": self.connection_value(),
                                    "output_dir": self.output_dir.get(),
                                    "acquisition": self._settings().mapping(),
                                    "moving_average": self.moving_average_value(),
                                    "span": self.viewport.span})

    def connection_value(self):
        """Config key ('auto'/'dock'/'bluetooth') for the selected transport."""
        return CONNECTION_VALUES.get(self.connection_variable.get(), "auto")

    def moving_average_value(self):
        try:
            window = int(self.moving_average_variable.get())
        except (TypeError, ValueError):
            return 1
        return min(10001, max(1, window))

    def _display_changed(self):
        """The moving-average window changed: re-smooth the shown series."""
        self.moving_average_variable.set(str(self.moving_average_value()))
        self.dirty = True

    def _set_configuration(self, configuration):
        self.address.set(configuration.address or "")
        self.name.set(configuration.name or "")
        self.connection_variable.set(CONNECTION_LABELS.get(configuration.connection,
                                                           CONNECTION_LABELS["auto"]))
        self.output_dir.set(configuration.output_dir)
        settings = configuration.acquisition
        self.rate_variables["rate"].set(f"{settings.rate:g}")
        self.rate_variables["output_rate"].set(f"{settings.sample_rate:g}")
        self.moving_average_variable.set(str(configuration.moving_average))
        self.viewport.span = configuration.span
        self._settings_changed()

    def _settings_changed(self, *_args):
        try:
            requested = self._settings()
            if self.applied is None:
                text = "Not applied. Start uses the settings shown above."
            else:
                current = self.applied
                text = ("Applied" if requested == current else "PENDING CHANGES — Apply to switch")
                text += f": {current.sample_rate:g} Hz output / {current.rate:g} Hz device"
        except (ValueError, TypeError) as error:
            text = f"Pending settings are invalid: {error}"
        self.settings_status.set(text)

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

    # -------------------------------------------------------- connection UI

    def connect(self):
        try:
            configuration = self._configuration()
        except (ValueError, TypeError) as error:
            self.notice.set(str(error))
            return
        self.notice.set("Connecting… discovery, pairing and the serial bind are automatic.")
        self._send("connect", address=configuration.address, name=configuration.name,
                   output_dir=configuration.output_dir,
                   connection_mode=configuration.connection)

    def _show_chooser(self, payload):
        """Modal picker when discovery finds several Shimmers."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Select a Shimmer")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="Several Shimmer devices answered.\nPick the one to connect to:",
                  padding=10).pack()
        listbox = tk.Listbox(dialog, width=52, height=min(8, max(3, len(payload["candidates"]))),
                             exportselection=False)
        for candidate in payload["candidates"]:
            label = candidate.get("name") or candidate.get("address") or candidate.get("port", "?")
            detail = candidate.get("address") or candidate.get("port", "")
            listbox.insert("end", f"{label}   {detail}")
        listbox.pack(fill="both", expand=True, padx=10)
        listbox.selection_set(0)

        def finish(accepted):
            selection = listbox.curselection()
            if accepted and selection:
                payload["done"]["choice"] = payload["candidates"][selection[0]]
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))

        buttons = ttk.Frame(dialog, padding=10)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Connect", command=lambda: finish(True), style="Accent.TButton").pack(side="left", expand=True)
        ttk.Button(buttons, text="Cancel", command=lambda: finish(False)).pack(side="left", expand=True, padx=(6, 0))
        dialog.wait_window()
        payload["event"].set()

    def _show_privilege(self, payload):
        """Explain the one command that could not be run automatically."""
        command = payload["command"]
        accepted = messagebox.askokcancel(
            "Administrator rights needed",
            "Binding the Bluetooth serial port needs administrator rights and the automatic "
            "attempts did not succeed.\n\n"
            "Option A — approve the authorization popup if it is still open.\n\n"
            f"Option B — run this once in a terminal:\n\n  sudo {command}\n\n"
            "Then press Accept to continue with the connection.",
            parent=self.root)
        payload["done"]["accepted"] = bool(accepted)
        payload["event"].set()

    # ---------------------------------------------------------- transport

    def start(self):
        try:
            settings = self._settings()
        except (ValueError, TypeError) as error:
            self.notice.set(str(error))
            return
        if not self.embedded:
            self.follow()
        self.notice.set("Recording. Wear the GSR electrodes on two fingers of one hand.")
        self._send("start", settings=settings)

    def apply_settings(self):
        try:
            settings = self._settings()
        except (ValueError, TypeError) as error:
            self.notice.set(str(error))
            return
        if self.state == "recording" and not messagebox.askokcancel(
                "Apply to a new segment",
                "This stops and verifies the current segment, then starts another with the new settings.",
                parent=self.root):
            return
        self._send("apply", settings=settings)

    def new_session(self):
        if self.recording.samples and not messagebox.askokcancel(
                "New session", "Start a new recording? The next Start replaces last_output.csv. "
                "Save CSV first to keep this recording under another name.", parent=self.root):
            return
        self._send("new")

    def transport_state(self) -> str:
        if self.state in ("connecting", "verifying") or self.pending or self.closing:
            return "busy"
        return "running" if self.state == "recording" else "stopped"

    def transport(self, action: str) -> None:
        if action == "start":
            if self.state in ("connected", "paused"):
                self.start()
        elif action in ("pause", "stop"):
            if self.state == "recording":
                self._send(action)
            elif action == "stop" and self.state == "paused":
                self._send("stop")

    def connection(self):
        """(level, message) for the workspace status circle: ok/warning/off."""
        if self.closing:
            return "warning", "Closing…"
        if self.state == "disconnected":
            return "off", "Disconnected"
        if self.state == "connecting":
            return "warning", "Connecting…"
        if self.state == "verifying":
            return "warning", "Verifying segment…"
        if self.state == "recording":
            return "ok", "Recording"
        if self.state == "paused":
            return "ok", "Paused · segment verified"
        return "ok", "Connected · ready"

    def connect_toggle(self) -> None:
        if self.state == "disconnected":
            self.connect()
        else:
            self._send("disconnect")

    def _update_controls(self):
        busy = self.pending or self.closing or self.state in ("connecting", "verifying")
        disconnected = self.state == "disconnected"
        idle = self.state in ("connected", "paused")
        running = self.state == "recording"
        for widget, enabled in ((self.connect_button, disconnected), (self.disconnect_button, not disconnected),
                                (self.start_button, idle), (self.pause_button, running),
                                (self.apply_button, idle or running), (self.new_button, idle or disconnected),
                                (self.load_button, disconnected), (self.export_button, bool(self.recording.samples))):
            widget.state(["!disabled"] if enabled and not busy else ["disabled"])
        for widget in self.rate_widgets:
            widget.state(["!disabled"] if idle or running else ["disabled"])
        self.start_button.configure(text="Resume recording" if self.state == "paused" else "Start recording")
        labels = {"disconnected": "Disconnected", "connecting": "Connecting…",
                  "connected": "Connected · ready", "recording": "● Recording · provisional",
                  "verifying": "Stopping + verifying…", "paused": "Paused · saved"}
        self.status.set("Closing…" if self.closing else labels.get(self.state, self.state))

    # ------------------------------------------------------------- events

    def _reset_history(self):
        self.clock_origin = None
        self.recording, self.metadata = Recording(), {}
        self.epoch += 1
        self.viewport.latest, self.viewport.start = 0, 0
        self.viewport.live()
        self.dirty = True

    def _event(self, kind, payload):
        if kind == "state":
            self.state = payload
            if payload not in ("connecting", "verifying"):
                self.pending = False
            self._update_controls()
        elif kind == "device":
            self.device = payload
            self.name.set(payload.get("name", ""))
            detail = {"bluetooth": payload.get("address", ""), "dock": payload.get("path", ""),
                      "simulated": "demo"}.get(payload.get("transport"), payload.get("path", ""))
            self.device_text.set(f"{payload.get('name', 'Shimmer')} · {payload.get('firmware', '')} "
                                 f"{payload.get('firmware_version', '')}\n{detail} · battery "
                                 f"{payload.get('battery_percent', '?')}%")
            self._log(f"Connected: {payload.get('name')} via {payload.get('transport')}.")
            self.notice.set("Connected. Wear the GSR electrodes; open-circuit samples read OPEN.")
        elif kind == "choose":
            self._show_chooser(payload)
        elif kind == "privilege":
            self._show_privilege(payload)
        elif kind == "session":
            self.session_path = Path(payload)
            self._reset_history()
            self.metadata = {"device": self.device.copy(), "simulated": self.demo}
            self.source_text.set(f"Autosave: {payload}")
            self._log(f"Autosaving CSV + metadata: {payload}")
        elif kind == "reset":
            self.session_path = None
            self._reset_history()
            self.source_text.set("New session — next Start replaces last_output.csv")
        elif kind == "segment":
            self.recording.begin(payload)
            self._log(f"Segment {payload.number + 1}: {payload.settings.sample_rate:g} Hz "
                      f"({payload.settings.rate:g} Hz device) ({payload.reason}).")
        elif kind == "timebase":
            self.clock_origin = float(payload)
        elif kind == "samples":
            try:
                self.recording.append(*payload)
            except ValueError as error:
                self.notice.set(f"{error} — reconnect the device if this repeats.")
            self.viewport.update(self.recording.times[-1] if self.recording.times else 0.0)
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
            self.notice.set(f"ERROR: {payload}")
        elif kind == "saved":
            self.session_path = Path(payload)
            self.source_text.set(f"Recording: {payload}")
            self.notice.set(f"CSV renamed to {payload}")
            self._log(f"CSV and metadata renamed to {payload}")
        elif kind == "save_error":
            self.notice.set(f"CSV not renamed: {payload}")
        elif kind == "closed":
            self.closed = True
            if self.on_closed is not None:
                self.on_closed()
            elif not self.embedded:
                self.root.destroy()

    def _pump(self):
        self.pump_job = None
        try:
            self._pump_once()
        except Exception as error:
            self.notice.set(f"Display update failed (acquisition continues): {error}")
            self._log(f"Display update failed: {error}")
        finally:
            if not self.closed:
                self.pump_job = self.root.after(40, self._pump)

    def _pump_once(self):
        if self.save_future is not None and self.save_future.done():
            future, self.save_future = self.save_future, None
            try:
                self._event("saved", str(future.result()))
            except OSError as error:
                self._event("save_error", str(error))
        deadline = time.monotonic() + 0.008
        for _ in range(400):
            if time.monotonic() >= deadline:
                break
            try:
                kind, payload = self.worker.events.get_nowait()
            except queue.Empty:
                break
            self._event(kind, payload)
            if self.closed:
                return
        if not self.closing and self.dirty and time.monotonic() - getattr(self, "last_render", 0.0) >= 0.2:
            self._render()
            self.last_render, self.dirty = time.monotonic(), False

    def _render(self):
        samples, times = self.recording.samples, self.recording.times
        if not self.embedded:
            first = max(0, bisect.bisect_left(times, self.viewport.start) - 1) if times else 0
            last = min(len(samples), bisect.bisect_right(times, self.viewport.start + self.viewport.span) + 1)
            selected, selected_times = samples[first:last], times[first:last]
            window = self.moving_average_value()
            smoothed, _ = moving_average(selected, window) if window > 1 else (None, None)
            raw = [sample.conductance for sample in selected]
            traces = [Trace(selected_times, raw, UI_ACCENT, "raw")]
            if smoothed is not None:
                traces.append(Trace(selected_times, smoothed, "#f2bd62", f"MA {window}"))
            self.conductance_plot.set_data(
                traces,
                self.recording.markers(), self.recording.invalid_spans())
            self.time_scroll.set(*self.viewport.fractions())
        if samples:
            index = len(samples) - 1
            sample = samples[index]
            value = sample.conductance
            suffix = " · OPEN CIRCUIT" if sample.open_circuit else ""
            self.readout.set(f"t {sample.time_s:.3f} s   |   "
                             f"{'OPEN' if value is None else f'{value:.3f} µS'}   |   "
                             f"range {sample.gsr_range} · adc {sample.adc}{suffix}")
            self.history_status.set(f"{len(samples):,} samples · "
                                    f"{sum(1 for s in samples if s.open_circuit):,} open-circuit · "
                                    f"{len(self.recording.segments)} segments")
        else:
            self.readout.set("No samples")
            self.history_status.set("0 samples")
        self._update_controls()

    def _navigate(self):
        self.dirty = True

    def follow(self):
        self.viewport.live()
        self._navigate()

    def _scroll(self, action, value, *_args):
        if action == "moveto":
            self.viewport.moveto(float(value))
        else:
            self.viewport.scroll(float(value))
        self._navigate()

    # ------------------------------------------------------------- files

    def load_csv(self, path=None):
        if self.state != "disconnected" or self.pending:
            return None
        path = path or filedialog.askopenfilename(parent=self.root, title="Load Shimmer recording",
                                                  filetypes=[("CSV", "*.csv"), ("All files", "*")])
        if not path:
            return None
        try:
            recording, metadata = load_recording(path)
        except (OSError, ValueError, TypeError) as error:
            self.notice.set(f"Load failed: {error}")
            return None
        self._reset_history()
        self.recording, self.metadata = recording, metadata
        self.session_path = None
        self.viewport.update(recording.times[-1])
        self.source_text.set(f"Loaded: {path}")
        self._log(f"Loaded {len(recording.samples):,} samples from {path}.")
        return path

    def clear_recording(self):
        self._reset_history()
        self.session_path = None
        self.source_text.set("No recording loaded")

    def recording_snapshot(self):
        samples, count = self.recording.samples, len(self.recording.samples)
        metadata = {**self.metadata, "segments": [asdict(segment)
                    for segment in self.recording.segments.values()]}
        return {"count": count, "columns": CSV_COLUMNS,
                "rows": lambda: (csv_row(index, samples[index]) for index in range(count)),
                "sidecars": {".json": metadata}}

    def export_csv(self, path=None):
        if not self.recording.samples or self.save_future is not None:
            return None
        path = path or filedialog.asksaveasfilename(parent=self.root, title="Save recording",
                                                    defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return None
        if self.session_path is not None:
            if self.state != "disconnected":
                self.worker.submit("save", path=path)
            else:
                self.save_future = submit(rename_recording, self.session_path, path, (".json",))
            self.notice.set("Renaming CSV and metadata…")
            return path
        try:
            export_recording(path, self.recording, self.metadata)
        except (OSError, ValueError) as error:
            self.notice.set(f"Export failed: {error}")
            return None
        self._log(f"Exported samples with per-sample range/ADC decode to {path}.")
        return path

    def save_recording(self):
        self.export_csv()

    # ------------------------------------------------------- configuration

    def configuration_snapshot(self):
        try:
            return self._configuration().mapping()
        except (ValueError, TypeError):
            return {}

    def apply_configuration(self, values):
        self._set_configuration(Configuration.parse(values))

    def set_decomposition(self, algorithm, smoothing, prominence):
        pass  # Decomposition is a workspace feature over the exported signal.

    def close(self):
        if self.closing:
            return
        self.closing = True
        try:
            write_configuration(self.config_path, self._configuration())
        except (OSError, ValueError, TypeError) as error:
            self._log(f"Last settings not saved: {error}")
        self.notice.set("Closing: stopping the stream and restoring the previous sensor configuration…")
        self._update_controls()
        self.worker.submit("close")

    # ------------------------------------------------ workspace plot API

    def plot_snapshot(self, requested=()):
        recording = SimpleNamespace(samples=self.recording.samples, times=self.recording.times,
                                    segments=self.recording.segments.copy())
        data = SimpleNamespace(recording=recording, count=len(recording.times),
                               window=self.moving_average_value())
        return SimpleNamespace(get_series=partial(ShimmerGUI.get_series, data),
                               invalid_spans=partial(ShimmerGUI.invalid_spans, data),
                               signals=lambda: ShimmerGUI.signals(None),
                               revision=(data.count, self.epoch),
                               clock_origin=getattr(self, "clock_origin", None),
                               latest=recording.times[-1] if recording.times else 0.0)

    @staticmethod
    def _static_series(data, low=None, high=None):
        samples, times = data.recording.samples, data.recording.times
        if not samples:
            return {}
        if low is None:
            selected, selected_times = samples, times
        else:
            first = max(0, bisect.bisect_left(times, low) - 1)
            last = len(samples) if high is None else min(len(samples), bisect.bisect_right(times, high) + 1)
            selected, selected_times = samples[first:last], times[first:last]
        conductance, _ = moving_average(selected, max(1, getattr(data, "window", 1)))
        identities = [sample.segment.number for sample in selected]
        return {"conductance_uS": {"times": selected_times, "values": conductance,
                                   "segments": identities}}

    def get_series(self, low=None, high=None) -> dict[str, dict]:
        """Signal series for the workspace plot engine: {name: trace data}.

        Called both live (self is the panel) and through plot_snapshot
        (self is the snapshot namespace carrying the frozen window value).
        """
        window = getattr(self, "window", None)
        if window is None:
            window = self.moving_average_value() if hasattr(self, "moving_average_value") else 1
        return ShimmerGUI._static_series(
            SimpleNamespace(recording=self.recording, count=len(self.recording.times),
                            window=window), low, high)

    def invalid_spans(self, low=None, high=None):
        samples, times = self.recording.samples, self.recording.times
        if low is not None and samples:
            first = max(0, bisect.bisect_left(times, low) - 1)
            last = len(samples) if high is None else min(len(samples), bisect.bisect_right(times, high) + 1)
            samples = samples[first:last]
        spans = [(sample.time_s, sample.time_s + 1 / sample.segment.settings.sample_rate)
                 for sample in samples if sample.open_circuit]
        # Inlined segment-error spans: this runs both on the live Recording
        # and on the SimpleNamespace built by plot_snapshot, which has no
        # invalid_spans() method of its own.
        spans.extend((segment.start_s, segment.start_s + segment.samples / segment.settings.rate)
                     for segment in self.recording.segments.values()
                     if segment.error and segment.samples)
        if low is None:
            return spans
        return [(max(start, low), end) for start, end in spans if end >= low
                and (high is None or start <= high)]

    def signals(self) -> dict[str, dict]:
        """Signal catalogue for the workspace signal-selection table."""
        return {
            "conductance_uS": {"label": "Skin conductance", "unit": "µS", "primary": True},
            "gsr_raw16": {"label": "GSR raw word", "unit": "counts"},
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Display simulated data; never accesses the device")
    parser.add_argument("--address", help="Bluetooth address or dock serial path (otherwise discover)")
    parser.add_argument("--output-dir", help="Directory for automatically saved recordings")
    parser.add_argument("--config", type=Path, default=default_path(), help="Last-used GUI settings (JSON)")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux") and not args.demo:
        parser.error("Shimmer acquisition currently supports Linux only")
    warning = None
    try:
        configuration = read_configuration(args.config) if args.config.exists() else Configuration()
    except (OSError, ValueError, TypeError) as error:
        warning = f"Could not load {args.config}: {error}. Using defaults."
        configuration = Configuration()
    values = configuration.mapping()
    for name in ("address", "output_dir"):
        if getattr(args, name):
            values[name] = getattr(args, name)
    try:
        configuration = Configuration.parse(values)
    except (ValueError, TypeError) as error:
        parser.error(str(error))
    try:
        root = tk.Tk()
    except tk.TclError as error:
        parser.exit(1, f"Could not open a desktop display: {error}\n")
    app = ShimmerGUI(root, configuration, config_path=args.config, demo=args.demo)
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
