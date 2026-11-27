"""Live EDA classification device: Setz/SVM inference over any workspace signal.

The panel loads a sw/classification ``.joblib`` model, consumes one selected
"device:signal" conductance series, and exposes as device signals:

* one signal per model feature (display units),
* the raw SVM ``margin`` plus a constant ``margin threshold`` signal, so the
  operating point is visible where the margin crosses it,
* the ``decision`` — drawn as a red full-height area wherever the margin is
  POSITIVE (the device only knows POSITIVE/NEGATIVE, whatever the training
  called the classes).

Its configuration pane shows the feature space (red/blue regions, the
division and threshold, no support points) with the operating point as a
white circle with black outline moving as live windows are classified.
All algorithmic work happens in ``inference.py``, which delegates to
``sw/classification``.
"""

import bisect
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from types import SimpleNamespace

from ...common.theme import UI_BORDER, UI_MUTED, UI_PLOT, UI_TEXT

from . import inference

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MODEL_DIR = ROOT / "sw/classification/outputs"
MODEL_FILETYPES = [("SVM models", "*.joblib"), ("All files", "*")]

POLL_MS = 150          # Tk-side pickup of worker results and pane refresh.
POSITIVE_COLOR = "#b22222"   # Decision area / POSITIVE region.
NEGATIVE_COLOR = "#2e5f8a"   # NEGATIVE region.

# Workspace feature-space pane colors (dark theme).
PLANE_FIGURE_FACE = UI_PLOT
PLANE_TEXT = UI_TEXT
PLANE_MUTED = UI_MUTED


def validate_configuration(values):
    """Validate a saved Classification configuration (raises ValueError)."""
    if not isinstance(values, dict):
        raise ValueError("Invalid Classification configuration")
    for key in ("model_path", "source"):
        if key in values and not isinstance(values[key], str):
            raise ValueError(f"Invalid Classification {key}")
    if "threshold" in values:
        try:
            float(values["threshold"])
        except (TypeError, ValueError):
            raise ValueError("Invalid Classification threshold") from None
    for key in ("window_s", "step_s", "sample_rate_Hz"):
        if key in values:
            try:
                value = float(values[key])
            except (TypeError, ValueError):
                raise ValueError(f"Invalid Classification {key}") from None
            if not value > 0:
                raise ValueError(f"Invalid Classification {key}")
    return values


