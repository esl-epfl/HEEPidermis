"""Device-neutral time navigation and bounded-cost Tk trace rendering."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
import math
import tkinter as tk

from .theme import UI_ACCENT, UI_BORDER, UI_MUTED, UI_PLOT, UI_TEXT


@dataclass
class TimeViewport:
    span: float = 30.0
    start: float = 0.0
    latest: float = 0.0
    follow: bool = True
    cursor: float | None = None

    def update(self, latest):
        self.latest = max(0.0, latest)
        if self.follow:
            self.start = max(0.0, self.latest - self.span)
        else:
            self.start = max(0.0, min(self.start, max(0.0, self.latest - self.span)))

    def live(self):
        self.follow, self.cursor = True, None
        self.update(self.latest)

    def scroll(self, amount):
        self.follow = False
        self.start = max(0.0, min(self.start + amount * self.span * 0.12,
                                  max(0.0, self.latest - self.span)))

    def zoom(self, factor):
        end = self.start + self.span
        self.span = max(1.0, min(7200.0, self.span * factor))
        if not self.follow:
            self.start = max(0.0, end - self.span)
        self.update(self.latest)

    def fractions(self):
        total = max(self.latest, self.span)
        return self.start / total, min(1.0, (self.start + self.span) / total)

    def moveto(self, fraction):
        self.follow = False
        self.start = max(0.0, min(float(fraction) * max(self.latest, self.span),
                                  max(0.0, self.latest - self.span)))


@dataclass
class Trace:
    times: object
    values: object
    color: str
    name: str = ""
    right: bool = False
    segments: object = None
    width: float = 1.7


def envelope(times, values, start, span, pixels):
    """Keep first/last/min/max in temporal order per pixel, preserving narrow peaks."""
    selected = []
    bucket, indices = None, []

    def flush():
        if indices:
            chosen = {indices[0], indices[-1], min(indices, key=lambda i: values[i]),
                      max(indices, key=lambda i: values[i])}
            selected.extend(sorted(chosen))

    for i, t in enumerate(times):
        column = int((t - start) / span * pixels)
        if column != bucket:
            flush()
            bucket, indices = column, []
        indices.append(i)
    flush()
    return [(times[i], values[i]) for i in selected]


def ticks(low, high, count=5):
    raw = max(1e-12, (high - low) / count)
    power = 10 ** math.floor(math.log10(raw))
    step = next((m * power for m in (1, 2, 5, 10) if m * power >= raw), 10 * power)
    first = math.ceil(low / step) * step
    return [first + i * step for i in range(max(0, int((high - first) / step) + 1))]


class TimePlot(tk.Canvas):
    """A shared viewport with independent left/right y axes and segment breaks."""

    LEFT, RIGHT = 78, 76

    def __init__(self, master, viewport, title, unit, on_navigate, right_unit="", **kwargs):
        super().__init__(master, background=UI_PLOT, highlightthickness=1,
                         highlightbackground=UI_BORDER, **kwargs)
        self.viewport, self.title, self.unit = viewport, title, unit
        self.right_unit, self.on_navigate = right_unit, on_navigate
        self.traces, self.markers, self.invalid, self.peaks = [], [], [], []
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._cursor)
        self.bind("<B1-Motion>", self._cursor)
        self.bind("<Button-4>", lambda _e: self._wheel(-1))
        self.bind("<Button-5>", lambda _e: self._wheel(1))
        self.bind("<MouseWheel>", lambda e: self._wheel(-1 if e.delta > 0 else 1))

    def _cursor(self, event):
        width = max(1, self.winfo_width() - self.LEFT - self.RIGHT)
        self.viewport.cursor = self.viewport.start + max(0, min(width, event.x - self.LEFT)) / width * self.viewport.span
        self.viewport.follow = False
        self.on_navigate()

    def _wheel(self, delta):
        self.viewport.scroll(delta)
        self.on_navigate()
        return "break"

    def set_data(self, traces, markers=(), invalid=(), peaks=()):
        self.traces, self.markers, self.invalid, self.peaks = traces, markers, invalid, peaks
        self.redraw()

    def redraw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 200 or height < 80:
            return
        left, right, top, bottom = self.LEFT, width - self.RIGHT, 34, height - 28
        start, span = self.viewport.start, self.viewport.span
        end = start + span
        x = lambda t: left + (t - start) / span * (right - left)
        visible, ranges = [], {False: [], True: []}
        for trace in self.traces:
            first = max(0, bisect.bisect_left(trace.times, start) - 1)
            last = min(len(trace.times), bisect.bisect_right(trace.times, end) + 1)
            runs, run_t, run_y, previous = [], [], [], None
            for i in range(first, last):
                value = trace.values[i]
                segment = trace.segments[i] if trace.segments is not None else 0
                if not math.isfinite(value) or previous is not None and segment != previous:
                    if run_t:
                        runs.append((run_t, run_y))
                    run_t, run_y = [], []
                previous = segment
                if math.isfinite(value):
                    run_t.append(trace.times[i])
                    run_y.append(value)
            if run_t:
                runs.append((run_t, run_y))
            ranges[trace.right].extend(value for _, ys in runs for value in ys)
            visible.append((trace, runs))
        bounds = {}
        for axis, values in ranges.items():
            lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
            margin = max((hi - lo) * 0.07, abs(hi) * 0.001, 0.001)
            bounds[axis] = lo - margin, hi + margin
        y = lambda value, axis: bottom - (value - bounds[axis][0]) / (bounds[axis][1] - bounds[axis][0]) * (bottom - top)
        for a, b in self.invalid:
            if b >= start and a <= end:
                self.create_rectangle(x(max(a, start)), top, x(min(b, end)), bottom, fill="#442630", outline="")
        for t in self.peaks:
            if start <= t <= end:
                self.create_rectangle(x(max(start, t - 0.25)), top, x(min(end, t + 0.25)), bottom,
                                      fill="#17364c", outline="")
        for t in ticks(start, end, 7):
            px = x(t)
            self.create_line(px, top, px, bottom, fill="#18313d")
            self.create_text(px, bottom + 14, text=f"{t:g}", fill=UI_MUTED, font=("Helvetica", 8))
        for right_axis in (False, True):
            if right_axis and not self.right_unit:
                continue
            lo, hi = bounds[right_axis]
            for value in ticks(lo, hi):
                py = y(value, right_axis)
                if not right_axis:
                    self.create_line(left, py, right, py, fill="#18313d")
                self.create_text(right + 8 if right_axis else left - 8, py,
                                 text=f"{value:.4g}", anchor="w" if right_axis else "e",
                                 fill="#8eafff" if right_axis else UI_MUTED, font=("Helvetica", 8))
        for trace, runs in visible:
            for times, values in runs:
                points = envelope(times, values, start, span, max(1, right - left))
                coords = [coordinate for t, value in points for coordinate in (x(t), y(value, trace.right))]
                if len(coords) >= 4:
                    self.create_line(*coords, fill=trace.color, width=trace.width)
                elif coords:
                    px, py = coords
                    self.create_oval(px - 2, py - 2, px + 2, py + 2, fill=trace.color, outline="")
        for t, label in self.markers:
            if start <= t <= end:
                self.create_line(x(t), top, x(t), bottom, fill="#6c6977", dash=(3, 4))
        cursor = self.viewport.cursor
        if cursor is not None and start <= cursor <= end:
            self.create_line(x(cursor), top, x(cursor), bottom, fill=UI_TEXT, dash=(4, 3))
        self.create_text(12, 15, text=self.title, anchor="w", fill=UI_TEXT, font=("Helvetica", 10, "bold"))
        self.create_text(left - 8, top - 1, text=self.unit, anchor="se", fill=UI_MUTED, font=("Helvetica", 8))
        if self.right_unit:
            self.create_text(right + 8, top - 1, text=self.right_unit, anchor="sw", fill="#8eafff", font=("Helvetica", 8))
        labels = "  ·  ".join(trace.name for trace in self.traces if trace.name)
        self.create_text(right, 15, text=labels, anchor="e", fill=UI_ACCENT, font=("Helvetica", 8))
        if not ranges[False] and not ranges[True]:
            self.create_text((left + right) / 2, (top + bottom) / 2, text="Waiting for samples",
                             fill=UI_MUTED, font=("Helvetica", 11))
