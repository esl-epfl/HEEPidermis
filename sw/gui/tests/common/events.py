"""Standard event feed shared with the measurement GUI (stdlib only).

Every test appends one JSON line per event to the feed file and prints the
same line to the terminal, so a run is observable from either place.  The
format is documented in ``sw/gui/tests/AGENTS.md``.
"""

import json
import os
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parents[2] / "outs/tests/events.jsonl"


class EventPublisher:
    """One append/write per event; concurrent test processes never truncate history."""

    def __init__(self, path=None):
        override = (os.environ.get("HEEPIDERMIS_TESTS_EVENTS")
                    or os.environ.get("HEEPIDERMIS_STROOP_EVENTS"))  # Legacy name.
        self.path = Path(path or override or DEFAULT_PATH).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

    def emit(self, event):
        data = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if os.write(self.fd, data) != len(data):
            raise OSError("Incomplete test event write")
        # The same packet on the terminal: a run stays readable without the GUI.
        print(data.decode("utf-8"), end="", flush=True)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
