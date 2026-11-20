"""Passive local event device; cognitive tests run in their own process/window."""

import bisect
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
from tkinter import ttk
from types import SimpleNamespace

from .recording import COLUMNS, Event, EventTail, read_recording


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PATH = ROOT / "sw/gui/outs/stroop/events.jsonl"


def validate_configuration(values):
    if not isinstance(values, dict):
        raise ValueError("Invalid Stroop configuration")
    if values.get("variant", "stroop") not in ("stroop", "stroop-stress"):
        raise ValueError("Unknown Stroop variant")
    for key in ("event_path", "launch_options"):
        if key in values and not isinstance(values[key], str):
            raise ValueError(f"Invalid Stroop {key}")
    shlex.split(values.get("launch_options", ""))
    if "show_stimuli" in values and not isinstance(values["show_stimuli"], bool):
        raise ValueError("show_stimuli must be true or false")
    if "voice_mode" in values and not isinstance(values["voice_mode"], bool):
        raise ValueError("voice_mode must be true or false")
    if "voice_calibration" in values and not isinstance(values["voice_calibration"], bool):
        raise ValueError("voice_calibration must be true or false")
    return values


class StroopPanel:
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
        self.event_path = tk.StringVar(value=os.environ.get("HEEPIDERMIS_STROOP_EVENTS", str(DEFAULT_PATH)))
        self.variant = tk.StringVar(value="stroop")
        self.launch_options = tk.StringVar(value="")
        self.show_stimuli = tk.BooleanVar(value=False)
        self.voice_mode = tk.BooleanVar(value=False)
        self.voice_calibration = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Closed")
        self.count_text = tk.StringVar(value="No events yet")
        self.connect_labels = ("Open", "Close")
        frame = ttk.Frame(host, padding=8)
        frame.pack(fill="x")
        for label, variable in (("Local event feed (same computer)", self.event_path),
                                ("Launch options (same flags as STROOP_ARGS)", self.launch_options)):
            ttk.Label(frame, text=label).pack(anchor="w")
            ttk.Entry(frame, textvariable=variable).pack(fill="x", pady=(3, 8))
        ttk.Combobox(frame, textvariable=self.variant, values=("stroop", "stroop-stress"),
                     state="readonly").pack(fill="x")
        ttk.Checkbutton(frame, text="Also show each stimulus onset", variable=self.show_stimuli).pack(anchor="w")
        ttk.Checkbutton(frame, text="Voice input — say the colors instead of pressing keys",
                        variable=self.voice_mode).pack(anchor="w")
        ttk.Checkbutton(frame, text="Voice calibration — say each color once before the test",
                        variable=self.voice_calibration).pack(anchor="w")
        ttk.Label(frame, text="Open launches the Stroop test (same as make stroop) and listens\n"
                  "for its events; Close stops listening. White labeled events appear on\n"
                  "every plot. The task window uses SPACE to begin and ESC to finish.",
                  wraplength=620).pack(anchor="w", pady=6)
        report = ttk.Frame(reports, padding=8)
        report.grid(row=0, column=0, columnspan=2, sticky="nsew")
        ttk.Label(report, textvariable=self.status).pack(anchor="w")
        ttk.Label(report, textvariable=self.count_text).pack(anchor="w")
        self.log = tk.Text(report, height=6, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)
        self.job = root.after(50, self._pump)

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

    def connect_toggle(self):
        # The device's Open button launches the Stroop test (as make stroop
        # does) and starts listening; Close only stops the listener.
        self.disconnect() if self.connected else self.launch()

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
        self.reader = threading.Thread(target=receive, name="stroop-events", daemon=True)
        self.reader.start()
        self.status.set("Listening for Stroop events")

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
            self._log("Stroop test window closed.")
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
                self._log(f"Could not stop the Stroop test: {error}")
            self.process = None
        self.disconnect()

    def transport(self, action):
        # Common Start only listens (it must not pop up a fullscreen task by
        # surprise); the device's Close is what ends the test.
        self.connect() if action == "start" else self.disconnect()

    def launch(self):
        # Always listen; only spawn a window when none of ours is open.
        self.connect()
        if self._task_alive():
            self.status.set("Stroop test already open · listening")
            return
        args = [self.variant.get()]
        if self.voice_mode.get():
            args += ["--response-mode", "voice",
                     "--voice-calibration", str(self.voice_calibration.get()).lower()]
        try:
            self.process = subprocess.Popen([sys.executable, str(ROOT / "tests/run_test.py"),
                                             *args, *shlex.split(self.launch_options.get()),
                                             "--events", str(Path(self.event_path.get()).expanduser().resolve())], cwd=ROOT)
            self.status.set("Stroop opened · listening for events")
        except (ValueError, OSError) as error:
            self.disconnect()
            self.status.set(f"Could not open Stroop: {error}")

    def configuration_snapshot(self):
        return {"event_path": self.event_path.get(), "variant": self.variant.get(),
                "launch_options": self.launch_options.get(), "show_stimuli": self.show_stimuli.get(),
                "voice_mode": self.voice_mode.get(),
                "voice_calibration": self.voice_calibration.get()}

    def apply_configuration(self, values):
        for name, value in validate_configuration(values).items():
            if name in ("event_path", "variant", "launch_options", "show_stimuli",
                        "voice_mode", "voice_calibration"):
                getattr(self, name).set(value)

    @staticmethod
    def signals():
        return {"events": {"label": "Stroop events", "unit": "", "primary": True, "kind": "events"}}

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
                               "values": [1 if e.label == "correct" else 0 if e.label == "wrong" else math.nan
                                          for e in selected], "labels": [e.label for e in selected],
                               "segments": [e.run_id for e in selected], "kind": "events"}}

        def markers(low, high):
            return [(e.time_s, e.label) for e in events[bisect.bisect_left(times, low):bisect.bisect_right(times, high)]
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
        self.count_text.set(f"Loaded {len(events)} Stroop events")
        return path

    def close(self):
        self.closed = True
        self.disconnect()
        self.root.after_cancel(self.job)
        for child in self.host.winfo_children():
            child.destroy()
