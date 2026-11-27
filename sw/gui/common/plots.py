"""Device-neutral time navigation and bounded-cost Tk trace rendering."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
import math
import sys
import tkinter as tk
from tkinter import font as tkfont

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
    alpha: float = 1.0
    zorder: int = 0
    markers: bool = False
    steps: bool = False
    line: bool = True
    spline: bool = False


def blend(color: str, alpha: float, background: str = UI_PLOT) -> str:
    """Simulate transparency by blending the trace color with the plot canvas.

    Tk canvas items have no alpha channel; blending toward the background
    reproduces the visual result for a dark, fixed plot surface.
    """
    alpha = min(1.0, max(0.0, float(alpha)))
    if alpha >= 0.999:
        return color

    def channels(value):
        value = value.lstrip("#")
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

    try:
        fg, bg = channels(color), channels(background)
    except ValueError:
        return color
    mixed = tuple(round(f + (b - f) * (1 - alpha)) for f, b in zip(fg, bg))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


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


def spline_points(points, max_points=4000):
    """Catmull-Rom smoothing over (time, value) pairs; display-only.

    Passes exactly through every sample and densifies between samples, so a
    sparse signal reads as a continuous curve. The point budget bounds the
    canvas work regardless of zoom level; runs keep their real sample times
    and values untouched (axis scaling still uses the raw data).
    """
    count = len(points)
    if count < 3:
        return points
    divisions = max(2, min(8, max_points // count))
    smooth = [points[0]]
    for index in range(count - 1):
        p0 = points[index - 1] if index else points[index]
        p1, p2 = points[index], points[index + 1]
        p3 = points[index + 2] if index + 2 < count else points[index + 1]
        for step in range(1, divisions + 1):
            s = step / divisions
            s2, s3 = s * s, s * s * s
            smooth.append(tuple(
                0.5 * (2 * b + (c - a) * s
                       + (2 * a - 5 * b + 4 * c - d) * s2
                       + (3 * b - a - 3 * c + d) * s3)
                for a, b, c, d in zip(p0, p1, p2, p3)))
    return smooth


class TimePlot(tk.Canvas):
    """A shared viewport with independent left/right y axes and segment breaks."""

    LEFT, RIGHT = 78, 76

    def __init__(self, master, viewport, title, unit, on_navigate, right_unit="", **kwargs):
        super().__init__(master, background=UI_PLOT, highlightthickness=1,
                         highlightbackground=UI_BORDER, **kwargs)
        self.viewport, self.title, self.unit = viewport, title, unit
        self.right_unit, self.on_navigate = right_unit, on_navigate
        self.traces, self.markers, self.invalid, self.peaks = [], [], [], []
        self.annotations = []  # (time, value, label, right-axis) in data coordinates.
        self.annotation_lines = []  # (time, annotation, selected); vertical user markers.
        self.areas = []  # (start, end) full-height red highlights (e.g. POSITIVE).
        self.on_click = None  # Optional callback(time) on Button-1 press.
        self.fixed_bounds = {}  # Per-axis (lo, hi) when a row pins the range.
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._cursor)
        self.bind("<Button-4>", lambda _e: self._wheel(-1))
        self.bind("<Button-5>", lambda _e: self._wheel(1))
        self.bind("<MouseWheel>", lambda e: self._wheel(-1 if e.delta > 0 else 1))

    def set_units(self, unit=None, right_unit=None, *, redraw=True):
        """Update the axis unit labels; called when the stack content changes."""
        changed = False
        if unit is not None and unit != self.unit:
            self.unit, changed = unit, True
        if right_unit is not None and right_unit != self.right_unit:
            self.right_unit, changed = right_unit, True
        if changed and redraw:
            self.redraw()

    def _press(self, event):
        """Button-1 press: notify an optional click callback, then move the cursor."""
        if self.on_click is not None:
            try:
                width = max(1, self.winfo_width() - self.LEFT - self.RIGHT)
                stamp = self.viewport.start + max(0, min(width, event.x - self.LEFT)) / width * self.viewport.span
                self.on_click(stamp)
            except Exception as error:  # A click must never break plotting.
                print(f"[plot] click callback failed: {error}", file=sys.stderr)
        self._cursor(event)

    def _cursor(self, event):
        width = max(1, self.winfo_width() - self.LEFT - self.RIGHT)
        self.viewport.cursor = self.viewport.start + max(0, min(width, event.x - self.LEFT)) / width * self.viewport.span
        self.viewport.follow = False
        self.on_navigate()

    def _wheel(self, delta):
        self.viewport.scroll(delta)
        self.on_navigate()
        return "break"

    def set_data(self, traces, markers=(), invalid=(), peaks=(), annotations=(),
                 fixed_bounds=None, annotation_lines=(), areas=()):
        self.traces, self.markers, self.invalid, self.peaks = traces, markers, invalid, peaks
        self.annotations = list(annotations)
        self.annotation_lines = list(annotation_lines)
        self.areas = list(areas)   # Full-height (start, end) highlights, e.g. POSITIVE.
        self.fixed_bounds = dict(fixed_bounds or {})
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
            fixed = self.fixed_bounds.get(axis)
            if fixed and all(math.isfinite(v) for v in fixed) and fixed[1] > fixed[0]:
                bounds[axis] = float(fixed[0]), float(fixed[1])
                continue
            lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
            margin = max((hi - lo) * 0.07, abs(hi) * 0.001, 0.001)
            bounds[axis] = lo - margin, hi + margin
        y = lambda value, axis: bottom - (value - bounds[axis][0]) / (bounds[axis][1] - bounds[axis][0]) * (bottom - top)
        for a, b in self.invalid:
            if b >= start and a <= end:
                self.create_rectangle(x(max(a, start)), top, x(min(b, end)), bottom, fill="#442630", outline="")
        for a, b in self.areas:
            if b >= start and a <= end:
                self.create_rectangle(x(max(a, start)), top, x(min(b, end)), bottom, fill="#6e2222", outline="")
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
        ordered = sorted(visible, key=lambda item: item[0].zorder)
        # Event markers interleave with the traces by their zorder: lowering
        # the event row's zorder draws the lines behind the signals.
        markers = []
        for marker in self.markers:
            t, label = marker[0], marker[1]
            color = marker[2] if len(marker) > 2 else "#ffffff"
            zorder = marker[3] if len(marker) > 3 else 0
            if start <= t <= end:
                markers.append((t, label, color, zorder))
        if markers:
            threshold = min(z for _, _, _, z in markers)
            self._draw_traces([item for item in ordered if item[0].zorder < threshold],
                              x, y, start, span, right, left, top)
            self._draw_markers(markers, x, top, bottom, right, left)
            self._draw_traces([item for item in ordered if item[0].zorder >= threshold],
                              x, y, start, span, right, left, top)
        else:
            self._draw_traces(ordered, x, y, start, span, right, left, top)
        # White feature annotations at data coordinates; they move and zoom
        # with the signal because they are redrawn from (time, value) each frame.
        for t, value, label, right_axis in self.annotations:
            if not start <= t <= end:
                continue
            px, py = x(t), y(value, right_axis)
            near_right = px > right - 90
            self.create_text(px - 6 if near_right else px + 6, py - 1,
                             text=label, anchor="e" if near_right else "w",
                             fill="#ffffff", font=("Helvetica", 8, "bold"))
        # User annotations: vertical lines spanning the full plot height, with
        # their label at the bottom of the line. Line items keep a reference to
        # the annotation object, so label/color edits show up on a plain redraw.
        if self.annotation_lines:
            self._draw_annotation_lines(x, top, bottom, start, end)
        cursor = self.viewport.cursor
        if cursor is not None and start <= cursor <= end:
            self.create_line(x(cursor), top, x(cursor), bottom, fill=UI_TEXT, dash=(4, 3))
        self.create_text(12, 15, text=self.title, anchor="w", fill=UI_TEXT, font=("Helvetica", 10, "bold"))
        # Axis labels: magnitude name and unit, on the side of each axis.
        if self.unit:
            self.create_text(4, top - 3, text=self.unit, anchor="sw",
                             fill=UI_MUTED, font=("Helvetica", 8))
        if self.right_unit:
            self.create_text(width - 4, top - 3, text=self.right_unit, anchor="se",
                             fill="#8eafff", font=("Helvetica", 8))
        # Legend: one clear color swatch per signal, grouped by its axis side.
        legend_font = tkfont.Font(family="Helvetica", size=8)
        cursor_x = 16 + tkfont.Font(family="Helvetica", size=10, weight="bold").measure(self.title)
        for trace in self.traces:
            if trace.name and not trace.right:
                cursor_x = self._legend_entry(cursor_x, 15, trace, legend_font)
                if cursor_x > right - 140:
                    break
        for trace in reversed(self.traces):
            if trace.name and trace.right:
                cursor_x = self._legend_entry(width - 10, 15, trace, legend_font, "e")
                if cursor_x < left + 140:
                    break
        if not ranges[False] and not ranges[True] and not markers:
            message = ("No signals with data — add rows in SIGNALS or load a CSV"
                       if not self.traces and not self.annotation_lines else
                       "No data in the visible window — Live returns to the newest samples")
            self.create_text((left + right) / 2, (top + bottom) / 2, text=message,
                             fill=UI_MUTED, font=("Helvetica", 11))

    def _draw_traces(self, items, x, y, start, span, right, left, top):
        """Draw trace runs; items are (trace, runs) already in zorder order."""
        for trace, runs in items:
            fill = blend(trace.color, trace.alpha)
            for times, values in runs:
                points = envelope(times, values, start, span, max(1, right - left))
                if trace.spline:
                    points = spline_points(points)
                coords = [coordinate for t, value in points for coordinate in (x(t), y(value, trace.right))]
                if trace.steps and len(coords) >= 4:
                    stepped = coords[:2]
                    for index in range(2, len(coords), 2):
                        stepped.extend([coords[index], coords[index - 1], coords[index], coords[index + 1]])
                    coords = stepped
                if trace.line and len(coords) >= 4:
                    self.create_line(*coords, fill=fill, width=trace.width, tags="trace-line")
                elif trace.line and len(coords) == 2:
                    px, py = coords
                    self.create_oval(px - 2, py - 2, px + 2, py + 2, fill=fill, outline="")
                if trace.markers and points:
                    stride = max(1, len(points) // 400)
                    for t, value in points[::stride]:
                        px, py = x(t), y(value, trace.right)
                        self.create_oval(px - 2.5, py - 2.5, px + 2.5, py + 2.5,
                                         fill=fill, outline="")

    def _draw_markers(self, markers, x, top, bottom, right, left):
        """Vertical event lines with colored labels beside them."""
        marker_font = tkfont.Font(family="Helvetica", size=8, weight="bold")
        lanes = [left - 10] * max(1, min(4, int((bottom - top) / 18)))
        hidden_labels = 0
        for t, label, color, _zorder in sorted(markers, key=lambda m: m[3]):
            px = x(t)
            self.create_line(px, top, px, bottom, fill=color, tags="event-line")
            text_width = marker_font.measure(label)
            near_right = px + text_width + 6 > right
            a = px - text_width - 5 if near_right else px + 5
            lane = next((i for i, edge in enumerate(lanes) if a > edge + 4), None)
            if lane is None:
                hidden_labels += 1
                continue
            lanes[lane] = a + text_width
            self.create_text(px - 5 if near_right else px + 5, top + 3 + lane * 18,
                             text=label, anchor="ne" if near_right else "nw",
                             fill=color, font=marker_font, tags="event-label")
        if hidden_labels:
            self.create_text(right - 4, bottom - 4, anchor="se", fill="#ffffff",
                             text=f"Zoom in for {hidden_labels} more event labels", font=("Helvetica", 8))

    def _draw_annotation_lines(self, x, top, bottom, start, end):
        """Full-height annotation lines; each label sits left-aligned just to
        the right of its line, staggered into lanes to avoid overlaps."""
        font = tkfont.Font(family="Helvetica", size=8, weight="bold")
        gap = 4  # Space between the marker line and its label.
        lane_spans: list[list[tuple[float, float]]] = []  # Occupied x spans per label lane.
        for stamp, annotation, selected in sorted(self.annotation_lines, key=lambda item: item[0]):
            if not start <= stamp <= end:
                continue
            px = x(stamp)
            color = getattr(annotation, "color", "") or "#ffffff"
            self.create_line(px, top, px, bottom, fill=color,
                             width=3 if selected else 1)
            if selected:  # A soft double edge makes the selected marker stand out.
                self.create_line(px - 1.5, top, px - 1.5, bottom, fill=color)
                self.create_line(px + 1.5, top, px + 1.5, bottom, fill=color)
            label = str(getattr(annotation, "label", "") or "")
            if not label:
                continue
            left = px + gap
            right = left + font.measure(label)
            lane = next((i for i, spans in enumerate(lane_spans)
                         if all(left > b or right < a for a, b in spans)), None)
            if lane is None and len(lane_spans) < 3:
                lane = len(lane_spans)
            if lane is None:  # No room: keep a small tick at the bottom.
                self.create_rectangle(px - 2, bottom - 3, px + 2, bottom + 1,
                                      fill=color, outline="")
                continue
            if lane == len(lane_spans):
                lane_spans.append([])
            lane_spans[lane].append((left, right))
            self.create_text(left, bottom - 3 - lane * 12, text=label, anchor="sw",
                             fill=color, font=font)

    def _legend_entry(self, px, py, trace, font, anchor="w"):
        """Draw one legend entry (color box + name); returns the next edge x."""
        fill = blend(trace.color, trace.alpha)
        text_width = font.measure(trace.name)
        if anchor == "w":
            self.create_rectangle(px, py - 8, px + 9, py + 1, fill=fill, outline=UI_BORDER)
            self.create_text(px + 13, py - 4, text=trace.name, anchor="w",
                             fill=UI_ACCENT, font=font)
            return px + 13 + text_width + 16
        self.create_text(px, py - 4, text=trace.name, anchor="e", fill=UI_ACCENT, font=font)
        self.create_rectangle(px - text_width - 13, py - 8, px - text_width - 4, py + 1,
                              fill=fill, outline=UI_BORDER)
        return px - text_width - 13 - 16
