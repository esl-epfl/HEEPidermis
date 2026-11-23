"""Local numbered clips and timestamped lyric lines; no downloads."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess

from .spec import SONGS_DIR, song_files


@dataclass(frozen=True)
class Song:
    number: int
    title: str
    artist: str
    path: Path

    @property
    def display(self):
        return f"{self.title.upper()}, by {self.artist}"


def discover_songs(folder):
    return {number: Song(number, *data) for number, data in song_files(folder).items()}


def read_segment(path):
    """Optional ``x_segment.txt``: one [mm:ss.xx] start time in the source audio.

    The supplied files may be full tracks; the excerpt plays from this time
    for ``test_time`` seconds. Missing file means "start at 0".
    """
    path = Path(path)
    if not path.exists():
        return 0.0
    lines = [line.split("#", 1)[0].strip()
             for line in path.read_text(encoding="utf-8-sig").splitlines()]
    lines = [line for line in lines if line]
    if len(lines) != 1:
        raise ValueError(f"{path.name}: expected one segment start timestamp, e.g. [00:30.00]")
    match = re.fullmatch(r"\[(\d+):([0-5]?\d(?:\.\d+)?)\]", lines[0])
    if not match:
        raise ValueError(f"{path.name}: expected [mm:ss.xx]")
    return int(match[1]) * 60 + float(match[2])


def segment_lyrics(lines, start, duration):
    """Rebase source-file lyrics onto the excerpt; keep an in-progress line.

    Lines outside the excerpt are excluded (also from the next-line preview).
    A line already in progress at the excerpt start is shown from 0.0. An
    explicit empty timed line still clears the display at its own time.
    """
    previous = [(stamp, text) for stamp, text in lines if stamp <= start]
    result = [(0.0, previous[-1][1])] if previous else []
    result.extend((stamp - start, text) for stamp, text in lines
                  if start < stamp < start + duration)
    return result


CONTOUR_FRAME = 0.1  # Seconds per pitch estimate of the melody feedback.


def song_contour(path, start, duration, frame=CONTOUR_FRAME):
    """Approximate pitch contour of the excerpt: one Hz value per frame.

    Decodes the excerpt to mono 8 kHz with ffmpeg and estimates one pitch
    per frame with an FFT autocorrelation (65–900 Hz, None = unvoiced).
    This feeds only the singing feedback bars — it is intentionally rough
    (the recording mixes voice and instruments) and is never a measurement.
    Returns [] when ffmpeg or NumPy is unavailable or decoding fails.
    """
    binary = shutil.which("ffmpeg")
    if not binary:
        return []
    try:
        import numpy as np
    except ImportError:
        return []
    rate = 8000
    try:
        raw = subprocess.run(
            [binary, "-v", "error", "-ss", str(start), "-t", str(duration), "-i", str(path),
             "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    samples = np.frombuffer(raw, dtype="<i2").astype(float) / 32768
    size = int(rate * frame)
    count = len(samples) // size
    if count == 0:
        return []
    frames = samples[:count * size].reshape(count, size)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    windowed = frames * np.hanning(size)
    spec = np.fft.rfft(windowed, 2 * size, axis=1)
    corr = np.fft.irfft((spec * spec.conj()).real, 2 * size, axis=1)[:, :size // 2]
    low, high = int(rate / 900), int(rate / 65)
    energy = np.concatenate((np.cumsum(windowed * windowed, axis=1),
                             np.zeros((count, 1))), axis=1)
    total = energy[:, -2]
    lags = np.arange(low, high)
    # Normalized autocorrelation for every lag of every frame at once:
    # sum x[0:L-l]^2 and sum x[l:L]^2 from cumulative energies.
    a = energy[:, size - lags]
    b = total[:, None] - energy[:, lags]
    denominator = np.sqrt(np.maximum(a * b, 1e-12))
    values = np.where(denominator > 0, corr[:, lags] / denominator, 0.0)
    # Spectral fallback for dense mixes where autocorrelation stays weak:
    # the strongest bin of the spectrum between 65 and 900 Hz.
    bin_freqs = np.fft.rfftfreq(2 * size, 1 / rate)
    band = np.flatnonzero((bin_freqs >= 65) & (bin_freqs <= 900))
    magnitudes = np.abs(spec)[:, band]
    contour = []
    for row, level, spectrum in zip(values, rms, magnitudes):
        pitch = None
        if level >= .01:
            peaks = [i for i in range(1, len(row) - 1)
                     if row[i] > row[i - 1] and row[i] >= row[i + 1]]
            if peaks:
                best = max(row[i] for i in peaks)
                if best >= .6:
                    first = next(i for i in peaks if row[i] >= max(.6, best * .95))
                    pitch = float(rate / (low + first))
            if pitch is None:
                pitch = float(bin_freqs[band][int(np.argmax(spectrum))])
        contour.append(pitch)
    # Light median smoothing: the mix makes single frames jump between
    # instruments (often by octaves); the bars only need the broad contour.
    smoothed = []
    for i in range(count):
        window = [contour[j] for j in range(max(0, i - 2), min(count, i + 3))
                  if contour[j] is not None]
        smoothed.append(float(np.median(window)) if window else None)
    return smoothed


def read_lyrics(path):
    """LRC-style [mm:ss.xx] lines, relative to the start of the supplied clip.

    Multiple time tags per line are supported. Empty timed lines explicitly
    clear the karaoke display. Also accepts seconds + tab + text.
    """
    path = Path(path)
    if not path.exists():
        return []
    lines = []
    for index, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        tags = list(re.finditer(r"\[(\d+):([0-5]?\d(?:\.\d+)?)\]", raw))
        if tags and tags[0].start() == 0:
            text = raw[tags[-1].end():].strip()
            for tag in tags:
                lines.append((int(tag[1]) * 60 + float(tag[2]), text))
        elif re.match(r"\[(ar|ti|al|by):", raw):
            continue  # Common LRC metadata.
        else:
            match = re.fullmatch(r"(\d+(?:\.\d+)?)\t(.*)", raw)
            if not match:
                raise ValueError(f"{path.name}:{index}: expected [mm:ss.xx] lyric text")
            lines.append((float(match[1]), match[2].strip()))
    return sorted(lines, key=lambda row: row[0])


def lyric_at(lines, seconds):
    index = bisect_right([stamp for stamp, _ in lines], seconds) - 1
    current = lines[index][1] if index >= 0 else ""
    following = lines[index + 1][1] if index + 1 < len(lines) else ""
    return index, current, following
