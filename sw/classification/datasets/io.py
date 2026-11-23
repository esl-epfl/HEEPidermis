"""Datasets contain only individual Timeseries recordings and annotation files."""

from pathlib import Path

from ..tools.timeseries import Timeseries
from ..tools.ts_params import TSP_CLASSES, TSP_MAGNITUDE, TSP_SUBJECT, TSP_UNITS


def load_dataset(directory):
    """Return two dictionaries of Timeseries, keyed by the corresponding data name."""
    directory = Path(directory)
    recordings, annotations = {}, {}
    for path in sorted(directory.glob("*.pkl")):
        ts = Timeseries.load(path)
        if path.stem != ts.name:
            raise ValueError(f"Filename and Timeseries name differ: {path.name}, {ts.name}")
        for key in (TSP_MAGNITUDE, TSP_UNITS, TSP_SUBJECT):
            if key not in ts.params:
                raise ValueError(f"Missing {key} in {path.name}")
        if ts.name.startswith("ann_"):
            if TSP_CLASSES not in ts.params:
                raise ValueError(f"Missing class index in {path.name}")
            annotations[ts.name[4:]] = ts
        else:
            recordings[ts.name] = ts
    if set(annotations) - set(recordings):
        raise ValueError("Annotation files without matching recordings.")
    if not recordings:
        raise ValueError(f"No prepared Timeseries recordings in {directory}")
    return recordings, annotations
