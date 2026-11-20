"""Validated test events, append-only feed reader, and native CSV format.

Events follow the standard in ``sw/gui/tests/AGENTS.md``: schema_version 2
adds a per-event ``color`` (palette name or #rrggbb); schema_version 1
recordings (legacy Stroop feeds) still load and render as white.
"""

import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path


# The standard marker palette; tests may also send "#rrggbb" hex strings.
MARKER_COLORS = {"white": "#ffffff", "green": "#3fbf5f", "red": "#e04848",
                 "yellow": "#f2bd62", "blue": "#4fa3ff", "orange": "#f08020",
                 "purple": "#a050d0", "cyan": "#30b8c8"}

COLUMNS = ("time_s", "event_label", "event_color", "event_monotonic_s", "event_unix_s",
           "run_id", "event_sequence", "test", "event_details")
REQUIRED = ("time_s", "event_label", "event_monotonic_s", "event_unix_s", "run_id",
            "event_sequence", "test", "event_details")


def _valid_text(value):
    return isinstance(value, str) and 1 <= len(value) <= 64


def _valid_color(value):
    if not isinstance(value, str) or not value:
        return False
    return value in MARKER_COLORS or _valid_text(value)


@dataclass(frozen=True)
class Event:
    time_s: float
    label: str
    monotonic_s: float
    unix_s: float
    run_id: str
    sequence: int
    test: str
    details: dict
    color: str = "white"

    @classmethod
    def from_message(cls, message, origin):
        if not isinstance(message, dict) or message.get("schema_version") not in (1, 2):
            raise ValueError("Unsupported test event version")
        stamp, wall = float(message["monotonic_s"]), float(message["unix_s"])
        sequence, label = message["sequence"], message["label"]
        run_id, test, details = message["run_id"], message["test"], message.get("details", {})
        color = message.get("color", "white")  # Schema 1 events predate colors.
        if (not all(math.isfinite(v) for v in (stamp, wall, origin)) or
                not _valid_text(label) or not isinstance(sequence, int) or sequence < 0 or
                not isinstance(run_id, str) or not 1 <= len(run_id) <= 128 or
                not _valid_text(test) or not isinstance(details, dict) or
                not _valid_color(color)):
            raise ValueError("Invalid test event")
        return cls(stamp - origin, label, stamp, wall, run_id, sequence, test, details, color)

    def hex_color(self):
        """Resolved canvas color: palette name or passthrough #rrggbb."""
        if self.color in MARKER_COLORS:
            return MARKER_COLORS[self.color]
        return self.color if self.color.startswith("#") else "#ffffff"

    def row(self):
        return (self.time_s, self.label, self.color, self.monotonic_s, self.unix_s,
                self.run_id, self.sequence, self.test, json.dumps(self.details, ensure_ascii=False))


def read_recording(path):
    events, seen = [], set()
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not set(REQUIRED).issubset(reader.fieldnames or []):
            raise ValueError("Missing test event columns")
        for row in reader:
            stamp, elapsed = float(row["event_monotonic_s"]), float(row["time_s"])
            event = Event.from_message({"schema_version": 2, "monotonic_s": stamp,
                                        "unix_s": float(row["event_unix_s"]), "label": row["event_label"],
                                        "color": row.get("event_color") or "white",
                                        "run_id": row["run_id"], "sequence": int(row["event_sequence"]),
                                        "test": row["test"], "details": json.loads(row["event_details"])},
                                       stamp - elapsed)
            if elapsed < 0 or not math.isfinite(elapsed) or (event.run_id, event.sequence) in seen:
                raise ValueError("Invalid or duplicate test event")
            seen.add((event.run_id, event.sequence))
            # Preserve the saved relative time without subtracting large timestamps again.
            events.append(Event(elapsed, event.label, stamp, event.unix_s, event.run_id,
                                event.sequence, event.test, event.details, event.color))
    if not events:
        raise ValueError("The recording contains no test events")
    return sorted(events, key=lambda e: (e.time_s, e.sequence))


class EventTail:
    """Incremental JSONL reader: partial lines wait; rotation/truncation reopens."""

    def __init__(self, path):
        self.path, self.stream, self.identity = Path(path), None, None

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def poll(self):
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return []
        identity = (stat.st_dev, stat.st_ino)
        if self.stream is None or identity != self.identity or stat.st_size < self.stream.tell():
            self.close()
            self.stream = self.path.open("rb")
            self.identity = identity
        messages = []
        for _ in range(256):
            position = self.stream.tell()
            line = self.stream.readline(65537)
            if not line:
                break
            if len(line) > 65536:
                raise ValueError("Test event exceeds 64 KiB")
            if not line.endswith(b"\n"):
                self.stream.seek(position)
                break
            try:
                messages.append(json.loads(line))
            except (ValueError, UnicodeError):
                messages.append(None)  # Report malformed lines without losing following events.
        return messages
