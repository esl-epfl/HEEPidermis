"""Prepare subject-named Timeseries files from raw GUI, PhysioNet or CASE recordings.

PhysioNet source: Hongn, Bosch, Prado & Bonomini (2025), version 1.0.1,
doi:10.13026/he0v-tf17. Tag interpretation follows the authors' published
Wearable_Dataset.ipynb. Dataset attribution and license: datasets/README.md.

CASE source: Sharma, Nordsletten & De Vos (2019), doi:10.6084/m9.figshare.8869157.
The interpolated GSR (1 kHz, µS) is decimated to 4 Hz and each emotional video
segment is labeled from its elicited emotion, filtered by continuous-arousal
agreement (datasets/README.md).
"""

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
import shutil

import numpy as np
from scipy.signal import decimate

from ..tools.timeseries import Timeseries
from ..tools.ts_params import (
    TSP_CHANNELS, TSP_CLASSES, TSP_CITATION, TSP_END_S, TSP_MAGNITUDE, TSP_SOURCE, TSP_SUBJECT, TSP_UNITS,
)
from .download_physionet import BASE, CITATION, ROOT, TEST_SUBJECTS, TRAIN_SUBJECTS


def annotations_from_intervals(data, intervals, classes):
    """Convert (start, end, class_index) intervals to a step-valued annotation Timeseries.

    Each class holds from its timestamp to the next timestamp. End markers
    explicitly restore -1 (unknown); annotation intervals are half-open.
    """
    events = {}
    previous_end = -np.inf
    for start, end, label in sorted(intervals):
        if not np.isfinite([start, end]).all() or start < previous_end or end <= start:
            raise ValueError("Annotation intervals must be finite, ordered and non-overlapping.")
        if label not in classes:
            raise ValueError(f"Undefined class index: {label}")
        events[start], events[end] = label, -1
        previous_end = end
    times = sorted(events)
    annotations = Timeseries("ann_" + data.name, [events[t] for t in times], times)
    annotations.params.update({TSP_SUBJECT: data.params[TSP_SUBJECT], TSP_CLASSES: dict(classes),
                               TSP_END_S: times[-1] if times else 0.})
    annotations.params[TSP_MAGNITUDE], annotations.params[TSP_UNITS] = "annotation", "class_index"
    return annotations


def _timestamp(value):
    try:
        return float(value)
    except ValueError:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc).timestamp() if stamp.tzinfo is None else stamp.timestamp()


def physionet_to_timeseries(folder, name=None):
    """Return (data, annotations): original EDA remains in µS until conversion."""
    folder = Path(folder)
    name = name or folder.name
    with (folder / "EDA.csv").open() as stream:
        origin = stream.readline().strip()
        hz = float(stream.readline().strip())
        values = np.loadtxt(stream, delimiter=",")
    if hz <= 0 or not np.isfinite(hz):
        raise ValueError("Invalid E4 sampling frequency.")
    params = {TSP_SUBJECT: name, TSP_SOURCE: BASE + f"Wearable_Dataset/STRESS/{folder.name}/EDA.csv",
              TSP_CITATION: CITATION, "Recording start (UTC)": origin,
              "License": "Open Data Commons Attribution License v1.0"}
    data = Timeseries(name, values, np.arange(len(values)) / hz)
    data.params.update(params)
    data.params[TSP_MAGNITUDE], data.params[TSP_UNITS] = "conductance", "uS"
    tags = [_timestamp(line.strip()) - _timestamp(origin) for line in (folder / "tags.csv").read_text().splitlines() if line.strip()]
    if len(tags) != 9 or np.any(np.diff(tags) <= 0):
        raise ValueError(f"Expected nine ordered v2 protocol tags for {name}.")
    # Zero-based original tags: baseline (0,1), task conditions (1,2), (3,4), (5,6), (7,8).
    intervals = [(tags[0], tags[1], 0)] + [(tags[a], tags[b], 1) for a, b in ((1, 2), (3, 4), (5, 6), (7, 8))]
    annotations = annotations_from_intervals(data, intervals, {0: "not stress", 1: "stress"})
    annotations.params.update({TSP_SOURCE: BASE + f"Wearable_Dataset/STRESS/{folder.name}/tags.csv",
                               TSP_CITATION: CITATION, "Label source": "Protocol tasks, not self-report thresholds",
                               "Class meaning": "stress vs not stress (baseline tasks); rest periods unannotated"})
    return data, annotations


