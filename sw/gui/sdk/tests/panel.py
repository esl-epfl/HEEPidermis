"""Passive local test device; cognitive tests run in their own process/window.

The panel discovers the tests in ``sw/gui/tests/`` (see the AGENTS.md
standard there): a test folder with a ``spec.py`` provides its own
configuration form (sliders, dropdowns, checkboxes, free entries), which
this panel renders and turns into command-line flags for ``run_test.py``.
Folders without a spec are placeholders — they show in the dropdown but
report "No such test was found".
"""

import bisect
import dataclasses
import math
import os
from pathlib import Path
import queue
import shlex
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from types import SimpleNamespace

from gui.tests import available_tests
from gui.tests.common import settings

from .recording import COLUMNS, Event, EventTail, read_recording


ROOT = Path(__file__).resolve().parents[4]
RUNNER = ROOT / "sw/gui/tests/run_test.py"
DEFAULT_PATH = ROOT / "sw/gui/outs/tests/events.jsonl"


def validate_configuration(values):
    """Validate a saved Tests-panel configuration (raises ValueError)."""
    if not isinstance(values, dict):
        raise ValueError("Invalid Tests configuration")
    tests = available_tests()
    implemented = [entry["title"] for entry in tests.values() if entry["implemented"]]
    title = values.get("test", implemented[0] if implemented else next(iter(tests), ""))
    if not isinstance(title, str) or title not in {entry["title"] for entry in tests.values()}:
        raise ValueError("Unknown test")
    for key in ("event_path", "launch_options", "config_file"):
        if key in values and not isinstance(values[key], str):
            raise ValueError(f"Invalid Tests {key}")
    shlex.split(values.get("launch_options", ""))
    if "show_stimuli" in values and not isinstance(values["show_stimuli"], bool):
        raise ValueError("show_stimuli must be true or false")
    form_values = values.get("values", {})
    if not isinstance(form_values, dict):
        raise ValueError("Invalid Tests form values")
    spec = next(entry["spec"] for entry in tests.values() if entry["title"] == title)
    known = {field["key"]: field for field in getattr(spec, "FORM", ()) if "key" in field}
    for key, value in form_values.items():
        if key not in known or isinstance(value, (dict, list)):
            raise ValueError(f"Invalid Tests option '{key}'")
    return values


