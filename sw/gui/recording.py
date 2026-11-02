"""Recording data, conductance conversion and motion screening, independent of Tk."""

from __future__ import annotations

from collections import deque
import csv
from dataclasses import dataclass
import math
from pathlib import Path
from statistics import median

from acquisition import DLCDecoder


DIFFERENTIAL_MODES = ("Hardware P−N", "Chip software P−N", "GUI P−N")


def recording_configuration_path(path):
    """Return the JSON sidecar used to preserve a recording's GUI settings."""
    path = Path(path)
    return path.with_suffix(path.suffix + ".configuration.json")


def recording_uart_path(path):
    """Return the text sidecar containing a recording's raw UART stream."""
    path = Path(path)
    return path.with_suffix(path.suffix + ".uart.log")


@dataclass
class Sample:
    time_s: float
    p_uv: int
    n_uv: int
    p_hz: int | None = None
    n_hz: int | None = None
    current_ua: float = 0.52
    source: str = "raw"
    segment: int = 0
    sample_hz: float = 10.0
    dlc_byte: int | None = None
    stored_bytes: int = 2
    tonic_us: float | None = None
    phasic_us: float | None = None
    phasic_peak: bool = False
    valid: bool = True  # Acquisition validity; never overwritten by motion screening.
    measured_delta_uv: float | None = None
    differential_mode: int = 0
    supply_hz: float = 10.0
    p_count: int | None = None
    n_count: int | None = None
    p_fine: int | None = None
    n_fine: int | None = None
    difference_counts: int | None = None
    dlc_kind: str | None = None
    difference_word: int | None = None
    difference_shift: int = 0
    motion_artifact: bool = False
    voltage_invalid: bool = False
    processing_start: int = 0
    current_code: int | None = None
    current_settling: bool = False
    dlc_level: int | None = None
    dlc_level_change: int = 0
    dlc_log_width: int | None = None
    dlc_discard_bits: int | None = None
    dlc_reference_p: bool | None = None
    buffer_loss: bool = False

    @property
    def raw_delta_uv(self):
        return self.p_uv - self.n_uv

    @property
    def dlc_amplitude_knot(self):
        return (self.dlc_byte not in (None, 0) and
                self.dlc_kind not in ("overflow", "time_overflow") and
                (self.dlc_level is None or self.dlc_level_change != 0))

    @property
    def delta_uv(self):
        return self.measured_delta_uv if self.measured_delta_uv is not None else self.raw_delta_uv

    @property
    def analysis_valid(self):
        return self.valid and not self.motion_artifact and not self.voltage_invalid and not self.current_settling and not self.buffer_loss

    @property
    def acquisition_key(self):
        return (self.segment, self.current_ua, self.sample_hz, self.supply_hz,
                self.source, self.differential_mode, self.valid)


def conductance_us(delta_uv, current_ua, offset_uv=0.0):
    """I / (P − N − offset), in µS. Reversed/zero drops are not measurements.

    offset_uv is a measured differential voltage offset, not an inferred tissue
    baseline. A nonzero offset requires calibration against a known load.
    """
    drop = delta_uv - offset_uv
    if not all(math.isfinite(x) for x in (drop, current_ua)) or drop <= 0 or current_ua <= 0:
        return None
    return current_ua * 1_000_000 / drop


def invalid_periods(samples):
    """Contiguous exclusions for plot shading and the overview timeline."""
    start = end = None
    for index, sample in enumerate(samples):
        if not sample.analysis_valid:
            tolerance = max(3.0, 2.5 / sample.sample_hz)
            next_time = samples[index + 1].time_s if index + 1 < len(samples) else sample.time_s + 1 / sample.sample_hz
            last = min(next_time, sample.time_s + tolerance)
            if start is None or sample.time_s > end:
                if start is not None:
                    yield start, end
                start = sample.time_s
            end = last
        elif start is not None:
            yield start, end
            start = end = None
    if start is not None:
        yield start, end


def buffer_loss_periods(samples):
    for sample in samples:
        if sample.buffer_loss:
            yield sample.time_s, sample.time_s + 1 / sample.sample_hz


def restore_buffer_loss(samples, uart_lines):
    references = iter(sample for sample in samples if sample.source == "dlc" and sample.dlc_byte is None)
    last_reference = None
    dlc_active = False
    for line in uart_lines:
        kind = line.split(",", 1)[0]
        if kind == "A":
            dlc_active = False
            last_reference = None
        elif kind == "B":
            dlc_active = True
        elif kind == "R" and dlc_active:
            last_reference = next(references, None)
        elif kind == "X" and last_reference is not None:
            last_reference.buffer_loss = True


