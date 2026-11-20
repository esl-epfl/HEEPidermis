"""One workspace CSV and JSON sidecar, preserving native samples and signal rows.

CSV uses a union of native device columns. `workspace_kind` distinguishes native
samples from calculated signals, which keep their own timestamps (no resampling).
All functions here run without Tk or acquisition-worker access.
"""

import csv
import json
import os
from pathlib import Path
import tempfile


FORMAT = "heepidermis-workspace-recording"
PREFIX = ("workspace_kind", "workspace_device", "workspace_signal", "workspace_row",
          "workspace_value", "workspace_unit", "workspace_segment")


def metadata_path(path):
    return Path(str(path) + ".workspace.json")


def write_session(path, configuration, recordings, signals):
    """Publish a complete snapshot. Existing recordings are never overwritten."""
    path = Path(path)
    sidecar = metadata_path(path)
    if path.exists() or sidecar.exists():
        raise FileExistsError("Choose a new filename; a workspace recording already exists there.")
    columns = [*PREFIX, "workspace_label"]
    for recording in recordings.values():
        columns.extend(name for name in recording["columns"] if name not in columns)
    if "time_s" not in columns:
        columns.append("time_s")
    metadata = {"format": FORMAT, "version": 1, "configuration": configuration,
                "recordings": {key: {name: value for name, value in recording.items() if name != "rows"}
                               for key, recording in recordings.items()}}
    # Stage both files before publishing either. Hard links provide no-clobber
    # publication even if another save chooses the same destination concurrently.
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".workspace-") as folder:
        staged_csv, staged_json = Path(folder) / "data.csv", Path(folder) / "data.json"
        staged_json.write_text(json.dumps(metadata, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                               encoding="utf-8")
        with staged_csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for device, recording in recordings.items():
                for values in recording["rows"]():
                    writer.writerow({**dict(zip(recording["columns"], values)),
                                     "workspace_kind": "sample", "workspace_device": device})
            for signal in signals:
                data = signal["data"]
                segments = data.get("segments")
                labels = data.get("labels")
                for index, (stamp, value) in enumerate(zip(data["times"], data["values"])):
                    writer.writerow({"workspace_kind": "signal", "workspace_device": signal["device"],
                                     "workspace_signal": signal["signal"], "workspace_row": signal.get("row", ""),
                                     "time_s": stamp, "workspace_value": value,
                                      "workspace_unit": signal.get("unit", ""),
                                      "workspace_label": labels[index] if labels is not None else "",
                                     "workspace_segment": segments[index] if segments is not None else ""})
        os.link(staged_json, sidecar)
        try:
            os.link(staged_csv, path)
        except BaseException:
            sidecar.unlink()
            raise
    return path


def read_session(path, validators, aliases=None):
    """Extract and validate every native recording before any panel is changed.

    ``aliases`` renames legacy device keys while reading (e.g.
    ``{"stroop": "tests"}``) so old workspace CSVs load into renamed panels.
    The caller owns the returned TemporaryDirectory while panels use its files.
    """
    path = Path(path)
    aliases = aliases or {}
    metadata = json.loads(metadata_path(path).read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("format") != FORMAT or metadata.get("version") != 1:
        raise ValueError("Unsupported workspace recording format")
    configuration, recordings = metadata.get("configuration"), metadata.get("recordings")
    if (not isinstance(configuration, dict) or configuration.get("version") != 1 or
            not isinstance(configuration.get("workspace"), dict) or not isinstance(recordings, dict)):
        raise ValueError("Missing workspace configuration or device recordings")
    recordings = {aliases.get(key, key): value for key, value in recordings.items()}
    if not recordings or any(key not in validators for key in recordings):
        raise ValueError("The workspace contains an unsupported device recording")
    directory = tempfile.TemporaryDirectory(prefix="heepidermis-workspace-")
    files, streams, writers, counts = {}, {}, {}, {}
    try:
        for device, recording in recordings.items():
            columns = recording["columns"]
            if not isinstance(columns, list) or not columns or any(not isinstance(c, str) for c in columns):
                raise ValueError(f"Invalid native columns for {device}")
            target = Path(directory.name) / f"{device}.csv"
            files[device], counts[device] = target, 0
            streams[device] = target.open("w", newline="", encoding="utf-8")
            writers[device] = csv.DictWriter(streams[device], fieldnames=columns)
            writers[device].writeheader()
            for suffix, values in recording.get("sidecars", {}).items():
                if suffix not in (".json", ".configuration.json"):
                    raise ValueError("Unsupported recording sidecar")
                Path(str(target) + suffix).write_text(json.dumps(values), encoding="utf-8")
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if not set(PREFIX).issubset(reader.fieldnames or []):
                raise ValueError("Not a workspace CSV")
            for row in reader:
                if row["workspace_kind"] == "signal":
                    continue  # Recomputed from native samples and the restored signal table.
                device = aliases.get(row["workspace_device"], row["workspace_device"])
                if row["workspace_kind"] != "sample" or device not in writers:
                    raise ValueError("Invalid workspace CSV record")
                writers[device].writerow({name: row[name] for name in recordings[device]["columns"]})
                counts[device] += 1
        for stream in streams.values():
            stream.close()
        for device, target in files.items():
            if counts[device] != recordings[device]["count"]:
                raise ValueError(f"Incomplete recording for {device}")
            validators[device](target)
        return directory, configuration, files
    except BaseException:
        for stream in streams.values():
            stream.close()
        directory.cleanup()
        raise
