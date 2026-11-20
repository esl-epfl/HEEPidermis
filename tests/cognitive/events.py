"""Durable local event feed shared with the measurement GUI (stdlib only)."""

import json
import os
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parents[2] / "sw/gui/outs/stroop/events.jsonl"


class EventPublisher:
    """One append/write per event; concurrent test processes never truncate history."""

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("HEEPIDERMIS_STROOP_EVENTS", DEFAULT_PATH)).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

    def emit(self, event):
        data = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if os.write(self.fd, data) != len(data):
            raise OSError("Incomplete Stroop event write")

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
