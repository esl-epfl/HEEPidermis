"""One in-flight display job; acquisition never waits for display work."""

from concurrent.futures import Future
import threading


def submit(function, *args):
    """A daemon task with a Future, without an unbounded executor work queue."""
    future = Future()

    def run():
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)

    threading.Thread(target=run, name="plot-computation", daemon=True).start()
    return future


def compact_series(data, limit=2400):
    """Bound Tk work using ordered extrema; retain breaks between segments.

    Reduction is display-only: analysis and CSV use the original samples.
    """
    import math

    times, values = data["times"], data["values"]
    if len(times) <= limit:
        return data
    segments = data.get("segments")
    stride = max(1, (len(times) + limit // 4 - 1) // (limit // 4))
    selected = []
    for first in range(0, len(times), stride):
        last = min(len(times), first + stride)
        indices = range(first, last)
        finite = [i for i in indices if math.isfinite(values[i])]
        if len(finite) != last - first or (segments is not None and
                any(segments[i] != segments[first] for i in indices)):
            # A mixed bucket is a gap, never a line joining different runs.
            selected.append((times[first], math.nan, 0))
            continue
        keep = sorted({first, last - 1, min(finite, key=values.__getitem__),
                       max(finite, key=values.__getitem__)})
        selected.extend((times[i], values[i], segments[i] if segments is not None else 0)
                        for i in keep)
    return {**data, "times": [p[0] for p in selected],
            "values": [p[1] for p in selected], "segments": [p[2] for p in selected]}