def restore_dlc_metadata(samples, uart_lines):
    """Recover historical dLC levels and widths from a recording's UART log."""
    events = []
    decoder = None
    for line in uart_lines:
        fields = line.strip().split(",")
        try:
            if fields[0] == "A":
                decoder = None
            elif fields[0] == "B" and len(fields) in (10, 11, 12, 13, 14):
                decoder = DLCDecoder(int(fields[6]), int(fields[3]), int(fields[4]),
                                     int(fields[5]), int(fields[2]) / 1000, int(fields[7]),
                                     reference_p=len(fields) in (12, 13, 14))
            elif decoder is not None and fields[0] in ("R", "H"):
                if fields[0] == "R":
                    if len(fields) not in (13, 14):
                        continue
                    p_hz, n_hz = map(int, fields[5:7] if len(fields) == 14 else fields[4:6])
                elif len(fields) in (5, 6):
                    p_hz, n_hz = map(int, fields[2:4])
                elif len(fields) == 4:
                    n_hz = int(fields[2])
                    p_hz = round(n_hz + decoder.difference_hz)
                else:
                    continue
                events.append((None, p_hz, n_hz, decoder.level, 0,
                               decoder.log_width, decoder.discard_bits, decoder.reference_p))
            elif decoder is not None and fields[0] == "D" and len(fields) in (6, 7, 8):
                p_hz = int(fields[5] if len(fields) == 8 else fields[4]) if len(fields) != 6 else None
                n_hz = int(fields[6] if len(fields) == 8 else fields[5] if len(fields) == 7 else fields[4])
                for byte in bytes.fromhex(fields[-1]):
                    change = decoder.level_change(byte)
                    decoder.level += change
                    events.append((byte, p_hz, n_hz, decoder.level, change,
                                   decoder.log_width, decoder.discard_bits, decoder.reference_p))
        except (ValueError, IndexError):
            continue

    position = 0
    for sample in samples:
        if sample.source != "dlc" or sample.dlc_level is not None:
            continue
        for event_index in range(position, len(events)):
            byte, p_hz, n_hz, level, change, log_width, discard_bits, reference_p = events[event_index]
            if sample.dlc_byte != byte or sample.n_hz is not None and sample.n_hz != n_hz:
                continue
            if (byte is None or reference_p) and sample.p_hz is not None and sample.p_hz != p_hz:
                continue
            sample.dlc_level = level
            sample.dlc_level_change = change
            sample.dlc_log_width = log_width
            sample.dlc_discard_bits = discard_bits
            sample.dlc_reference_p = reference_p
            position = event_index + 1
            break


class MotionDetector:
    """Causal common-mode screen for correlated P/reference and N movement.

    A simultaneous step or a correlated trailing window with comparable channel
    amplitudes marks the current sample. Steps hold the exclusion for one window.
    Acquisition boundaries reset the detector; repeated dLC timestamps add no
    evidence. No raw values or acquisition validity are modified.
    """

    def __init__(self, enabled=True, window_s=2.0, threshold_uv=1000.0, correlation=0.8):
        self.enabled = enabled
        self.window_s = window_s
        self.threshold_uv = threshold_uv
        self.correlation = correlation
        self.points = deque()
        self.key = None
        self.hold_until = -math.inf
        self.previous = None
        self.window_artifact = False

    def classify(self, sample):
        if sample.acquisition_key != self.key:
            self.points.clear()
            self.hold_until = -math.inf
            self.previous = None
            self.window_artifact = False
            self.key = sample.acquisition_key
        if not self.enabled or not sample.valid:
            return False
        t, p, n = sample.time_s, sample.p_uv, sample.n_uv
        if self.previous and t - self.previous[0] > max(3.0, 2.5 / sample.sample_hz):
            self.points.clear()
            self.previous = None
            self.hold_until = -math.inf
            self.window_artifact = False
        while self.points and t - self.points[0][0] > self.window_s:
            self.points.popleft()
        if self.previous and t == self.previous[0]:
            return t < self.hold_until or self.window_artifact
        if self.previous:
            _, last_p, last_n = self.previous
            dp, dn = p - last_p, n - last_n
            if abs(dp) >= self.threshold_uv and 0.5 <= dn / dp <= 2.0:
                self.hold_until = t + self.window_s
        self.previous = (t, p, n)
        # Every raw step is inspected; correlation needs at most 65 time bins.
        if self.points and t - self.points[-1][0] < self.window_s / 64:
            return t < self.hold_until or self.window_artifact
        self.points.append((t, p, n))
        self.window_artifact = False
        if t < self.hold_until:
            return True
        if len(self.points) < 3:
            return False
        points = list(self.points)
        ps, ns = [v[1] for v in points], [v[2] for v in points]
        if max(ps) - min(ps) < self.threshold_uv:
            return False
        pm, nm = sum(ps) / len(ps), sum(ns) / len(ns)
        pp = sum((v - pm) ** 2 for v in ps)
        nn = sum((v - nm) ** 2 for v in ns)
        pn = sum((a - pm) * (b - nm) for a, b in zip(ps, ns))
        self.window_artifact = bool(pp and nn and 0.5 <= pn / pp <= 2.0 and pn / math.sqrt(pp * nn) >= self.correlation)
        return self.window_artifact


