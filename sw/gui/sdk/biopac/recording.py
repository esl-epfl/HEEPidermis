"""BIOPAC session data and persistence, independent of Tk and the network worker."""

from __future__ import annotations

from collections import deque
import csv
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import struct
import tempfile

from .acquire import RATES, VOLTS_PER_COUNT
from .profiles import DEFAULT_PROFILE, get_profile
from ...common.recording import rename_recording
import time


_DEFAULT_PROFILE_VALUES = get_profile(DEFAULT_PROFILE)
# Same 1–9 steps per decade as HEEPidermis, up to this driver's hardware limit.
OUTPUT_RATES = tuple(float(f"{mantissa * 10 ** decade:g}")
                     for decade in range(-1, 3) for mantissa in range(1, 10)) + (1000.0,)


def acquisition_rate(output_rate):
    """Use the lowest validated hardware rate that can supply this output rate."""
    output_rate = float(output_rate)
    if not math.isfinite(output_rate) or not 0.1 <= output_rate <= max(RATES):
        raise ValueError("Choose an output rate between 0.1 and 1000 Hz")
    return next(rate for rate in RATES if rate >= output_rate)


@dataclass(frozen=True)
class Settings:
    channel: int = _DEFAULT_PROFILE_VALUES.channel
    rate: int = _DEFAULT_PROFILE_VALUES.rate
    sensitivity: float = _DEFAULT_PROFILE_VALUES.sensitivity
    zero_voltage: float = _DEFAULT_PROFILE_VALUES.zero_voltage
    filters: tuple = _DEFAULT_PROFILE_VALUES.filters
    profile: str = DEFAULT_PROFILE
    output_rate: float | None = None  # None preserves the original full-rate behavior.

    @property
    def sample_rate(self):
        return self.rate if self.output_rate is None else self.output_rate

    @property
    def keep_ratio(self):
        ratio = Fraction(str(self.sample_rate)) / self.rate
        return ratio.numerator, ratio.denominator

    @classmethod
    def from_profile(cls, profile):
        """Load a preset explicitly; parsing saved settings retains all overrides."""
        return cls.parse({"profile": profile})

    @classmethod
    def parse(cls, values):
        if not isinstance(values, dict):
            raise ValueError("Acquisition settings must be a JSON object")
        profile_key = values.get("profile", DEFAULT_PROFILE)
        profile = get_profile(profile_key)
        channel, rate = int(values.get("channel", profile.channel)), int(values.get("rate", profile.rate))
        sensitivity = float(values.get("sensitivity", profile.sensitivity))
        zero = float(values.get("zero_voltage", profile.zero_voltage))
        filter_values = values.get("filters", profile.filters)
        if not isinstance(filter_values, (list, tuple)):
            raise ValueError("Enter three physical filter settings")
        filters = tuple(str(x).strip() for x in filter_values)
        if channel not in range(1, 17) or rate not in RATES:
            raise ValueError("Choose channel 1–16 and a supported sample rate")
        if not math.isfinite(sensitivity) or sensitivity <= 0 or not math.isfinite(zero):
            raise ValueError("Sensitivity must be positive and finite; calibrated zero must be finite")
        if len(filters) != 3 or any(not value or len(value) > 40 for value in filters):
            raise ValueError("Enter all three physical filter settings (maximum 40 characters each)")
        output = values.get("output_rate")
        if output is not None:
            output = float(output)
            if not math.isfinite(output) or not 0.1 <= output <= rate:
                raise ValueError("Output rate must be between 0.1 Hz and the hardware rate")
            if output == rate:
                output = None
        return cls(channel, rate, sensitivity, zero, filters, profile_key, output)

    def mapping(self):
        return asdict(self)


@dataclass
class Segment:
    number: int
    start_s: float
    settings: Settings
    reason: str = "record"
    started_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    samples: int = 0
    verified: bool = False
    ended: bool = False
    error: str | None = None
    raw_offset: int = 0
    kept_samples: int = 0


@dataclass(frozen=True)
class Sample:
    time_s: float
    index: int
    adc_count: int
    segment: Segment

    @property
    def voltage(self):
        return self.adc_count * VOLTS_PER_COUNT

    @property
    def conductance(self):
        settings = self.segment.settings
        return (self.voltage - settings.zero_voltage) * settings.sensitivity

    @property
    def clipped(self):
        return self.adc_count in (-32768, 32767)


