"""Map device-relative samples to one host-monotonic workspace timeline."""

class AlignedSnapshot:
    def __init__(self, snapshot, offset):
        self.snapshot, self.offset = snapshot, offset
        self.latest = getattr(snapshot, "latest", 0.0) + offset
        self.revision = (getattr(snapshot, "revision", None), offset)
        self.signals = getattr(snapshot, "signals", None)

    def get_series(self, low=None, high=None):
        if low is None and high is None:
            series = self.snapshot.get_series()
        else:
            series = self.snapshot.get_series(None if low is None else low - self.offset,
                                              None if high is None else high - self.offset)
        return {key: {**data, "times": [stamp + self.offset for stamp in data["times"]]}
                for key, data in series.items()}

    def invalid_spans(self, low=None, high=None):
        method = getattr(self.snapshot, "invalid_spans", None)
        if method is None:
            return []
        return [(start + self.offset, end + self.offset) for start, end in method(
            None if low is None else low - self.offset, None if high is None else high - self.offset)]

    def get_markers(self, low, high):
        method = getattr(self.snapshot, "get_markers", None)
        if method is None:
            return []
        # Markers are (time, label, [color]) tuples; the color rides along.
        return [tuple((marker[0] + self.offset,) + tuple(marker[1:]))
                for marker in method(low - self.offset, high - self.offset)]
