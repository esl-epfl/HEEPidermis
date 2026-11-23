"""Shimmer session data and persistence, independent of Tk and the link worker."""

from __future__ import annotations

from collections import deque
import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time

from ...common.recording import rename_recording
from .acquire import RATES, DEFAULT_RATE, adc_to_conductance, decode_gsr


@dataclass(frozen=True)
class Settings:
    rate: float = DEFAULT_RATE
    output_rate: float | None = None  # None keeps every hardware sample.
    device_name: str = ""
    address: str = ""

    @property
    def sample_rate(self):
        return self.rate if self.output_rate is None else self.output_rate

    @property
    def keep_ratio(self):
        from fractions import Fraction
        ratio = Fraction(str(self.sample_rate)) / Fraction(str(self.rate))
        return ratio.numerator, ratio.denominator

    @classmethod
    def parse(cls, values):
        if not isinstance(values, dict):
            raise ValueError("Acquisition settings must be a JSON object")
        rate = float(values.get("rate", DEFAULT_RATE))
        if rate not in RATES:
            raise ValueError(f"Choose a supported Shimmer rate: one of {RATES}")
        output = values.get("output_rate")
        if output is not None:
            output = float(output)
            if not math.isfinite(output) or not 0.1 <= output <= rate:
                raise ValueError("Output rate must be between 0.1 Hz and the device rate")
            if output == rate:
                output = None
        return cls(rate, output, str(values.get("device_name", "")), str(values.get("address", "")))

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
    kept_samples: int = 0
    time_anchor: float | None = None  # First device tick, in seconds.
    last_ticks: int | None = None  # Last unwrapped device tick seen (continuity).
    last_time_s: float | None = None  # Last assigned sample time, for gap bridging.


@dataclass(frozen=True)
class Sample:
    time_s: float
    index: int
    raw16: int
    segment: Segment

    @property
    def gsr_range(self):
        return decode_gsr(self.raw16)[0]

    @property
    def adc(self):
        return decode_gsr(self.raw16)[1]

    @property
    def conductance(self):
        """Skin conductance in µS, or None when the electrode input is open."""
        return adc_to_conductance(self.raw16)

    @property
    def open_circuit(self):
        return self.conductance is None


CSV_COLUMNS = ("sample_index", "time_s", "segment", "segment_sample_index", "sample_rate_hz",
               "gsr_raw16", "gsr_range", "gsr_adc", "conductance_us", "skin_resistance_ohm",
               "open_circuit", "device", "firmware", "hardware_sample_rate_hz")


def csv_row(index, sample):
    settings = sample.segment.settings
    conductance = sample.conductance
    resistance = None if conductance is None else 1e6 / conductance
    return (index, sample.time_s, sample.segment.number, sample.index, settings.sample_rate,
            sample.raw16, sample.gsr_range, sample.adc,
            "" if conductance is None else f"{conductance:.6f}",
            "" if resistance is None else f"{resistance:.1f}",
            int(sample.open_circuit), settings.device_name, "", settings.rate)


