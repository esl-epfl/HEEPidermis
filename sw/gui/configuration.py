"""Versioned, validated GUI configurations with atomic JSON writes."""

import json
import math
from pathlib import Path


def validate_configuration(values, choices, rate_count):
    if not isinstance(values, dict):
        raise ValueError("Configuration must be a JSON object")
    if values.get("version", 1) != 1:
        raise ValueError("Unsupported configuration version")
    result = {}
    ranges = {
        "mcu_freq": (1, 1_000_000_000, True), "baud": (1, 100_000_000, True),
        "current_code": (1, 255, True), "sampling_index": (0, rate_count - 1, True),
        "supply_index": (0, rate_count - 1, True), "dlc_time_bits": (1, 6, True),
        "filter_window": (1, 100000, True), "rate_window": (2, 100000, True),
        "smoothing": (0, 1000, False), "peak_prominence": (0.000001, 1000, False),
        "motion_window": (0.1, 30, False), "motion_threshold_mv": (0.001, 100, False),
        "motion_correlation": (0.1, 1, False), "voltage_offset_mv": (-500, 500, False),
        "view_seconds": (0.1, 86400 * 365, False), "dlc_width": (1, 32768, True),
        "dlc_buffer_events": (1, 1024, True),
    }
    for name, value in values.items():
        if name == "version":
            continue
        if name in ranges:
            low, high, integer = ranges[name]
            number = float(value)
            if not math.isfinite(number) or not low <= number <= high or integer and number != int(number):
                raise ValueError(f"Invalid {name}: expected {low}–{high}")
            if name == "dlc_width" and int(number) & (int(number) - 1):
                raise ValueError("dLC level width must be a power of two")
            result[name] = int(number) if integer else number
        elif name in ("use_dlc", "compare_dlc", "supply_follow", "motion_enabled"):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false")
            result[name] = value
        elif name in choices:
            if value not in choices[name]:
                raise ValueError(f"Unknown {name}: {value}")
            result[name] = value
        elif name == "port":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Serial port must be nonempty")
            result[name] = value
    mode = result.get("differential_mode")
    if mode == "GUI P−N":
        result["use_dlc"] = False
    return result


def write_configuration(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"version": 1, **values}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_configuration(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