def read_recording(path, default_current_ua=0.52):
    """Read session and exported GUI CSVs, validating before replacing history."""
    samples = []
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"time_s", "vco_p_uV", "vco_n_uV"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError("Missing recording columns: " + ", ".join(sorted(missing)))
        for line, row in enumerate(reader, 2):
            def number(name, default=None, integer=False):
                value = row.get(name)
                if value is None or value == "":
                    return default
                result = float(value)
                if not math.isfinite(result) or integer and result != int(result):
                    raise ValueError(f"Invalid {name}")
                return int(result) if integer else result

            try:
                mode = row.get("differential_mode") or DIFFERENTIAL_MODES[0]
                mode = DIFFERENTIAL_MODES.index(mode) if mode in DIFFERENTIAL_MODES else int(mode)
                if mode not in range(3):
                    raise ValueError("Unknown differential mode")
                artifact = number("motion_artifact", 0, True)
                buffer_loss = number("buffer_loss", 0, True)
                settling = number("current_settling", 0, True)
                valid = number("acquisition_valid", None, True)
                if valid is None:
                    valid = number("valid", 1, True)
                    # New exports include acquisition_valid. This fallback also
                    # recovers a motion-only exclusion from intermediate files.
                    if artifact:
                        valid = 1
                reference_p = number("dlc_reference_p", integer=True)
                if reference_p not in (None, 0, 1):
                    raise ValueError("Invalid dlc_reference_p")
                sample = Sample(
                    number("time_s"), number("vco_p_uV", integer=True), number("vco_n_uV", integer=True),
                    number("vco_p_Hz", integer=True), number("vco_n_Hz", integer=True),
                    number("injected_current_uA", default_current_ua), row.get("source") or "raw", number("segment", 0, True),
                    number("sample_rate_Hz", 10.0), number("dlc_byte", integer=True),
                    number("stored_bytes", 2, True), valid=bool(valid), measured_delta_uv=number("delta_v_uV"),
                    differential_mode=mode, supply_hz=number("supply_sample_rate_Hz", number("sample_rate_Hz", 10.0)),
                    p_count=number("p_counter", integer=True), n_count=number("n_counter", integer=True),
                    p_fine=number("p_fine", integer=True), n_fine=number("n_fine", integer=True),
                    difference_counts=number("difference_phase_counts", integer=True),
                    difference_word=number("difference_word_int16", integer=True),
                    difference_shift=number("difference_word_shift", 0, True), dlc_kind=row.get("dlc_event_kind") or None,
                    motion_artifact=bool(artifact), current_code=number("idac_code", integer=True),
                    current_settling=bool(settling), dlc_level=number("dlc_level", integer=True),
                    dlc_level_change=number("dlc_level_change", 0, True),
                    dlc_log_width=number("dlc_log_width", integer=True),
                    dlc_discard_bits=number("dlc_discard_bits", integer=True),
                    dlc_reference_p=bool(reference_p) if reference_p is not None else None,
                    buffer_loss=bool(buffer_loss))
                if (sample.time_s is None or sample.p_uv is None or sample.n_uv is None or sample.current_ua is None or
                    sample.time_s < 0 or sample.current_ua <= 0 or sample.sample_hz <= 0 or sample.supply_hz <= 0 or
                    sample.stored_bytes < 0 or sample.source not in ("raw", "dlc") or
                    valid not in (0, 1) or artifact not in (0, 1) or settling not in (0, 1) or buffer_loss not in (0, 1) or
                    sample.current_code is not None and not 0 <= sample.current_code <= 255 or
                    sample.dlc_byte is not None and not 0 <= sample.dlc_byte <= 255 or
                    sample.dlc_log_width is not None and not 0 <= sample.dlc_log_width <= 15 or
                    sample.dlc_discard_bits is not None and not 0 <= sample.dlc_discard_bits <= 15):
                    raise ValueError("Invalid sample values")
                if samples and sample.time_s < samples[-1].time_s:
                    raise ValueError("Timestamps must be nondecreasing")
                sample.valid = sample.valid and all(hz is None or 24000 <= hz <= 1051710
                                                    for hz in (sample.p_hz, sample.n_hz))
                if sample.current_code is not None:
                    sample.valid = sample.valid and math.isclose(
                        sample.current_ua * 1000, sample.current_code * 40, abs_tol=1e-6)
                samples.append(sample)
            except (ValueError, TypeError) as error:
                raise ValueError(f"CSV row {line}: {error}") from error
    if not samples:
        raise ValueError("Recording contains no samples")
    # Old files did not store rate; infer it without including pauses.
    if "sample_rate_Hz" not in (reader.fieldnames or ()):
        intervals = [b.time_s - a.time_s for a, b in zip(samples, samples[1:]) if b.time_s > a.time_s]
        rate = 1 / median(intervals) if intervals else 10.0
        for sample in samples:
            sample.sample_hz = sample.supply_hz = rate
    uart_path = recording_uart_path(path)
    if uart_path.exists():
        uart_lines = uart_path.read_text(encoding="utf-8", errors="replace").splitlines()
        restore_buffer_loss(samples, uart_lines)
        if any(sample.source == "dlc" and sample.dlc_level is None for sample in samples):
            restore_dlc_metadata(samples, uart_lines)
    return samples
