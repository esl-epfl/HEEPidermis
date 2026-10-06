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
        """Classify crossing direction and time/amplitude overflow continuations."""
        amplitude_bits = 8 - self.time_bits
        mask = (1 << (amplitude_bits - 1)) - 1
        magnitude, ticks = byte & mask, byte >> amplitude_bits
        if not magnitude or not ticks or (magnitude == mask and next_byte is not None and next_byte >> amplitude_bits == 0):
            return "overflow"
        return "down" if byte & (1 << (amplitude_bits - 1)) else "up"

    def decode(self, payload):
        """Yield (elapsed ticks, reconstructed P Hz, N Hz) for every stored byte.

        RTL sign-magnitude layout is {dt, sign, magnitude}. Zero-magnitude
        packets extend time; zero-time packets continue a large crossing.
        The ADC decoder uses 62 phase counts per oscillator cycle.
        """
        amplitude_bits = 8 - self.time_bits
        magnitude_mask = (1 << (amplitude_bits - 1)) - 1
        for byte in payload:
            magnitude = byte & magnitude_mask
            sign = (byte >> (amplitude_bits - 1)) & 1
            self.ticks += byte >> amplitude_bits
            self.level += -magnitude if sign else magnitude
            if self.reference_p:
                yield self.ticks, self.n_hz, round(self.n_hz - self.difference_hz)
            else:
                yield self.ticks, round(self.n_hz + self.difference_hz), self.n_hz


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