class ClassificationPanel:
    """Addable like a device; computes Setz features + SVM inference live."""

    # The workspace hands this panel a series provider on creation (see
    # Workspace._enable_device): a callable returning ({device_key: snapshot},
    # {"device:signal": unit}) for the current workspace state.
    WORKSPACE_HOOKS = True

    def __init__(self, root, *, host, reports, on_closed=None,
                 series_provider=None, on_change=None):
        self.root, self.host, self.on_closed = root, host, on_closed
        self.series_provider = series_provider
        self.on_change = on_change
        self.connected, self.closed = False, False
        # Attributes the workspace's load/save paths probe directly.
        self.recording_state, self.operation, self.save_future = "stopped", None, None
        self.clock_origin = None   # Outputs already sit on the workspace clock.

        self._model = None
        self._model_version = 0
        self._outputs = {}         # Latest applied compute_outputs() result.
        self._revision = 0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._results = queue.Queue()
        self._worker = None
        self._fingerprint = None   # Last computed source fingerprint.

        self.model_path = tk.StringVar(value="")
        self.threshold_text = tk.StringVar(value="0")
        self.window_text = tk.StringVar(value="60")
        self.step_text = tk.StringVar(value="15")
        self.rate_text = tk.StringVar(value="4")
        self.source = tk.StringVar(value="")
        self.status = tk.StringVar(value="No model loaded")
        self.summary = tk.StringVar(value="")
        self.hint = tk.StringVar()
        # Plain mirrors of the Tk variables for the worker thread: Tk variable
        # access is only safe on the main thread, so the traces below (which
        # run there) keep these copies current.
        self._threshold_value = 0.
        self._window_value = 60.
        self._step_value = 15.
        self._rate_value = 4.
        self._source_value = ""
        self._model_path_value = ""

        self._build_pane(host)
        self._build_report(reports)
        self._poller = root.after(POLL_MS, self._poll)
        self.threshold_text.trace_add("write", self._on_threshold)
        self.window_text.trace_add("write", self._on_window)
        self.step_text.trace_add("write", self._on_step)
        self.rate_text.trace_add("write", self._on_rate)
        self.source.trace_add("write", self._on_source)
        self._update_hint()

    # --------------------------------------------------------- Tk mirrors

    def _on_threshold(self, *_args):
        try:
            value = float(self.threshold_text.get())
        except (TypeError, ValueError):
            return
        self._threshold_value = value
        self._nudge()

    def _on_window(self, *_args):
        self._update_positive("_window_value", self.window_text)

    def _on_step(self, *_args):
        self._update_positive("_step_value", self.step_text)

    def _on_rate(self, *_args):
        self._update_positive("_rate_value", self.rate_text)

    def _update_positive(self, mirror, variable):
        """Mirror a positive-number field; ignore partial/invalid edits."""
        try:
            value = float(variable.get())
        except (TypeError, ValueError):
            return
        if value > 0:
            setattr(self, mirror, value)
        self._update_hint()
        self._nudge()

    def _update_hint(self, *_args):
        self.hint.set(
            "The device adds one signal per model feature, the raw SVM "
            "margin with a constant margin-threshold signal, and the decision: a red\n"
            "area wherever the margin is POSITIVE. Windows (%g s, %g step) need "
            "covered data before the first margin appears."
            % (self._window_value, self._step_value))

    def _on_source(self, *_args):
        self._source_value = self.source.get()
        self._nudge()

    # ------------------------------------------------------------ the pane

    def _build_pane(self, host):
        frame = ttk.Frame(host, padding=8)
        frame.pack(fill="x")
        ttk.Label(frame, text="Model (.joblib from sw/classification)").pack(anchor="w")
        model_row = ttk.Frame(frame)
        model_row.pack(fill="x", pady=(3, 0))
        ttk.Button(model_row, text="Load…", command=self.load_model_dialog).pack(side="left")
        ttk.Label(model_row, textvariable=self.model_path, foreground="#8ab4c8",
                  wraplength=420).pack(side="left", padx=(8, 0))
        ttk.Label(frame, textvariable=self.summary, wraplength=520).pack(anchor="w", pady=(2, 0))
        ttk.Label(frame, text="Decision threshold (margin cut; inference-time)").pack(anchor="w", pady=(6, 0))
        self.threshold_box = ttk.Spinbox(frame, from_=-50., to=50., increment=.1,
                                         textvariable=self.threshold_text, width=9)
        self.threshold_box.pack(anchor="w")
        ttk.Label(frame, text="Windowing (inference-time; defaults follow the loaded model)").pack(
            anchor="w", pady=(6, 0))
        windowing = ttk.Frame(frame)
        windowing.pack(fill="x")
        for label, variable, width, low, high, increment in (
                ("Window (s)", self.window_text, 6, 5., 600., 5.),
                ("Step (s)", self.step_text, 5, 1., 120., 1.),
                ("Rate (Hz)", self.rate_text, 5, .5, 100., .5)):
            ttk.Label(windowing, text=label).pack(side="left", padx=(0, 3))
            ttk.Spinbox(windowing, from_=low, to=high, increment=increment,
                        textvariable=variable, width=width).pack(side="left", padx=(0, 10))
        ttk.Label(frame, text="Source signal (conductance)").pack(anchor="w", pady=(6, 0))
        self.source_box = ttk.Combobox(frame, textvariable=self.source, state="readonly",
                                       postcommand=self._refresh_sources)
        self.source_box.pack(fill="x")
        ttk.Label(frame, text="Feature space", foreground="#8ab4c8").pack(anchor="w", pady=(8, 2))
        self._build_space(frame)
        ttk.Label(frame, textvariable=self.status, foreground="#8ab4c8",
                  wraplength=520).pack(anchor="w", pady=(4, 0))
        ttk.Label(frame, textvariable=self.hint,
                  style="Muted.TLabel", wraplength=520).pack(anchor="w", pady=4)

    def _build_space(self, parent):
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        self._figure = Figure(figsize=(5.1, 3.8), dpi=96)
        self._figure.patch.set_facecolor(PLANE_FIGURE_FACE)
        self._axes = self._figure.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._figure, master=parent)
        self._canvas.get_tk_widget().pack(fill="x")
        self._drawn_key = None
        self._draw_space()

    def _build_report(self, reports):
        report = ttk.Frame(reports, padding=8)
        report.grid(row=0, column=0, columnspan=2, sticky="nsew")
        ttk.Label(report, textvariable=self.status).pack(anchor="w")
        ttk.Label(report, textvariable=self.summary, style="Muted.TLabel",
                  wraplength=620).pack(anchor="w")
        self.log = tk.Text(report, height=6, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

    # ------------------------------------------------------- configuration

    def _refresh_sources(self):
        """Source choices: every device signal the pipeline can convert."""
        choices = self._source_choices()
        self.source_box.configure(values=choices)
        if self.source.get() not in choices and choices:
            self.source.set(choices[0])

    def _source_choices(self):
        if self.series_provider is None:
            return []
        try:
            _snapshots, units = self.series_provider()
        except Exception:
            return []
        return sorted(key for key in units
                      if not key.startswith("classification:"))

    def load_model_dialog(self):
        initial = Path(self.model_path.get() or DEFAULT_MODEL_DIR)
        path = filedialog.askopenfilename(
            parent=self.root, title="Load SVM model",
            initialdir=str(initial.parent if initial.is_file() else DEFAULT_MODEL_DIR),
            filetypes=MODEL_FILETYPES)
        if path:
            self.set_model(path)

    def set_model(self, path):
        """Load a model on the Tk thread (joblib loads are quick)."""
        try:
            model = inference.load_model(path)
        except ImportError as error:
            self.status.set(f"Could not load the model: {error}. The Python "
                            "environment running the GUI is missing scikit-learn/joblib "
                            "(pip install -r sw/gui/requirements.txt).")
            self._log(f"Model load failed: {error}")
            return
        except Exception as error:
            self.status.set(f"Could not load the model: {error}")
            return
        self._apply_model(str(path), model)

    def _apply_model(self, path, model):
        with self._lock:
            self._model, self._model_version = model, self._model_version + 1
            self._outputs, self._fingerprint = {}, None
        stored = float(model.get("decision_threshold", 0.) or 0.)
        self._model_path_value = str(path)
        self.model_path.set(path)
        self.threshold_text.set(f"{stored:g}")
        # Windowing follows the model's trained options until tuned by hand.
        options = model.get("feature_options", {})
        for key, variable in (("window_s", self.window_text), ("step_s", self.step_text),
                              ("sample_rate_Hz", self.rate_text)):
            if key in options:
                variable.set(f"{float(options[key]):g}")
        kernel = getattr(model["pipeline"].named_steps.get("svc", None), "kernel", "?")
        names = ", ".join(model["feature_names"])
        self.summary.set(f"{kernel} SVM · features: {names} · "
                         f"threshold {stored:g} · trained on {len(model.get('subjects', []))} subject(s)")
        self.status.set(f"Model loaded: {Path(path).name}")
        self._log(f"Loaded {path}")
        self._nudge()
        if self.on_change is not None:
            try:
                self.on_change()   # The signal catalogue just grew feature rows.
            except Exception:
                pass

    def current_threshold(self):
        try:
            value = float(self.threshold_text.get())
        except (TypeError, ValueError):
            return None
        return value if value == value else None   # Reject NaN from partial edits.

    def _current_positive(self, variable):
        """Value of a positive-number field, or None while invalid/empty."""
        try:
            value = float(variable.get())
        except (TypeError, ValueError):
            return None
        return value if value == value and value > 0 else None

    # ------------------------------------------------------------- worker

    def _nudge(self):
        self._wake.set()

    def connect(self):
        if self.connected:
            return
        self.connected = True
        if self._worker is None:
            self._stop.clear()
            self._worker = threading.Thread(target=self._work, name="classification", daemon=True)
            self._worker.start()
        self.status.set("Computing classification")
        self._nudge()

    def disconnect(self):
        self.connected = False
        self.status.set("Stopped" if self._model is not None else "No model loaded")

    def connect_toggle(self):
        self.disconnect() if self.connected else self.connect()

    def transport(self, action):
        self.connect() if action == "start" else self.disconnect()

    def transport_state(self):
        return "running" if self.connected else "stopped"

    def connection(self):
        if self.connected and self._model is not None:
            source = f" · source {self.source.get()}" if self.source.get() else ""
            return ("ok", f"Computing{source}")
        if self._model is not None:
            return ("off", "Stopped")
        return ("off", "No model loaded")

    def _work(self):
        """Background inference: latest-wins, throttled by input changes."""
        while not self._stop.is_set():
            self._wake.wait(.5)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                if not self._update_once():
                    break
            except Exception as error:   # The worker must survive any failure.
                self._results.put(("error", f"Classification worker error: {error}"))

    def _update_once(self):
        if not self.connected or self.closed:
            return True
        if self.series_provider is None:
            self._results.put(("status", "No workspace series provider."))
            return True
        threshold = self._threshold_value
        if threshold is None:
            return True
        with self._lock:
            model, version = self._model, self._model_version
        if model is None:
            self._results.put(("status", "Load a model (.joblib) to start inference."))
            return True
        source = self._source_value
        if not source:
            self._results.put(("status", "Pick a source signal."))
            return True
        device, _, signal = source.partition(":")
        try:
            snapshots, units = self.series_provider()
        except Exception as error:
            self._results.put(("status", f"Series provider failed: {error}"))
            return True
        snapshot = snapshots.get(device)
        data = None
        try:
            if snapshot is not None:
                data = (snapshot.get_series() or {}).get(signal)
        except Exception as error:
            self._results.put(("status", f"Source series failed: {error}"))
            return True
        if not data or not data.get("times"):
            self._results.put(("status", f"Waiting for source data: {source}"))
            return True
        times = data["times"]
        window, step, rate = self._window_value, self._step_value, self._rate_value
        fingerprint = (source, len(times), round(float(times[-1]), 6), threshold,
                       version, self._model_path_value, window, step, rate)
        with self._lock:
            unchanged = fingerprint == self._fingerprint
        if unchanged:
            return True
        try:
            outputs = inference.compute_outputs(model, threshold, times, data["values"],
                                                units.get(source, ""), window_s=window,
                                                step_s=step, sample_rate_Hz=rate)
        except ValueError as error:
            self._results.put(("status", f"Waiting: {error}"))
            return True
        except Exception as error:
            self._results.put(("error", f"Inference failed: {error}"))
            return True
        with self._lock:
            self._fingerprint = fingerprint
        self._results.put(("outputs", outputs))
        return True

    # ------------------------------------------------------------- poller

    def _poll(self):
        """Tk-thread pickup: apply worker results, refresh pane and status."""
        if self.closed:
            return
        try:
            while True:
                kind, payload = self._results.get_nowait()
                if kind == "outputs":
                    with self._lock:
                        self._outputs = payload
                        self._revision += 1
                    windows = payload.get("windows_valid", 0)
                    self.status.set(f"Classified {windows} window(s) · threshold {payload['threshold']:g}")
                elif kind == "status":
                    self.status.set(payload)
                elif kind == "error":
                    self.status.set(payload)
                    self._log(payload)
        except queue.Empty:
            pass
        self._draw_space()
        self._poller = self.root.after(POLL_MS, self._poll)

    # ------------------------------------------------------ feature space

    def _plane_key(self, plane):
        if plane is None:
            return None
        point = plane.get("point_xy")
        margins = plane["margins"]
        return (id(margins), float(margins.sum()), point,
                self.current_threshold(), self.model_path.get())

    def _draw_space(self):
        """Redraw the feature-space pane when plane, point or threshold move."""
        with self._lock:
            plane = self._outputs.get("plane")
            threshold = self._outputs.get("threshold", self.current_threshold())
        key = self._plane_key(plane)
        if key == self._drawn_key:
            return
        self._drawn_key = key
        axes = self._axes
        axes.clear()
        axes.set_facecolor(PLANE_FIGURE_FACE)
        if plane is None:
            axes.text(.5, .5, "Load a model with at least two features",
                      ha="center", va="center", color=PLANE_MUTED, transform=axes.transAxes)
        else:
            pair, uu, vv, margins = plane["pair"], plane["uu"], plane["vv"], plane["margins"]
            axes.contourf(uu, vv, margins > threshold, levels=[-.5, .5, 1.5],
                          colors=[NEGATIVE_COLOR, POSITIVE_COLOR], alpha=.55)
            if margins.min() < threshold < margins.max():
                axes.contour(uu, vv, margins, levels=[threshold], colors="white", linewidths=1.4)
            if threshold:
                axes.contour(uu, vv, margins, levels=[0], colors="white",
                             linewidths=.8, linestyles="dotted")
            if plane.get("point_xy") is not None:
                # The operating point: white circle, black outline.
                axes.plot([plane["point_xy"][0]], [plane["point_xy"][1]], "o",
                          markerfacecolor="white", markeredgecolor="black",
                          markersize=9, zorder=5)
            axes.set_xlabel(f"{pair[0]} ({plane['scales'][0][1]})", color=PLANE_MUTED)
            axes.set_ylabel(f"{pair[1]} ({plane['scales'][1][1]})", color=PLANE_MUTED)
            axes.set_title(f"POSITIVE above the solid line (threshold {threshold:g}); "
                           f"dotted: fitted margin 0", color=PLANE_MUTED, fontsize=8)
            for spine in axes.spines.values():
                spine.set_color(UI_BORDER)
            axes.tick_params(colors=PLANE_MUTED, labelsize=8)
            axes.text(.02, .02, "○ operating point (live)", color=PLANE_TEXT, fontsize=7,
                      transform=axes.transAxes)
        self._canvas.draw_idle()

    # ------------------------------------------------- signals & snapshots

    def signals(self):
        with self._lock:
            model, outputs = self._model, self._outputs
        if model is None:
            return {}
        catalogue = {}
        features = outputs.get("features", {})
        for name in model["feature_names"]:
            catalogue[name] = {"label": name, "unit": features.get(name, {}).get("unit", "")}
        catalogue["margin"] = {"label": "SVM margin", "unit": "1"}
        catalogue["margin threshold"] = {"label": "Margin threshold", "unit": "1"}
        catalogue["decision"] = {"label": "Decision (POSITIVE = red area)", "unit": "class index"}
        return catalogue

    @staticmethod
    def _trim(entry, low, high):
        times = entry.get("times")
        if not times:
            return entry
        if low is None and high is None:
            return entry
        first = 0 if low is None else bisect.bisect_left(times, low)
        last = len(times) if high is None else bisect.bisect_right(times, high)
        if first <= 0 and last >= len(times):
            return entry
        trimmed = {key: value for key, value in entry.items() if key not in ("times", "values")}
        trimmed["times"] = times[first:last]
        if "values" in entry:
            trimmed["values"] = entry["values"][first:last]
        return trimmed

    def get_series(self, low=None, high=None):
        with self._lock:
            outputs = self._outputs
        series = {}
        for name, entry in outputs.get("features", {}).items():
            series[name] = self._trim(entry, low, high)
        series["margin"] = self._trim(outputs.get("margin", {"times": [], "values": []}), low, high)
        series["margin threshold"] = self._trim(
            outputs.get("margin_threshold", {"times": [], "values": []}), low, high)
        decision = self._trim(outputs.get("decision", {"times": [], "values": []}), low, high)
        decision["kind"] = "decision"
        spans = outputs.get("decision", {}).get("spans", [])
        if low is not None or high is not None:
            spans = [(max(a, low if low is not None else a), min(b, high if high is not None else b))
                     for a, b in spans
                     if (low is None or b >= low) and (high is None or a <= high)]
        decision["spans"] = spans
        series["decision"] = decision
        return series

    def plot_snapshot(self, requested=()):
        with self._lock:
            outputs = dict(self._outputs)
            revision = self._revision
        times = outputs.get("window_times", [])

        def get_series(low=None, high=None):
            return self.get_series(low, high)

        return SimpleNamespace(
            get_series=get_series, signals=self.signals,
            invalid_spans=lambda *_args: [],
            revision=(revision,),
            clock_origin=self.clock_origin,
            latest=times[-1] if times else 0.0)

    def recording_snapshot(self):
        return {"columns": [], "rows": lambda: iter(()), "count": 0, "sidecars": {}}

    # ------------------------------------------- configuration persistence

    def configuration_snapshot(self):
        threshold = self.current_threshold()
        values = {"model_path": self.model_path.get(), "source": self.source.get()}
        if threshold is not None:
            values["threshold"] = threshold
        for key, variable in (("window_s", self.window_text), ("step_s", self.step_text),
                              ("sample_rate_Hz", self.rate_text)):
            value = self._current_positive(variable)
            if value is not None:
                values[key] = value
        return values

    def apply_configuration(self, values):
        values = validate_configuration(values)
        source = values.get("source", "")
        if source and source != self.source.get():
            self.source.set(source)
        path = values.get("model_path", "")
        if path and path != self.model_path.get() and Path(path).is_file():
            self.set_model(path)   # Resets the numeric fields to the model's values.
        # Numeric fields after the model load, so saved values win over the
        # model's trained defaults.
        if "threshold" in values:
            text = f"{float(values['threshold']):g}"
            if text != self.threshold_text.get():
                self.threshold_text.set(text)
        for key, variable in (("window_s", self.window_text), ("step_s", self.step_text),
                              ("sample_rate_Hz", self.rate_text)):
            if key in values:
                text = f"{float(values[key]):g}"
                if text != variable.get():
                    variable.set(text)

    # --------------------------------------------------------- housekeeping

    def clear_recording(self):
        with self._lock:
            self._outputs, self._fingerprint = {}, None
            self._revision += 1

    def load_recording(self, path):
        return None   # Nothing native to load; outputs are recomputed from sources.

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 200:
            self.log.delete("1.0", "80.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def close(self):
        self.closed = True
        self._stop.set()
        self._wake.set()
        if self.root is not None:
            try:
                self.root.after_cancel(self._poller)
            except Exception:
                pass
        for child in self.host.winfo_children():
            child.destroy()
