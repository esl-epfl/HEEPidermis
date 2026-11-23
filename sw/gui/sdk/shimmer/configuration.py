"""Validated, portable GUI settings for the Shimmer SDK."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path

from .acquire import DEFAULT_RATE, RATES
from .recording import Settings, write_json


CONNECTION_MODES = ("auto", "dock", "bluetooth")
CONNECTION_LABELS = {"auto": "Auto — dock first, then Bluetooth", "dock": "USB dock (UART)",
                     "bluetooth": "Bluetooth"}
CONNECTION_VALUES = {label: value for value, label in CONNECTION_LABELS.items()}


@dataclass(frozen=True)
class Configuration:
    address: str = ""  # Bluetooth MAC or dock path; blank = auto-discover.
    name: str = ""
    connection: str = "auto"  # Transport to use: auto, dock or bluetooth.
    output_dir: str = str(Path(__file__).resolve().parents[2] / "outs" / "shimmer")
    acquisition: Settings = field(default_factory=Settings)
    moving_average: int = 1  # Display/export smoothing window in samples (1 = off).
    span: float = 30.0

    @classmethod
    def parse(cls, values):
        if not isinstance(values, dict) or values.get("schema_version", 1) != 1:
            raise ValueError("Unsupported Shimmer GUI configuration")
        address = str(values.get("address", "")).strip()
        name = str(values.get("name", "")).strip()
        connection = str(values.get("connection", "auto")).strip().lower()
        if connection not in CONNECTION_MODES:
            raise ValueError("Connection must be one of: " + ", ".join(CONNECTION_MODES))
        output_dir = str(values.get("output_dir", cls().output_dir)).strip()
        span = float(values.get("span", 30))
        if not output_dir or not math.isfinite(span) or not 1 <= span <= 7200:
            raise ValueError("Choose an output directory and a 1–7200 second plot window")
        moving_average = int(values.get("moving_average", 1))
        if not 1 <= moving_average <= 10001:
            raise ValueError("Moving average window must be 1–10001 samples")
        acquisition = values.get("acquisition", {})
        if not isinstance(acquisition, dict):
            raise ValueError("Acquisition settings must be a JSON object")
        if "rate" not in acquisition:
            acquisition = {**acquisition, "rate": DEFAULT_RATE}
        if acquisition.get("rate") not in RATES and float(acquisition.get("rate", 0)) not in RATES:
            raise ValueError(f"Choose a supported Shimmer rate: one of {RATES}")
        return cls(address, name, connection, output_dir, Settings.parse(acquisition),
                   moving_average, span)

    def mapping(self):
        return {"schema_version": 1, **asdict(self)}


def default_path():
    return Path(__file__).resolve().parents[2] / "configs" / "shimmer" / "last.json"


def read_configuration(path):
    return Configuration.parse(json.loads(Path(path).read_text()))


def write_configuration(path, configuration):
    write_json(path, Configuration.parse(configuration.mapping()).mapping())