def gui_to_timeseries(path, name):
    """Return voltage data and explicit MIST annotations; filename text is not a label."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    samples = [row for row in rows if row.get("workspace_kind", "sample") == "sample"
               and row.get("workspace_device", "heepidermis") == "heepidermis"]
    if not samples or not {"time_s", "vco_p_uV", "vco_n_uV"}.issubset(samples[0]):
        raise ValueError("No HEEPidermis samples with time_s, vco_p_uV and vco_n_uV.")
    # Duplicate dLC timestamps represent successive states; the last state is retained.
    samples = sorted({float(row["time_s"]): row for row in samples}.values(), key=lambda row: float(row["time_s"]))

    def numbers(key, default=np.nan):
        return np.array([float(row[key]) if row.get(key, "") != "" else default for row in samples])

    time = numbers("time_s")
    p, n = numbers("vco_p_uV"), numbers("vco_n_uV")
    difference = numbers("delta_v_uV") if "delta_v_uV" in samples[0] else p - n
    valid = np.ones(len(samples), dtype=bool)
    for key in ("valid", "acquisition_valid"):
        if key in samples[0]:
            valid &= numbers(key) == 1
    for key in ("motion_artifact", "current_settling", "buffer_loss"):
        valid &= numbers(key, 0.) == 0
    keys = ("segment", "injected_current_uA", "differential_mode", "sample_rate_Hz", "source")
    settings = [tuple(row.get(key, "") for key in keys) for row in samples]
    segments = np.cumsum([True] + [a != b for a, b in zip(settings, settings[1:])])
    channels = {}
    for key, values, magnitude, unit in (
        ("P", p, "voltage", "uV"), ("N", n, "voltage", "uV"),
        ("P_minus_N", p - n, "voltage", "uV"),
        ("current", numbers("injected_current_uA"), "current", "uA"),
        ("offset", numbers("voltage_offset_mV", 0.), "voltage", "mV"),
        ("valid", valid, "validity", "1"), ("segment", segments, "acquisition_segment", "index"),
    ):
        channel = Timeseries(name, values, time)
        channel.params[TSP_MAGNITUDE], channel.params[TSP_UNITS] = magnitude, unit
        channel.params.setdefault(TSP_SUBJECT, name)
        channels[key] = channel
    data = Timeseries(name, difference, time)
    data.params.update({TSP_SUBJECT: name, TSP_CHANNELS: channels, TSP_SOURCE: path.name,
                        "Differential": "Exported measured differential; raw P-N retained separately"})
    data.params[TSP_MAGNITUDE], data.params[TSP_UNITS] = "voltage", "uV"
    starts, intervals = {}, []
    events = [row for row in rows if row.get("workspace_kind", "sample") == "sample" and row.get("test") == "mist"]
    for row in sorted(events, key=lambda row: float(row["time_s"])):
        for phase, label in (("control", 0), ("stress", 1)):
            key = (row.get("run_id", ""), phase)
            if row.get("event_label") == phase + " start":
                starts[key] = float(row["time_s"])
            elif row.get("event_label") == phase + " end" and key in starts:
                intervals.append((starts.pop(key), float(row["time_s"]), label))
    annotations = annotations_from_intervals(data, intervals, {0: "not stress", 1: "stress"}) if intervals else None
    if annotations is not None:
        annotations.params[TSP_SOURCE] = path.name
    return data, annotations


def save_recording(data, annotations, directory):
    """Store data as <name>.pkl and annotations as ann_<name>.pkl."""
    if Path(data.name).name != data.name or data.name.startswith("ann_"):
        raise ValueError("Recording names must be plain filenames without the reserved ann_ prefix.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    data.dump(f"{directory}/")
    if annotations is not None:
        annotations.dump(f"{directory}/")


# CASE (Sharma et al. 2019): video-ID -> elicited-emotion label, from
# metadata/videos.xlsx (ID 9 is unused; 10/11/12 are start/blue/end screens).
CASE_VIDEO_NAMES = {1: "amusing-1", 2: "amusing-2", 3: "boring-1", 4: "boring-2",
                    5: "relaxed-1", 6: "relaxed-2", 7: "scary-1", 8: "scary-2",
                    10: "startVid", 11: "bluVid", 12: "endVid"}
CASE_STRESS_VIDEOS = (7, 8)            # scary: intended stress elicitation.
CASE_NOT_STRESS_VIDEOS = (3, 4, 5, 6)  # boring and relaxed videos.
CASE_TRAIN_SUBJECTS = tuple(f"sub_{number:02d}" for number in range(1, 25))
CASE_TEST_SUBJECTS = tuple(f"sub_{number:02d}" for number in range(25, 31))
CASE_CITATION = ("Castellini, C., Schwenker, F., Sharma, K., van den Broek, E. L., & "
                 "Albu-Schaeffer, A. (2019). CASE_Dataset-full. Figshare. "
                 "https://doi.org/10.6084/m9.figshare.8869157 "
                 "(data descriptor: Sharma, K., et al. (2019). Scientific Data, 6, 196. "
                 "https://doi.org/10.1038/s41597-019-0209-0). License: CC0.")
# Physiological GSR is logged at 1 kHz; the pipeline works at 4 Hz. Three
# zero-phase IIR decimation passes (1000 -> 100 -> 20 -> 4 Hz) avoid aliasing.
CASE_DECIMATION = (10, 5, 5)
CASE_SAMPLE_RATE_HZ = 4.
CASE_AROUSAL_RANGE = (0.5, 9.5)        # Joystick scale; its midpoint is 5.0.


def _case_segments(time_ms, video_ids):
    """Yield (start_ms, end_ms, video_id) for every constant-video-ID block."""
    boundaries = np.concatenate(([0], np.flatnonzero(np.diff(video_ids)) + 1, [len(video_ids)]))
    for start, stop in zip(boundaries[:-1], boundaries[1:]):
        # daqtime is in ms; the recording's half-open end sits one ms sample out.
        end_ms = time_ms[stop] if stop < len(time_ms) else time_ms[-1] + 1.
        yield time_ms[start], end_ms, int(video_ids[start])


def case_to_timeseries(physio_csv, annotation_csv, name, arousal_threshold=5.0):
    """Return (data, annotations) for one CASE participant.

    data is the GSR decimated from 1 kHz to 4 Hz (conductance, still in uS;
    siemens conversion happens downstream in data_to_conductance_S). The video
    ID (previous-value held) travels along as a 'video' channel for
    traceability. Annotations combine the elicited emotion of each video with
    the participant's continuous arousal: scary videos count as stress and
    boring/relaxed videos as not stress only when their segment-mean arousal
    agrees with the intended emotion (>= arousal_threshold for stress, below
    it for not stress; the scale runs 0.5-9.5, so the default 5.0 is the
    midpoint). Amusing videos and the start/blue/end screens stay unannotated.
    """
    physiological = np.loadtxt(physio_csv, delimiter=",", skiprows=1, usecols=(0, 3, 9))
    time_ms, gsr_uS, video_ids = (physiological[:, index] for index in range(3))
    if len(time_ms) < 2 or np.any(np.diff(time_ms) <= 0):
        raise ValueError(f"{physio_csv}: daqtime must be strictly increasing.")
    if not set(video_ids.astype(int)) <= set(CASE_VIDEO_NAMES):
        raise ValueError(f"{physio_csv}: unknown video IDs {set(video_ids.astype(int)) - set(CASE_VIDEO_NAMES)}")

    values = gsr_uS.astype(float)
    for factor in CASE_DECIMATION:
        values = decimate(values, factor, ftype="iir", zero_phase=True)
    time_s = time_ms[0] / 1000. + np.arange(len(values)) / CASE_SAMPLE_RATE_HZ
    # Previous-value hold of the video IDs on the decimated grid (250 ms steps).
    held = np.clip(np.searchsorted(time_ms, time_ms[0] + np.arange(len(values)) * 250., side="right") - 1,
                   0, len(time_ms) - 1)
    video_channel = Timeseries(name, video_ids[held].astype(int), time_s)
    video_channel.params[TSP_MAGNITUDE], video_channel.params[TSP_UNITS] = "stimulus", "video_id"

    data = Timeseries(name, values, time_s)
    data.params.update({TSP_SUBJECT: name, TSP_SOURCE: Path(physio_csv).name, TSP_CITATION: CASE_CITATION,
                        TSP_CHANNELS: {"video": video_channel},
                        "License": "CC0 (Figshare deposit of the CASE dataset)"})
    data.params[TSP_MAGNITUDE], data.params[TSP_UNITS] = "conductance", "uS"

    annotation = np.loadtxt(annotation_csv, delimiter=",", skiprows=1, usecols=(0, 2))
    annotation_ms, arousal = (annotation[:, index] for index in range(2))
    intervals = []
    for start_ms, end_ms, video_id in _case_segments(time_ms, video_ids):
        if video_id not in CASE_STRESS_VIDEOS + CASE_NOT_STRESS_VIDEOS:
            continue  # amusing, startVid, bluVid and endVid stay unannotated.
        inside = (annotation_ms >= start_ms) & (annotation_ms < end_ms)
        if not inside.any():
            continue  # No self-report in this segment; leave it unannotated.
        mean_arousal = float(np.mean(arousal[inside]))
        stress = video_id in CASE_STRESS_VIDEOS
        if stress == (mean_arousal >= arousal_threshold):
            intervals.append((start_ms / 1000., end_ms / 1000., int(stress)))
    annotations = None
    if intervals:
        annotations = annotations_from_intervals(data, intervals, {0: "not stress", 1: "stress"})
        annotations.params.update({TSP_SOURCE: Path(annotation_csv).name, TSP_CITATION: CASE_CITATION,
                                   "Label source": "Video emotion filtered by continuous-arousal agreement",
                                   "Class meaning": "stress = scary video with segment-mean arousal >= "
                                                    f"{arousal_threshold}; not stress = boring/relaxed video "
                                                    f"with segment-mean arousal < {arousal_threshold}",
                                   "Arousal threshold (0.5-9.5)": arousal_threshold})
    return data, annotations


def _case_interpolated(root):
    """Locate the extracted CASE data/interpolated directory (zip layouts vary)."""
    root = Path(root)
    found = sorted(root.glob("raw/case/extracted/*/data/interpolated")) \
        or sorted(root.glob("raw/case/extracted/data/interpolated"))
    if len(found) != 1:
        raise FileNotFoundError(
            f"CASE data not extracted under {root / 'raw/case/extracted'}; "
            "run: python -m sw.classification.datasets.download_case")
    return found[0]


def _case_csv(interpolated, kind, subject):
    """One participant's CSV; archive naming varies between sub_N and sub_0N."""
    directory = interpolated / kind
    for candidate in (f"{subject}.csv", f"sub_{int(subject.split('_')[1])}.csv"):
        if (directory / candidate).exists():
            return directory / candidate
    raise FileNotFoundError(f"No {kind} CSV for {subject} in {directory}")