def samples_from_block(segment, block):
    if block.first_sample != segment.samples or len(block.raw) % 2:
        raise ValueError("A sample block is missing, duplicated or out of order")
    rate = segment.settings.rate
    keep, total = segment.settings.keep_ratio
    # Absolute input indices determine the phase, independently of UDP block
    # boundaries. ceil(k * total / keep) spreads retained samples evenly.
    first, end = block.first_sample, block.first_sample + block.count
    first_output = ((first - 1) * keep // total + 1) if first else 0
    last_output = (end - 1) * keep // total + 1
    indices = ((k * total + keep - 1) // keep for k in range(first_output, last_output))
    return [Sample(segment.start_s + index / rate, index,
                   struct.unpack_from(">h", block.raw, 2 * (index - first))[0], segment)
            for index in indices]


CSV_COLUMNS = ("sample_index", "time_s", "segment", "segment_sample_index", "channel",
               "sample_rate_hz", "adc_count", "voltage_v_nominal", "conductance_us_nominal",
               "sensitivity_us_per_v", "zero_voltage_v", "filter_1", "filter_2", "filter_3", "adc_clipped",
               "hardware_profile", "hardware_sample_rate_hz")


def csv_row(index, sample):
    settings = sample.segment.settings
    return (index, sample.time_s, sample.segment.number, sample.index, settings.channel,
            settings.sample_rate, sample.adc_count, sample.voltage, sample.conductance,
            settings.sensitivity, settings.zero_voltage, *settings.filters, int(sample.clipped), settings.profile,
            settings.rate)


def profile_defaults(segments):
    """Archive the nominal presets separately from each segment's applied settings."""
    return {s.settings.profile: asdict(get_profile(s.settings.profile)) for s in segments}


def write_json(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(values, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class SessionWriter:
    """Append raw data immediately; final verification is recorded per segment."""

    def __init__(self, directory, device, simulated=False):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "last_output.csv"
        self.csv_file = self.path.open("w", newline="")
        self.raw_file = None
        self.trace_file = None
        try:
            self.raw_file = Path(str(self.path) + ".raw").open("wb")
            self.trace_file = Path(str(self.path) + ".udp.jsonl").open("w")
        except BaseException:
            self.close()
            raise
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(CSV_COLUMNS)
        self.count = 0
        self.last_sync = time.monotonic()
        self.segments = []
        self.metadata = {
            "schema_version": 2, "backend": "simulated" if simulated else "native_mp160_live_udp",
            "simulated": simulated, "device": device, "status": "recording", "segments": [],
            "timebase": "segment host anchor + sample index / nominal device rate; gaps retained",
            "calibration": "Nominal ±10 V ADC conversion; gain, zero and filters entered manually",
            "live_samples": "Provisional until each segment matches its final frozen buffer",
            "downsampling": "CSV retains evenly spaced original samples at output_rate; raw sidecar contains every hardware sample",
        }
        try:
            self.csv_file.flush()
            self.save_metadata()
        except BaseException:
            self.close()
            raise

    def trace(self, event):
        self.trace_file.write(json.dumps(event) + "\n")
        self.trace_file.flush()

    def begin(self, segment):
        segment.raw_offset = self.raw_file.tell()
        self.segments.append(segment)
        self.metadata["status"] = "recording"
        self.save_metadata()

    def append(self, segment, block):
        samples = samples_from_block(segment, block)
        for sample in samples:
            self.writer.writerow(csv_row(self.count, sample))
            self.count += 1
        self.raw_file.write(block.raw)
        segment.samples += block.count
        segment.kept_samples += len(samples)
        self.csv_file.flush()
        self.raw_file.flush()
        if time.monotonic() - self.last_sync >= 1:
            self.sync()

    def sync(self):
        for stream in (self.csv_file, self.raw_file, self.trace_file):
            if stream is not None and not stream.closed:
                stream.flush()
                os.fsync(stream.fileno())
        self.last_sync = time.monotonic()

    def rename(self, path):
        self.sync()
        self.save_metadata()
        self.path = rename_recording(self.path, path, (".raw", ".udp.jsonl", ".json"))
        return self.path

    def finish(self, segment, error=None):
        self.sync()
        segment.ended, segment.verified, segment.error = True, error is None, error
        self.metadata["status"] = "failed" if error else "paused"
        self.save_metadata()

    def save_metadata(self):
        self.metadata["samples"] = self.count
        self.metadata["hardware_samples"] = sum(segment.samples for segment in self.segments)
        self.metadata["segments"] = [asdict(segment) for segment in self.segments]
        self.metadata["hardware_profile_defaults"] = profile_defaults(self.segments)
        write_json(str(self.path) + ".json", self.metadata)

    def close(self):
        for name in ("trace_file", "raw_file", "csv_file"):
            stream = getattr(self, name, None)
            if stream is not None:
                stream.close()


class Recording:
    """GUI-owned history. Previous samples always keep their original calibration."""

    def __init__(self):
        self.samples, self.times, self.segments = [], [], {}

    def begin(self, segment):
        if segment.number in self.segments:
            raise ValueError("Duplicate recording segment")
        self.segments[segment.number] = segment

    def append(self, number, block):
        segment = self.segments[number]
        samples = samples_from_block(segment, block)
        if samples and self.times and samples[0].time_s <= self.times[-1]:
            raise ValueError("Recording timestamps must increase across acquisition segments")
        self.samples.extend(samples)
        self.times.extend(sample.time_s for sample in samples)
        segment.samples += block.count
        segment.kept_samples += len(samples)

    def finish(self, number, error=None):
        segment = self.segments[number]
        segment.ended, segment.verified, segment.error = True, error is None, error

    def markers(self):
        return [(segment.start_s, f"#{segment.number + 1} {segment.reason}") for segment in self.segments.values()]

    def invalid_spans(self):
        spans = []
        for segment in self.segments.values():
            if segment.error and segment.samples:
                spans.append((segment.start_s, segment.start_s + segment.samples / segment.settings.rate))
        for sample in self.samples:
            if sample.clipped:
                spans.append((sample.time_s, sample.time_s + 1 / sample.segment.settings.sample_rate))
        return spans


def moving_average(samples, window):
    if not isinstance(window, int) or window < 1:
        raise ValueError("Moving average window must be a positive integer")
    volts, conductance, keys = [], [], []
    queue, total_v, total_g, previous = deque(), 0.0, 0.0, None
    for sample in samples:
        key = sample.segment.number
        if key != previous or sample.clipped:
            queue.clear()
            total_v = total_g = 0.0
        previous = key
        if sample.clipped:
            volts.append(math.nan)
            conductance.append(math.nan)
        else:
            v, g = sample.voltage, sample.conductance
            queue.append((v, g))
            total_v += v
            total_g += g
            if len(queue) > window:
                old_v, old_g = queue.popleft()
                total_v -= old_v
                total_g -= old_g
            volts.append(total_v / len(queue))
            conductance.append(total_g / len(queue))
        keys.append(key)
    return volts, conductance, keys


def load_recording(path):
    """Load GUI sessions and the original batch CLI CSVs, without touching hardware."""
    path = Path(path)
    metadata = {}
    sidecar = Path(str(path) + ".json")
    if sidecar.exists():
        metadata = json.loads(sidecar.read_text())
    old_segments = {int(item["number"]): item for item in metadata.get("segments", [])}
    result = Recording()
    saved_counts = {}
    previous_key, current = None, None
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            try:
                seconds = float(row.get("time_s", row.get("time_s_nominal", "")))
                count = int(row["adc_count"])
                if not math.isfinite(seconds) or seconds < 0 or not -32768 <= count <= 32767:
                    raise ValueError("Invalid timestamp or ADC count")
                source_id = int(row.get("segment", 0))
                saved = old_segments.get(source_id, {})
                saved_settings = saved.get("settings", {})
                profile_key = row.get("hardware_profile", saved_settings.get("profile",
                                      metadata.get("hardware_profile", DEFAULT_PROFILE)))
                profile = get_profile(profile_key)
                hardware_rate = row.get("hardware_sample_rate_hz", saved_settings.get("rate",
                    row.get("sample_rate_hz", metadata.get("sample_rate_hz_nominal", profile.rate))))
                settings = Settings.parse({
                    "profile": profile_key,
                    "channel": row.get("channel", saved_settings.get("channel", metadata.get("channel", profile.channel))),
                    "rate": hardware_rate,
                    "output_rate": (row.get("sample_rate_hz") if "hardware_sample_rate_hz" in row
                                    else saved_settings.get("output_rate")),
                    "sensitivity": row.get("sensitivity_us_per_v", saved_settings.get("sensitivity",
                                           metadata.get("sensitivity_us_per_v_manual", profile.sensitivity))),
                    "zero_voltage": row.get("zero_voltage_v", saved_settings.get("zero_voltage",
                                            metadata.get("calibrated_zero_voltage_v", profile.zero_voltage))),
                    "filters": tuple(row.get(f"filter_{i + 1}", value) for i, value in enumerate(
                        saved_settings.get("filters", metadata.get("hardware_filters_manual", profile.filters)))),
                })
                key = source_id, settings
                if key != previous_key:
                    current = Segment(len(result.segments), seconds, settings,
                                      reason=saved.get("reason", "loaded"),
                                      verified=saved.get("verified", metadata.get("frozen_buffer_verified", False)),
                                      ended=True, error=saved.get("error"))
                    result.begin(current)
                    saved_counts[current.number] = int(saved.get("samples", 0))
                    previous_key = key
                if result.times and seconds <= result.times[-1]:
                    raise ValueError("Timestamps are not strictly increasing")
                index = int(row.get("segment_sample_index", current.samples))
                if index < current.samples:
                    raise ValueError("Sample indices are not increasing within the segment")
                result.samples.append(Sample(seconds, index, count, current))
                result.times.append(seconds)
                current.samples = index + 1
                current.kept_samples += 1
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(f"Invalid recording row {line}: {error}") from error
    if not result.samples:
        raise ValueError("The recording contains no ADC samples")
    for number, segment in result.segments.items():
        segment.samples = max(segment.samples, saved_counts[number])
    return result, metadata


def export_recording(path, recording, processing, metadata=None):
    path = Path(path)
    sidecar = Path(str(path) + ".json")
    if path.exists() or sidecar.exists():
        raise FileExistsError("Choose a new export filename; existing recordings are not overwritten")
    with path.open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_COLUMNS)
        for index, sample in enumerate(recording.samples):
            writer.writerow(csv_row(index, sample))
    write_json(sidecar, {**(metadata or {}), "schema_version": 2, "status": "exported",
                        "samples": len(recording.samples), "processing": processing,
                        "hardware_profile_defaults": profile_defaults(recording.segments.values()),
                        "segments": [asdict(segment) for segment in recording.segments.values()]})