class TestsPanel:
    def __init__(self, root, *, host, reports, on_closed=None):
        self.root, self.host, self.on_closed = root, host, on_closed
        self.events, self.times, self.seen = [], [], set()
        self.clock_origin = time.monotonic()
        self.since_unix = time.time()
        self.connected, self.closed = False, False
        self.recording_state, self.operation = "stopped", None
        self.messages = queue.Queue()
        self.stop_event, self.reader = threading.Event(), None
        self.process = None
        self.event_path = tk.StringVar(value=os.environ.get("HEEPIDERMIS_TESTS_EVENTS",
                                                            os.environ.get("HEEPIDERMIS_STROOP_EVENTS", ""))
                                       or str(DEFAULT_PATH))
        self.launch_options = tk.StringVar(value="")
        self.show_stimuli = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Closed")
        self.count_text = tk.StringVar(value="No events yet")
        self.connect_labels = ("Open", "Close")
        # Discovered tests: {title: entry}; placeholders stay in the dropdown.
        self.tests = {entry["title"]: entry for entry in available_tests().values()}
        self.test_selection = tk.StringVar(value=self._default_title())
        self.config_selection = tk.StringVar(value="defaults.hjson")
        self.config_status = tk.StringVar(value="")
        self.form_values = {}
        self.form_base = {}
        frame = ttk.Frame(host, padding=8)
        frame.pack(fill="x")
        ttk.Label(frame, text="Test").pack(anchor="w")
        selector = ttk.Combobox(frame, textvariable=self.test_selection,
                                values=sorted(self.tests), state="readonly")
        selector.pack(fill="x")
        selector.bind("<<ComboboxSelected>>", lambda _e: self._build_form())
        config_row = ttk.Frame(frame)
        config_row.pack(fill="x", pady=(6, 0))
        ttk.Label(config_row, text="Configuration").pack(side="left")
        ttk.Label(config_row, textvariable=self.config_selection, foreground="#8ab4c8",
                  wraplength=380).pack(side="left", padx=(8, 4))
        ttk.Button(config_row, text="Load…", width=8,
                   command=self.load_config_file).pack(side="left")
        ttk.Button(config_row, text="Save as…", width=9,
                   command=self.save_config_file).pack(side="left", padx=(4, 0))
        ttk.Label(frame, textvariable=self.config_status, wraplength=620).pack(anchor="w")
        ttk.Label(frame, text="Local event feed (same computer)").pack(anchor="w", pady=(6, 0))
        ttk.Entry(frame, textvariable=self.event_path).pack(fill="x", pady=(3, 8))
        ttk.Label(frame, text="Launch options (extra run_test.py flags)").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.launch_options).pack(fill="x", pady=(3, 8))
        self.form_frame = ttk.Frame(frame)
        self.form_frame.pack(fill="x")
        ttk.Checkbutton(frame, text="Also show each stimulus onset",
                        variable=self.show_stimuli).pack(anchor="w")
        ttk.Label(frame, text="Open launches the selected test (with the options above) and "
                  "listens for its events; Close stops listening. Colored labeled events\n"
                  "appear on every plot. The task window uses SPACE to begin and ESC to finish.",
                  wraplength=620).pack(anchor="w", pady=6)
        self._build_form()
        report = ttk.Frame(reports, padding=8)
        report.grid(row=0, column=0, columnspan=2, sticky="nsew")
        ttk.Label(report, textvariable=self.status).pack(anchor="w")
        ttk.Label(report, textvariable=self.count_text).pack(anchor="w")
        self.log = tk.Text(report, height=6, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)
        self.job = root.after(50, self._pump)

    # ------------------------------------------------------------- the form

    def _default_title(self):
        for entry in self.tests.values():
            if entry["implemented"]:
                return entry["title"]
        return next(iter(self.tests), "")

    def _entry(self, title=None):
        return self.tests.get(self.test_selection.get() if title is None else title)

    @staticmethod
    def _display_path(path):
        """Short display form: relative to the repo root when possible."""
        path = Path(path)
        try:
            return str(path.resolve().relative_to(ROOT))
        except ValueError:
            return str(path)

    def _configs_dir(self, entry):
        return self._entry_folder(entry) / "configs"

    @staticmethod
    def _entry_folder(entry):
        return entry["folder"]

    def _dialog_dir(self, entry):
        """Folder the configuration file dialogs open in."""
        configs = self._configs_dir(entry)
        return configs if configs.is_dir() else self._entry_folder(entry)

    def _set_config_file(self, path):
        self.config_selection.set(self._display_path(path))

    def _config_feedback(self, text):
        """Report load/save results right next to the configuration row.

        The device status line lives in the report tab; without this local
        feedback a Load click that changes nothing looks like a dead button.
        """
        self.config_status.set(text)
        self.status.set(text)

    def _base_config(self, spec, entry):
        """The test's own defaults: dataclass fields overlaid with its
        ``configs/defaults.hjson`` (same precedence as the CLI runner)."""
        base = {f.name: getattr(spec.Config, f.name) for f in dataclasses.fields(spec.Config)}
        base.update(settings.config_kwargs(
            entry["key"], settings.load_settings(self._configs_dir(entry) / "defaults.hjson"),
            spec.Config))
        return base

    def _build_form(self):
        """Render the selected test's own configuration menu.

        Placeholder folders show the "not found" message instead of options;
        their Open does nothing.  Checkboxes carry their own label so the box
        and its description always share one line.
        """
        for child in self.form_frame.winfo_children():
            child.destroy()
        self.form_values = {}
        self.form_base = {}
        self.config_status.set("")
        self.config_selection.set("defaults.hjson")
        entry = self._entry()
        if entry is None or not entry["implemented"]:
            ttk.Label(self.form_frame, text="No such test was found.",
                      foreground="#f2bd62").pack(anchor="w", pady=4)
            return
        spec = entry["spec"]
        try:
            self.form_base = self._base_config(spec, entry)
        except (ValueError, OSError) as error:
            self.status.set(f"Invalid default configuration: {error}")
        self._section_rules = []   # (rule, widgets) for conditional sections
        current_rule = None        # widgets go here while the section lasts
        for field in spec.FORM:
            if "section" in field:
                rule = field.get("enabled_by")
                current_rule = None
                if rule:
                    current_rule = []
                    self._section_rules.append((rule, current_rule))
                ttk.Label(self.form_frame, text=field["section"],
                          foreground="#8ab4c8").pack(anchor="w", pady=(8, 1))
                continue
            key, kind = field["key"], field["widget"]
            base = self.form_base.get(key, getattr(spec.Config, key))
            widget = None
            if kind == "checkbox":
                variable = tk.BooleanVar(value=bool(base))
                widget = ttk.Checkbutton(self.form_frame, text=field.get("label", key),
                                         variable=variable)
                widget.pack(anchor="w", pady=(2, 0))
            else:
                ttk.Label(self.form_frame, text=field.get("label", key)).pack(anchor="w", pady=(4, 0))
                if kind == "dropdown":
                    variable = tk.StringVar(value=str(base))
                    widget = ttk.Combobox(self.form_frame, values=[str(o) for o in field["options"]],
                                          textvariable=variable, state="readonly")
                    widget.pack(fill="x")
                    widget.bind("<<ComboboxSelected>>",
                                lambda _e: self._update_section_states())
                elif kind == "slider":
                    variable = tk.StringVar(value=self._widget_text(base))
                    widget = ttk.Spinbox(self.form_frame, from_=field["min"], to=field["max"],
                                         increment=field["step"], textvariable=variable,
                                         width=9)
                    widget.pack(fill="x")
                else:  # Free entry; empty means "use the default".
                    variable = tk.StringVar(value=self._widget_text(base))
                    widget = ttk.Entry(self.form_frame, textvariable=variable)
                    widget.pack(fill="x")
            if current_rule is not None and widget is not None:
                current_rule.append(widget)
            self.form_values[key] = variable
        self._update_section_states()

    def _update_section_states(self):
        """Grey out the sections whose ``enabled_by`` condition is not met.

        A section can declare ``{"section": ..., "enabled_by":
        {"key": ..., "values": (...)}``: its widgets stay usable only while
        the controlling key holds one of the listed values (e.g. the Stroop
        voice options need Response mode = voice, since the calibration and
        the microphone only exist in that mode).
        """
        for rule, widgets in self._section_rules:
            variable = self.form_values.get(rule["key"])
            active = variable is not None and str(variable.get()) in rule["values"]
            for widget in widgets:
                widget.state(["!disabled"] if active else ["disabled"])

    @staticmethod
    def _format_number(value):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    @classmethod
    def _widget_text(cls, value):
        """Format a config value for its form widget."""
        if value is None:
            return ""  # Empty free entry: keep the default.
        if isinstance(value, tuple):
            return " ".join(str(item) for item in value)
        return cls._format_number(value)

    @classmethod
    def _flag_value(cls, value):
        """Format a config value as one command-line argument."""
        if isinstance(value, tuple):
            return " ".join(str(item) for item in value)
        return cls._format_number(value)

    def _coerce(self, spec, field, text):
        """Turn a form string into the config field's type."""
        annotation = str(next(f.type for f in dataclasses.fields(spec.Config)
                              if f.name == field["key"]))
        value = text.strip()
        if "tuple" in annotation or "list" in annotation:
            return tuple(value.split())
        if "int" in annotation:
            return int(float(value))
        if "float" in annotation:
            return float(value)
        return value

    def _form_flags(self, spec):
        """Command-line flags for everything that differs from the test's
        own defaults (``form_base``)."""
        flags = []
        for field in spec.FORM:
            variable = self.form_values.get(field.get("key"))
            if variable is None:
                continue
            base = self.form_base.get(field["key"])
            if field["widget"] == "checkbox":
                if bool(variable.get()) != bool(base):
                    flags += [f"--{field['key'].replace('_', '-')}",
                              "true" if variable.get() else "false"]
                continue
            text = str(variable.get()).strip()
            if not text:  # Empty free entry: keep the default.
                continue
            try:
                value = self._coerce(spec, field, text)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid value for '{field.get('label', field['key'])}': {text}")
            if field["widget"] == "dropdown":
                value = str(variable.get())
                if value != str(base):
                    flags += [f"--{field['key'].replace('_', '-')}", value]
            elif value != base:
                flags += [f"--{field['key'].replace('_', '-')}", self._flag_value(value)]
        return flags

    # ------------------------------------------------ configuration files

    def load_config_file(self):
        """Pick a configuration file in a file explorer and load it.

        The form is first reset to the test's own defaults, then every value
        present in the file is applied, so a file only needs to list the
        parameters it wants to change.  Hand-written files without the test's
        section (bare keys, or a single differently-named section) are
        accepted too, and the dialog can point anywhere on disk.
        """
        entry = self._entry()
        if entry is None or not entry["implemented"]:
            return
        initial = Path(self.config_selection.get())
        path = filedialog.askopenfilename(
            parent=self.root, title="Load test configuration",
            initialdir=str(initial.parent if initial.is_file() else self._dialog_dir(entry)),
            filetypes=[("HJSON configurations", "*.hjson"), ("All files", "*")])
        if not path:
            return
        spec = entry["spec"]
        try:
            data = settings.test_section(entry["key"], settings.load_settings(path))
            kwargs = settings.config_kwargs(entry["key"], data, spec.Config)
        except (ValueError, OSError) as error:
            self._config_feedback(f"Could not load {Path(path).name}: {error}")
            return
        merged = {**self.form_base, **kwargs}
        changed = 0
        for field in spec.FORM:
            key = field.get("key")
            if key is None or key not in self.form_values or key not in merged:
                continue
            variable = self.form_values[key]
            if field["widget"] == "checkbox":
                if variable.get() != bool(merged[key]):
                    variable.set(bool(merged[key]))
                    changed += 1
            else:
                text = self._widget_text(merged[key])
                if str(variable.get()) != text:
                    variable.set(text)
                    changed += 1
        self._set_config_file(path)
        self._config_feedback(f"Loaded {Path(path).name}"
                              + (f" · {changed} value{'s' if changed != 1 else ''} changed"
                                 if changed else " · values already current"))

    def save_config_file(self):
        """Pick a file in a file explorer and save the current form values.

        The dialog asks before overwriting (same behavior as the workspace's
        "Save configuration…"); empty entries keep the test's defaults.
        """
        entry = self._entry()
        if entry is None or not entry["implemented"]:
            return
        spec = entry["spec"]
        current = Path(self.config_selection.get())
        initialfile = current.name if current.suffix == ".hjson" else "defaults.hjson"
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save test configuration",
            initialdir=str(self._dialog_dir(entry)), initialfile=initialfile,
            defaultextension=".hjson",
            filetypes=[("HJSON configurations", "*.hjson")])
        if not path:
            return
        values = {}
        for field in spec.FORM:
            key = field.get("key")
            if key is None:
                continue
            variable = self.form_values[key]
            if field["widget"] == "checkbox":
                values[key] = bool(variable.get())
                continue
            text = str(variable.get()).strip()
            if not text:
                continue  # Empty entry: keep the test's default.
            try:
                values[key] = self._coerce(spec, field, text)
            except (TypeError, ValueError):
                self._config_feedback(f"Invalid value for '{field.get('label', key)}': {text}")
                return
        try:
            settings.save_settings(path, entry["key"], values)
        except (ValueError, OSError) as error:
            self._config_feedback(f"Could not save the configuration: {error}")
            return
        self._set_config_file(path)
        self._config_feedback(f"Configuration saved: {Path(path).name}")

    # ------------------------------------------------------ event listening

    def set_workspace_clock(self, origin):
        if not self.events:
            self.clock_origin = origin
            self.since_unix = time.time() - (time.monotonic() - origin)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 300:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def connect(self):
        if self.connected:
            return
        if self.clock_origin is None:
            self.clear_recording()
        self.stop_event = threading.Event()
        tail = EventTail(Path(self.event_path.get()).expanduser())
        stop, messages = self.stop_event, self.messages

        def receive():
            try:
                while not stop.is_set():
                    for message in tail.poll():
                        messages.put(message)
                    stop.wait(0.05)
            except (OSError, ValueError) as error:
                messages.put(error)
            finally:
                tail.close()

        self.connected = True
        self.reader = threading.Thread(target=receive, name="test-events", daemon=True)
        self.reader.start()
        self.status.set("Listening for test events")

    def disconnect(self):
        self.connected = False
        self.stop_event.set()
        if self.reader is not None:
            self.reader.join(timeout=1)
            self.reader = None
        self.status.set("Closed")

    def _pump(self):
        try:
            self._watch_task()
            for _ in range(256):
                try:
                    message = self.messages.get_nowait()
                except queue.Empty:
                    break
                if isinstance(message, Exception):
                    self.disconnect()
                    self.status.set(f"Event feed error: {message}")
                    break
                if not self.connected:
                    continue
                try:
                    event = Event.from_message(message, self.clock_origin)
                    if event.time_s < 0 or event.unix_s < self.since_unix:
                        continue  # Old runs/boots are not part of this workspace.
                    key = event.run_id, event.sequence
                    if key in self.seen:
                        continue
                    self.seen.add(key)
                    index = bisect.bisect_right(self.times, event.time_s)
                    self.events.insert(index, event)
                    self.times.insert(index, event.time_s)
                    self._log(f"{event.time_s:.3f} s  {event.label}  [{event.test}]")
                    self.count_text.set(f"{len(self.events)} events · latest: {event.label}")
                except (ValueError, KeyError, TypeError) as error:
                    self._log(f"Ignored malformed event: {error}")
        finally:
            if not self.closed:
                self.job = self.root.after(50, self._pump)

    # --------------------------------------------------- open/close (observed)

    def _task_alive(self):
        """Whether a test window we launched is still running."""
        return self.process is not None and self.process.poll() is None

    def _watch_task(self):
        """Report the task window by observing it, never by assumption.

        A finished or closed test window clears the Open/Close state; the
        collected events remain on the plots.
        """
        if self.process is not None and not self._task_alive():
            self.process = None
            self._log("Test window closed.")
            if self.connected:
                self.disconnect()

    def connection(self):
        alive, listening = self._task_alive(), self.connected
        if alive and listening:
            return ("ok", "Test open · listening")
        if alive:
            return ("ok", "Test open · not listening")
        if listening:
            return ("ok", "Listening for events")
        return ("off", "Closed")

    def transport_state(self):
        return "running" if self.connected else "stopped"

    def connect_toggle(self):
        """The device's Open/Close button, driven by what is actually open."""
        if self._task_alive() or self.connected:
            self.close_task()
        else:
            self.launch()

    def close_task(self):
        """Close the test window (if we launched it) and stop listening."""
        if self._task_alive():
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception as error:
                self._log(f"Could not stop the test: {error}")
            self.process = None
        self.disconnect()

    def transport(self, action):
        # Common Start only listens (it must not pop up a fullscreen task by
        # surprise); the device's Close is what ends the test.
        self.connect() if action == "start" else self.disconnect()

    def launch(self):
        """Launch the selected test with its form values and start listening."""
        self.connect()
        if self._task_alive():
            self.status.set("Test already open · listening")
            return
        entry = self._entry()
        if entry is None or not entry["implemented"]:
            self.disconnect()
            self.status.set("No such test was found.")
            return
        try:
            flags = self._form_flags(entry["spec"])
        except ValueError as error:
            self.disconnect()
            self.status.set(str(error))
            return
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(RUNNER), entry["key"], *flags,
                 *shlex.split(self.launch_options.get()),
                 "--events", str(Path(self.event_path.get()).expanduser().resolve())], cwd=ROOT)
            self.status.set(f"{entry['title']} opened · listening for events")
        except (ValueError, OSError) as error:
            self.disconnect()
            self.status.set(f"Could not open the test: {error}")

    # ------------------------------------------------- configuration & data

    def configuration_snapshot(self):
        return {"event_path": self.event_path.get(), "test": self.test_selection.get(),
                "config_file": self.config_selection.get(),
                "values": {key: variable.get() for key, variable in self.form_values.items()},
                "launch_options": self.launch_options.get(),
                "show_stimuli": self.show_stimuli.get()}

    def apply_configuration(self, values):
        values = validate_configuration(values)
        if values.get("test") != self.test_selection.get():
            self.test_selection.set(values["test"])
            self._build_form()
        for name, value in values.items():
            if name == "event_path":
                self.event_path.set(value)
            elif name == "launch_options":
                self.launch_options.set(value)
            elif name == "show_stimuli":
                self.show_stimuli.set(value)
            elif name == "config_file":
                self.config_selection.set(value)
            elif name == "values":
                for key, form_value in value.items():
                    variable = self.form_values.get(key)
                    if variable is not None:
                        variable.set(form_value)

    @staticmethod
    def signals():
        return {"events": {"label": "Test events", "unit": "", "primary": True, "kind": "events"}}

    def plot_snapshot(self, requested=()):
        # Events are small immutable records; insertion of a delayed voice event
        # must not mutate a snapshot already being rendered/exported.
        events, times = tuple(self.events), tuple(self.times)
        show_stimuli = self.show_stimuli.get()

        def get_series(low=None, high=None):
            first = 0 if low is None else bisect.bisect_left(times, low)
            last = len(events) if high is None else bisect.bisect_right(times, high)
            selected = events[first:last]
            return {"events": {"times": list(times[first:last]),
                               "values": [1 if e.color == "green" else 0 if e.color == "red" else math.nan
                                          for e in selected], "labels": [e.label for e in selected],
                               "segments": [e.run_id for e in selected], "kind": "events"}}

        def markers(low, high):
            return [(e.time_s, e.label, e.hex_color())
                    for e in events[bisect.bisect_left(times, low):bisect.bisect_right(times, high)]
                    if show_stimuli or e.label != "stimulus"]

        return SimpleNamespace(get_series=get_series, get_markers=markers, signals=self.signals,
                               invalid_spans=lambda *_args: [], revision=(len(events), show_stimuli),
                               clock_origin=self.clock_origin, latest=times[-1] if times else 0.0)

    def recording_snapshot(self):
        events = tuple(self.events)
        return {"columns": COLUMNS, "rows": lambda: (e.row() for e in events),
                "count": len(events), "sidecars": {}}

    def clear_recording(self):
        self.events, self.times, self.seen = [], [], set()
        self.clock_origin, self.since_unix = time.monotonic(), time.time()
        self.count_text.set("No events yet")

    def load_recording(self, path):
        events = read_recording(path)
        self.disconnect()
        self.events, self.times = events, [event.time_s for event in events]
        self.seen = {(event.run_id, event.sequence) for event in events}
        self.clock_origin = None  # Offline alignment comes from the workspace sidecar.
        self.count_text.set(f"Loaded {len(events)} test events")
        return path

    def close(self):
        self.closed = True
        self.disconnect()
        self.root.after_cancel(self.job)
        for child in self.host.winfo_children():
            child.destroy()