def write_json(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(values, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


TICK_HZ = 32768.0
# LogAndStream packets carry a 24-bit RTC counter in 32768 Hz ticks: it wraps
# to zero every 512 s, which used to send the timebase ~512 s into the past
# mid-recording (observed on Shimmer_6065 in outs/test_shimmer.csv).
TICK_WRAP = 1 << 24


def samples_from_block(segment, block):
    """Decoded Sample list for a LiveBlock, keeping device time when present.

    Timebase rules, in order of preference per sample:

    1. Device ticks, unwrapped across the 24-bit counter wraparound.
    2. The previous sample's time plus the nominal period, whenever the device
       timestamp does not advance.  Some Shimmer3 firmware versions repeat the
       same tick value on consecutive packets at low sampling rates (observed
       at 10.24 Hz: tick steps alternate 0/3200), and equal timestamps break
       the workspace plots and the CSV round-trip.
    3. The segment anchor plus the sample index over the rate, when the packet
       carries no timestamp at all.
    """
    rate = segment.settings.rate
    period = 1.0 / rate
    keep, total = segment.settings.keep_ratio
    first, end = block.first_sample, block.first_sample + len(block.samples)
    first_output = ((first - 1) * keep // total + 1) if first else 0
    last_output = (end - 1) * keep // total + 1
    # Hardware indices that survive output-rate decimation.
    wanted = {((k * total + keep - 1) // keep) for k in range(first_output, last_output)}
    result = []
    for offset, (ticks, raw) in enumerate(block.samples):
        index = first + offset
        if index not in wanted:
            continue
        seconds = None
        if ticks is not None:
            if segment.last_ticks is not None:
                # Unwrap: after the wrap the counter restarts near zero, so
                # ticks fall far below the previous value; lift them back.
                while ticks < segment.last_ticks - TICK_WRAP // 2:
                    ticks += TICK_WRAP
            # Timebase: device 32768 Hz clock, anchored on the first ticked
            # sample of the segment.  Indices count received packets, so a
            # dropped Bluetooth packet shifts time, not phase.
            if segment.time_anchor is None:
                segment.time_anchor = ticks / TICK_HZ
            seconds = segment.start_s + ticks / TICK_HZ - segment.time_anchor
            segment.last_ticks = ticks
        if seconds is None or (segment.last_time_s is not None
                               and seconds <= segment.last_time_s):
            # Repeated or missing device timestamp: bridge with the nominal
            # sample period so time never stalls or runs backwards.
            seconds = (segment.start_s + index * period if segment.last_time_s is None
                       else segment.last_time_s + period)
        segment.last_time_s = seconds
        result.append(Sample(seconds, index, raw, segment))
    return result


class SessionWriter:
    """Append CSV rows immediately; final verification is recorded per segment."""

    def __init__(self, directory, device, simulated=False):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "last_output.csv"
        self.csv_file = self.path.open("w", newline="")
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(CSV_COLUMNS)
        self.count = 0
        self.last_sync = time.monotonic()
        self.segments = []
        self.metadata = {
            "schema_version": 1, "backend": "simulated" if simulated else "shimmer_live_spp",
            "simulated": simulated, "device": device, "status": "recording", "segments": [],
            "timebase": "device 32768 Hz clock when streamed, else segment anchor + index / rate",
            "calibration": "Shimmer GSR+ range/ADC decode; see acquire.RF_OHMS and the GSR+ user guide",
        }
        try:
            self.csv_file.flush()
            self.save_metadata()
        except BaseException:
            self.close()
            raise

    def begin(self, segment):
        self.segments.append(segment)
        self.metadata["status"] = "recording"
        self.save_metadata()

    def append(self, segment, block):
        samples = samples_from_block(segment, block)
        for sample in samples:
            self.writer.writerow(csv_row(self.count, sample))
            self.count += 1
        segment.samples += len(block.samples)
        segment.kept_samples += len(samples)
        self.csv_file.flush()
        if time.monotonic() - self.last_sync >= 1:
            self.sync()

    def sync(self):
        if not self.csv_file.closed:
            self.csv_file.flush()
            os.fsync(self.csv_file.fileno())
        self.last_sync = time.monotonic()

    def rename(self, path):
        self.sync()
        self.save_metadata()
        self.path = rename_recording(self.path, path, (".json",))
        return self.path

    def finish(self, segment, error=None):
        self.sync()
        segment.ended, segment.verified, segment.error = True, error is None, error
        self.metadata["status"] = "failed" if error else "paused"
        self.save_metadata()

    def save_metadata(self):
        self.metadata["samples"] = self.count
        self.metadata["segments"] = [asdict(segment) for segment in self.segments]
        write_json(str(self.path) + ".json", self.metadata)

    def close(self):
        if not self.csv_file.closed:
            self.csv_file.close()


class Recording:
    """GUI-owned history. Previous samples always keep their original decode."""

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
        segment.samples = block.first_sample + len(block.samples)
        segment.kept_samples += len(samples)

    def finish(self, number, error=None):
        segment = self.segments[number]
        segment.ended, segment.verified, segment.error = True, error is None, error

    def markers(self):
        return [(segment.start_s, f"#{segment.number + 1} {segment.reason}")
                for segment in self.segments.values()]

    def invalid_spans(self):
        return [(segment.start_s, segment.start_s + segment.samples / segment.settings.rate)
                for segment in self.segments.values() if segment.error and segment.samples]


def moving_average(samples, window):
    """Moving-averaged conductance; open-circuit samples become NaN gaps."""
    if not isinstance(window, int) or window < 1:
        raise ValueError("Moving average window must be a positive integer")
    conductance, keys = [], []
    queue, total, previous = deque(), 0.0, None
    for sample in samples:
        key = sample.segment.number
        if key != previous or sample.open_circuit:
            queue.clear()
            total = 0.0
        previous = key
        value = sample.conductance
        if value is None:
            conductance.append(math.nan)
        else:
            queue.append(value)
            total += value
            if len(queue) > window:
                total -= queue.popleft()
            conductance.append(total / len(queue))
        keys.append(key)
    return conductance, keys


def load_recording(path):
    """Load a saved Shimmer CSV (+ .json sidecar), without touching hardware."""
    path = Path(path)
    metadata = {}
    sidecar = Path(str(path) + ".json")
    if sidecar.exists():
        metadata = json.loads(sidecar.read_text())
    result = Recording()
    previous_key, current = None, None
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            try:
                seconds = float(row["time_s"])
                raw16 = int(row["gsr_raw16"])
                if not math.isfinite(seconds) or seconds < 0 or not 0 <= raw16 <= 0xFFFF:
                    raise ValueError("Invalid timestamp or GSR word")
                source_id = int(row.get("segment", 0))
                rate = float(row.get("hardware_sample_rate_hz", DEFAULT_RATE))
                settings = Settings.parse({
                    "rate": rate,
                    "output_rate": (row.get("sample_rate_hz")
                                    if "hardware_sample_rate_hz" in row else None),
                    "device_name": row.get("device", metadata.get("device", {}).get("name", "")),
                })
                key = source_id, settings
                if key != previous_key:
                    current = Segment(len(result.segments), seconds, settings, reason="loaded", ended=True)
                    result.begin(current)
                    previous_key = key
                if result.times and seconds <= result.times[-1]:
                    raise ValueError("Timestamps are not strictly increasing")
                index = int(row.get("segment_sample_index", current.samples))
                result.samples.append(Sample(seconds, index, raw16, current))
                result.times.append(seconds)
                current.samples = max(current.samples, index + 1)
                current.kept_samples += 1
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(f"Invalid recording row {line}: {error}") from error
    if not result.samples:
        raise ValueError("The recording contains no GSR samples")
    return result, metadata


def export_recording(path, recording, metadata=None):
    path = Path(path)
    sidecar = Path(str(path) + ".json")
    if path.exists() or sidecar.exists():
        raise FileExistsError("Choose a new export filename; existing recordings are not overwritten")
    with path.open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_COLUMNS)
        for index, sample in enumerate(recording.samples):
            writer.writerow(csv_row(index, sample))
    write_json(sidecar, {**(metadata or {}), "schema_version": 1, "status": "exported",
                         "samples": len(recording.samples),
                         "segments": [asdict(segment) for segment in recording.segments.values()]})
