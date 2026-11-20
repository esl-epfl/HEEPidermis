"""Shared scaffolding for the interactive tests in ``sw/gui/tests/``.

Design notes (the standard every test follows — see ``sw/gui/tests/AGENTS.md``)
-------------------------------------------------------------------------------
* Every test is a class deriving from :class:`CognitiveTest` with a matching
  dataclass config deriving from :class:`TestConfig`.  The config fields are
  plain ``int/float/bool/str`` so they can drive the command line interface
  (``sw/gui/tests/run_test.py``) and the auto-generated GUI form.
* A test renders into its own ``tk.Toplevel`` (fullscreen by default) and
  never touches acquisition hardware.  ``run()`` blocks and returns a
  :class:`TestResult` when used standalone; when embedded in the GUI, pass a
  ``parent`` widget and an ``on_finish`` callback instead.
* Results are JSON-serializable (``TestResult.to_dict()``) so they can be
  saved next to a session CSV or plotted in the GUI.
* Every test publishes the standard colored event feed
  (``common/events.py``); the GUI listens to that feed, so tests integrate
  without any GUI modification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
from pathlib import Path
import time
import tkinter as tk
import uuid
from typing import Callable, Optional


@dataclass
class TestConfig:
    """Base configuration for every cognitive test."""

    # Seconds of rest/countdown shown before the test begins.
    rest_time: float = 10.0
    # Total duration of the test phase, in seconds.
    test_time: float = 120.0
    # Fullscreen the test window (recommended for real experiments).
    fullscreen: bool = False
    # Window/canvas background color.
    background: str = "#101014"
    # Foreground (text) color for instructions and labels.
    foreground: str = "#f2f2f2"
    # Base font size in points; 0 picks a size from the window height.
    font_size: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict):
        """Build a config ignoring keys that are not fields of this class."""
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass
class TestResult:
    """Outcome of one test run; plain data so it can be saved or plotted."""

    test: str                 # Registry name, e.g. "stroop".
    config: dict              # The configuration that was used.
    events: list              # One entry per trial/response.
    summary: dict             # Aggregate measures (accuracy, mean RT, ...).
    started: str              # ISO timestamp of the run.
    duration: float           # Wall time of the test phase, in seconds.
    aborted: bool = False     # True when the run was cut short (Escape).
    timeline: list = field(default_factory=list)  # Timestamped GUI markers, including phase changes.

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")


class CognitiveTest:
    """Base class for the runnable tests.

    Subclasses set ``NAME`` and ``ConfigClass`` and implement ``_begin`` plus
    the key/timeout handling they need.  The base owns the window, the clock,
    the ``after`` bookkeeping and the event log.
    """

    NAME: str = "cognitive"
    ConfigClass = TestConfig

    def __init__(self, config: Optional[TestConfig] = None, **overrides):
        """Build a test from a config (or its defaults) plus keyword overrides.

        Both forms work and are equivalent:

            StroopTest(StroopConfig(test_time=30.0))
            StroopTest(test_time=30.0)

        ``None`` overrides are ignored, so GUI code can pass optional form
        fields straight through as keyword arguments.
        """
        data = self.ConfigClass().to_dict()
        if config is not None:
            data.update(config.to_dict() if isinstance(config, TestConfig) else dict(config))
        data.update({key: value for key, value in overrides.items() if value is not None})
        self.config = self.ConfigClass.from_dict(data)

        self.events: list[dict] = []
        self.result: Optional[TestResult] = None
        self.on_event: Optional[Callable[[dict], None]] = None
        self.on_finish: Optional[Callable[[TestResult], None]] = None

        self._root: Optional[tk.Misc] = None
        self._canvas: Optional[tk.Canvas] = None
        self._owns_root = False
        self._after_jobs: list[str] = []
        self._started_monotonic = 0.0
        self._started_iso = ""
        self._aborted = False
        self.on_marker = None
        self._publisher = None
        self._reset_markers()

    def _reset_markers(self):
        self.timeline = []
        self._run_id = uuid.uuid4().hex
        self._wall_offset = time.time() - time.monotonic()

    def mark(self, label, *, at=None, color="white", **details):
        """Publish a phase/outcome at its source time, never the GUI receive time.

        ``color`` is part of the standard event format (see AGENTS.md): a
        palette name ("green", "red", "white", "yellow", "blue") or a
        "#rrggbb" hex string.  Verdicts use green/red; every informational
        phase stays white.
        """
        stamp = time.monotonic() if at is None else at
        event = {"schema_version": 2, "run_id": self._run_id, "test": self.NAME,
                 "sequence": len(self.timeline), "label": label, "color": color,
                 "monotonic_s": stamp, "unix_s": stamp + self._wall_offset,
                 "details": details}
        self.timeline.append(event)
        if self._publisher is not None:
            try:
                self._publisher.emit(event)
            except OSError as error:
                print(f"[events] Could not publish Stroop marker: {error}", flush=True)
        if self.on_marker is not None:
            try:
                self.on_marker(event)
            except Exception:
                pass  # A GUI sink must never break the test.

    # ------------------------------------------------------------ public API

    def run(self, parent=None, event_path=None) -> Optional[TestResult]:
        """Run the test and return its result.

        Standalone use (``parent is None``): creates a ``tk.Tk`` window, runs
        the Tk mainloop and returns the :class:`TestResult` when the window
        closes.  Embedded use: pass a ``parent`` widget (a ``Toplevel`` is
        created on top of it) and read ``result`` from the ``on_finish``
        callback, because no mainloop is started here.
        """
        if parent is None:
            self._root = tk.Tk()
            self._owns_root = True
        else:
            self._root = tk.Toplevel(parent)
        self._root.title(f"{self.NAME.capitalize()} test")
        self._root.configure(background=self.config.background)
        if self.config.fullscreen:
            self._root.attributes("-fullscreen", True)
        else:
            self._root.geometry("1024x680")

        self._canvas = tk.Canvas(self._root, background=self.config.background,
                                 highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda _e: self._redraw_static())

        self._root.bind("<Key>", self._on_key)
        self._root.bind("<Escape>", self._on_escape)
        self._root.protocol("WM_DELETE_WINDOW", lambda: self._finish(aborted=self.state != "summary"))

        self._started_iso = time.strftime("%Y-%m-%d %H:%M:%S")
        self._started_monotonic = time.monotonic()
        try:
            # Every test publishes the standard event feed (AGENTS.md); the
            # GUI and the terminal both read these packets.
            from .events import EventPublisher
            self._publisher = EventPublisher(event_path)
            print(f"[events] Publishing to {self._publisher.path}", flush=True)
            self._begin()
            if self._owns_root:
                self._root.mainloop()
        except BaseException:
            if self._publisher is not None:
                self._publisher.close()
            self._root.destroy()
            raise
        finally:
            if self._owns_root and self._publisher is not None:
                self._publisher.close()
        return self.result

    # --------------------------------------------------------- helpers for
    # ----------------------------------------------------------- subclasses

    @property
    def elapsed(self) -> float:
        """Seconds since the test window was created."""
        return time.monotonic() - self._started_monotonic

    def after(self, ms: int, callback) -> None:
        """Schedule a callback that is cancelled automatically on finish."""
        self._after_jobs.append(self._root.after(ms, callback))

    def font(self, size: int = 0, bold: bool = True):
        """A Tk font tuple; ``size <= 0`` resolves the automatic base size."""
        if size <= 0:
            size = self.config.font_size or self._auto_font_size()
        return ("Helvetica", size, "bold" if bold else "normal")

    def record(self, **event) -> None:
        """Append one event (trial/response) and notify the GUI if attached."""
        event.setdefault("t", round(self.elapsed, 4))
        self.events.append(event)
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                pass  # A GUI sink must never break the test.

    def show_text(self, lines, y: float = 0.42, color: str = "", size: int = 0):
        """Centered multi-line text; returns the canvas item ids."""
        color = color or self.config.foreground
        height = self._canvas.winfo_height() or 700
        step = max(26, (self.font(size)[1] if size else self._auto_font_size()) + 16)
        first_y = height * y - step * (len(lines) - 1) / 2
        ids = [self._canvas.create_text(
            self._canvas.winfo_width() / 2, first_y + index * step,
            text=line, fill=color, font=self.font(size, bold=(index == 0)))
            for index, line in enumerate(lines)]
        return ids

    # -------------------------------------------------------- key/finish glue

    def _on_key(self, event) -> None:
        # Accept both the number row and the keypad variants.
        keysym = event.keysym
        if keysym.startswith("KP_"):
            keysym = keysym[3:]
        self._handle_key(keysym)

    def _on_escape(self, _event) -> None:
        # Leaving from the summary screen means the test ran to completion.
        self._finish(aborted=(self.state != "summary"))

    def _handle_key(self, keysym: str) -> None:
        """Override: react to a key press."""

    def _redraw_static(self) -> None:
        """Override: redraw size-dependent chrome after a window resize."""

    def _begin(self) -> None:
        """Override: show the first screen and start the state machine."""

    def _summary(self) -> dict:
        """Override: aggregate measures for the result."""
        return {"n_events": len(self.events)}

    def _finish(self, aborted: bool = False) -> None:
        if self.result is not None:
            return  # Already finished; ignore double Escapes / window closes.
        self._aborted = aborted
        for job in self._after_jobs:
            try:
                self._root.after_cancel(job)
            except Exception:
                pass
        self._after_jobs.clear()
        self.result = TestResult(
            test=self.NAME,
            config=self.config.to_dict(),
            events=list(self.events),
            summary=self._summary(),
            started=self._started_iso,
            duration=round(self.elapsed, 3),
            aborted=aborted,
            timeline=list(self.timeline),
        )
        if self.on_finish is not None:
            try:
                self.on_finish(self.result)
            except Exception:
                pass
        if self._root is not None:
            self._root.destroy()
        if self._publisher is not None:
            self._publisher.close()

    def _auto_font_size(self) -> int:
        height = self._root.winfo_height() if self._root else 0
        return max(18, min(48, height // 18))
