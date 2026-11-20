"""Validated Stroop events, append-only feed reader, and native CSV format."""

import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path


LABELS = ("instructions", "calibration", "calibration end", "rest", "rest end",
          "test start", "stimulus", "correct", "wrong", "timeout", "test aborted", "test end")
COLUMNS = ("time_s", "event_label", "event_monotonic_s", "event_unix_s", "run_id",
           "event_sequence", "test", "event_details")


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

    @classmethod
    def from_message(cls, message, origin):
        if not isinstance(message, dict) or message.get("schema_version") != 1:
            raise ValueError("Unsupported Stroop event version")
        stamp, wall = float(message["monotonic_s"]), float(message["unix_s"])
        sequence, label = message["sequence"], message["label"]
        run_id, test, details = message["run_id"], message["test"], message.get("details", {})
        if (not all(math.isfinite(v) for v in (stamp, wall, origin)) or
                label not in LABELS or not isinstance(sequence, int) or sequence < 0 or
                not isinstance(run_id, str) or not 1 <= len(run_id) <= 128 or
                test not in ("stroop", "stroop-stress") or not isinstance(details, dict)):
            raise ValueError("Invalid Stroop event")
        return cls(stamp - origin, label, stamp, wall, run_id, sequence, test, details)

    def row(self):
        return (self.time_s, self.label, self.monotonic_s, self.unix_s,
                self.run_id, self.sequence, self.test, json.dumps(self.details, ensure_ascii=False))


def read_recording(path):
    events, seen = [], set()
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not set(COLUMNS).issubset(reader.fieldnames or []):
            raise ValueError("Missing Stroop event columns")
        for row in reader:
            stamp, elapsed = float(row["event_monotonic_s"]), float(row["time_s"])
            event = Event.from_message({"schema_version": 1, "monotonic_s": stamp,
                                       "unix_s": float(row["event_unix_s"]), "label": row["event_label"],
                                       "run_id": row["run_id"], "sequence": int(row["event_sequence"]),
                                       "test": row["test"], "details": json.loads(row["event_details"])},
                                      stamp - elapsed)
            if elapsed < 0 or not math.isfinite(elapsed) or (event.run_id, event.sequence) in seen:
                raise ValueError("Invalid or duplicate Stroop event")
            seen.add((event.run_id, event.sequence))
            # Preserve the saved relative time without subtracting large timestamps again.
            events.append(Event(elapsed, event.label, stamp, event.unix_s, event.run_id,
                                event.sequence, event.test, event.details))
    if not events:
        raise ValueError("The recording contains no Stroop events")
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
                raise ValueError("Stroop event exceeds 64 KiB")
            if not line.endswith(b"\n"):
                self.stream.seek(position)
                break
            try:
                messages.append(json.loads(line))
            except (ValueError, UnicodeError):
                messages.append(None)  # Report malformed lines without losing following events.
        return messages
