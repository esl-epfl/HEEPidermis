"""Move completed/live recordings without regenerating samples or analysis."""

import os
from pathlib import Path


def rename_recording(source, destination, suffixes=()):
    """Move the CSV and its sidecars on the same filesystem, with rollback.

    Open file descriptors continue appending to the renamed files on Linux.
    Cross-filesystem moves fail explicitly rather than silently copying a
    changing recording. Call from the writer's thread or under its lock.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        return destination
    moves = [(Path(str(source) + suffix), Path(str(destination) + suffix))
             for suffix in suffixes if Path(str(source) + suffix).exists()]
    moves.append((source, destination))
    for old, new in moves:
        if new.exists():
            raise FileExistsError(f"Choose a new filename: {new} already exists")
        if old.stat().st_dev != new.parent.stat().st_dev:
            raise OSError("Choose a destination on the same filesystem to rename the recording")
    completed = []
    try:
        for old, new in moves:
            os.rename(old, new)
            completed.append((old, new))
    except OSError:
        for old, new in reversed(completed):
            os.rename(new, old)
        raise
    return destination
