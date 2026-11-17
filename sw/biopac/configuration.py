"""Validated, portable GUI settings; hardware declarations are never readback."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import ipaddress
import json
import math
import os
from pathlib import Path

from gui_common.eda import ALGORITHMS
from .recording import Settings, write_json


@dataclass(frozen=True)
class ProcessingSettings:
    average: int = 1
    smoothing: float = 1.0
    prominence: float = 0.01
    algorithm: str = ALGORITHMS[1]

    @classmethod
    def parse(cls, values):
        if not isinstance(values, dict):
            raise ValueError("Processing settings must be a JSON object")
        average = int(values.get("average", 1))
        smoothing = float(values.get("smoothing", 1))
        prominence = float(values.get("prominence", 0.01))
        algorithm = values.get("algorithm", ALGORITHMS[1])
        if not 1 <= average <= 10001:
            raise ValueError("Moving average must be 1–10001 samples")
        if not math.isfinite(smoothing) or not 0 <= smoothing <= 1000:
            raise ValueError("Tonic smoothing must be finite and between 0 and 1000")
        if not math.isfinite(prominence) or prominence <= 0:
            raise ValueError("Peak prominence must be finite and positive")
        if algorithm not in ALGORITHMS:
            raise ValueError("Unknown analysis algorithm")
        return cls(average, smoothing, prominence, algorithm)


@dataclass(frozen=True)
class Configuration:
    interface: str = ""
    ip: str = ""
    output_dir: str = str(Path(__file__).parent / "outs")
    acquisition: Settings = field(default_factory=Settings)
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)
    span: float = 30.0

    @classmethod
    def parse(cls, values):
        if not isinstance(values, dict) or values.get("schema_version", 1) != 1:
            raise ValueError("Unsupported BIOPAC GUI configuration")
        interface, ip = str(values.get("interface", "")).strip(), str(values.get("ip", "")).strip()
        if ip:
            ip = str(ipaddress.IPv4Address(ip))
        output_dir = str(values.get("output_dir", cls().output_dir)).strip()
        span = float(values.get("span", 30))
        if not output_dir or not math.isfinite(span) or not 1 <= span <= 7200:
            raise ValueError("Choose an output directory and a 1–7200 second plot window")
        return cls(interface, ip, output_dir, Settings.parse(values.get("acquisition", {})),
                   ProcessingSettings.parse(values.get("processing", {})), span)

    def mapping(self):
        return {"schema_version": 1, **asdict(self)}


def default_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "heepidermis" / "biopac.json"


def read_configuration(path):
    return Configuration.parse(json.loads(Path(path).read_text()))


def write_configuration(path, configuration):
    write_json(path, Configuration.parse(configuration.mapping()).mapping())