def prepare_case(root=ROOT, arousal_threshold=5.0):
    """Convert all CASE participants; subjects 1-24 train, 25-30 test."""
    interpolated = _case_interpolated(root)
    for split, subjects in (("training", CASE_TRAIN_SUBJECTS), ("test", CASE_TEST_SUBJECTS)):
        for name in subjects:
            data, annotations = case_to_timeseries(
                _case_csv(interpolated, "physiological", name),
                _case_csv(interpolated, "annotations", name),
                name, arousal_threshold=arousal_threshold)
            save_recording(data, annotations, root / split)
            labeled = len(data.time) and annotations is not None
            print(f"{name}: {len(data.time)} samples @ {CASE_SAMPLE_RATE_HZ} Hz, "
                  f"{'%d labeled intervals' % len(annotations.data[annotations.data >= 0]) if labeled else 'no labels'}")
        print(f"Prepared {len(subjects)} CASE recordings in {root / split}")


def prepare_physionet(root=ROOT):
    for split, subjects in (("training", TRAIN_SUBJECTS), ("test", TEST_SUBJECTS)):
        for name in subjects:
            data, annotations = physionet_to_timeseries(root / "raw/physionet/Wearable_Dataset/STRESS" / name)
            save_recording(data, annotations, root / split)
        print(f"Prepared {len(subjects)} Timeseries/annotation pairs in {root / split}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="format", required=True)
    subcommands.add_parser("physionet")
    case = subcommands.add_parser("case")
    case.add_argument("--arousal-threshold", type=float, default=5.0,
                      help="Segment-mean arousal (0.5-9.5 scale) above which scary videos count as stress")
    gui = subcommands.add_parser("gui")
    gui.add_argument("csv", type=Path)
    gui.add_argument("--name", required=True, help="Subject identifier, optionally with a session suffix")
    gui.add_argument("--output", type=Path, default=ROOT / "validation")
    args = parser.parse_args()
    if args.format == "physionet":
        prepare_physionet()
    elif args.format == "case":
        prepare_case(arousal_threshold=args.arousal_threshold)
    else:
        raw = ROOT / "raw/gui" / args.csv.name
        raw.parent.mkdir(parents=True, exist_ok=True)
        if raw.resolve() != args.csv.resolve():
            if raw.exists() and raw.read_bytes() != args.csv.read_bytes():
                raise FileExistsError(f"A different raw recording already exists at {raw}")
            shutil.copy2(args.csv, raw)
        save_recording(*gui_to_timeseries(raw, args.name), args.output)
        print(f"Prepared {args.name} in {args.output}")
