"""dLC wire decoding and recording-state history, independent of Tk widgets."""

from dataclasses import dataclass


@dataclass
class DLCDecoder:
    level: int
    log_width: int
    time_bits: int
    discard_bits: int
    rate_hz: float
    n_hz: int
    ticks: int = 0
    sequence: int = 0
    reference_p: bool = False

    @property
    def difference_hz(self):
        return self.level * (1 << (self.log_width + self.discard_bits)) * self.rate_hz / 62

    def event_kind(self, byte, next_byte=None):
        """Classify only time and amplitude overflow packets."""
        amplitude_bits = 8 - self.time_bits
        mask = (1 << (amplitude_bits - 1)) - 1
        magnitude, ticks = byte & mask, byte >> amplitude_bits
        if not magnitude:
            return "time_overflow"
        direction = "down" if byte & (1 << (amplitude_bits - 1)) else "up"
        continued = (magnitude == mask and next_byte is not None and
                     next_byte >> amplitude_bits == 0 and next_byte & mask)
        if not ticks or continued:
            return f"amplitude_{direction}"
        return None

    def level_change(self, byte):
        amplitude_bits = 8 - self.time_bits
        magnitude = byte & ((1 << (amplitude_bits - 1)) - 1)
        return -magnitude if byte & (1 << (amplitude_bits - 1)) else magnitude

    def decode_events(self, payload):
        """Yield ticks, reconstructed frequencies, level and signed level change.

        RTL sign-magnitude layout is {dt, sign, magnitude}. Zero-magnitude
        packets extend time; zero-time packets continue a large crossing.
        The ADC decoder uses 62 phase counts per oscillator cycle.
        """
        amplitude_bits = 8 - self.time_bits
        for byte in payload:
            self.ticks += byte >> amplitude_bits
            change = self.level_change(byte)
            self.level += change
            if self.reference_p:
                yield self.ticks, self.n_hz, round(self.n_hz - self.difference_hz), self.level, change
            else:
                yield self.ticks, round(self.n_hz + self.difference_hz), self.n_hz, self.level, change

    def decode(self, payload):
        """Yield the legacy ticks/P/N tuple for every stored byte."""
        for ticks, p_hz, n_hz, _level, _change in self.decode_events(payload):
            yield ticks, p_hz, n_hz


class RecordingHistory:
    """Continuous status intervals, including periods with no incoming samples."""

    def __init__(self):
        self.spans = []
        self.status = "paused"
        self.start = 0.0
        self.end = 0.0

    def change(self, time_s, status):
        time_s = max(self.start, time_s)
        if status != self.status:
            if time_s > self.start:
                self.spans.append((self.start, time_s, self.status))
            self.start, self.status = time_s, status
        self.end = max(self.end, time_s)

    def intervals(self):
        return self.spans + [(self.start, self.end, self.status)]
