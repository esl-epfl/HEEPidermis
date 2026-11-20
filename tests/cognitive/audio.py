"""Tiny sound helper for the tests: synthesized WAVs, played asynchronously.

No external audio assets and no extra Python dependencies: the sounds (an
error buzzer, a success cling and a clock tick) are synthesized with the
standard library into the system temp dir on first use and replayed from
there with whatever player the system provides (paplay, aplay, ffplay, ...).
Playback never blocks the Tk main loop; if no player exists the tests simply
run silently.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

RATE = 22050  # Samples per second for the synthesized WAVs.

# Candidate command-line players, in preference order.
PLAYERS = (
    ("paplay", ("paplay",)),                       # PulseAudio
    ("aplay", ("aplay", "-q")),                    # ALSA
    ("play", ("play", "-q")),                      # SoX
    ("ffplay", ("ffplay", "-nodisp", "-autoexit", "-nostdin", "-loglevel", "quiet")),
)


def _write_wav(path: Path, samples) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"".join(
        struct.pack("<h", max(-32767, min(32767, int(sample * 32767))))
        for sample in samples)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(frames)


def _tone(duration: float, fn) -> list:
    """Samples for `duration` seconds; fn(t in seconds) returns [-1, 1]."""
    return [fn(index / RATE) for index in range(int(RATE * duration))]


def _buzz():
    """Loud low 'meeeeh': fundamental + harmonics, flat and grating."""
    frequency = 170.0

    def sample(t):
        angle = 2.0 * math.pi * frequency * t
        value = (math.sin(angle) + 0.6 * math.sin(2 * angle)
                 + 0.35 * math.sin(3 * angle) + 0.2 * math.sin(4 * angle))
        return 0.85 * value / 2.15

    return _tone(0.7, sample)


def _cling():
    """Short bright two-note cling with a fast decay, deliberately quieter."""
    def sample(t):
        envelope = math.exp(-t * 22.0)
        value = (math.sin(2 * math.pi * 1318.5 * t)        # E6
                 + 0.5 * math.sin(2 * math.pi * 1975.5 * t)  # B6
                 + 0.25 * math.sin(2 * math.pi * 2637.0 * t))
        return 0.30 * envelope * value / 1.75

    return _tone(0.35, sample)


def _tick():
    """Very short clock tick: a damped high click."""
    def sample(t):
        return 0.30 * math.exp(-t * 300.0) * math.sin(2 * math.pi * 2100.0 * t)

    return _tone(0.03, sample)


TICK_DURATION = 0.03


def render_tick_track(path: Path, duration: float,
                      rate_start: float = 1.0, rate_end: float = 3.0) -> Path:
    """Write one continuous WAV with ticks that accelerate from `rate_start`
    to `rate_end` Hz across `duration` seconds.

    Playing the whole track as a single stream (instead of spawning one
    process per tick) guarantees the clock keeps ticking even while the
    buzzer/cling sounds play, and the acceleration is sample-accurate.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    click = _tick()
    click_n = len(click)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        written = 0
        total = int(RATE * duration)
        t = 0.0
        while written < total:
            progress = t / duration if duration > 0 else 1.0
            interval = 1.0 / (rate_start + (rate_end - rate_start) * progress)
            silence = max(0, int(RATE * interval) - click_n)
            for value in [0.0] * min(silence, total - written):
                handle.writeframes(struct.pack(
                    "<h", max(-32767, min(32767, int(value * 32767)))))
                written += 1
                if written >= total:
                    break
            t += silence / RATE
            for value in click:
                if written >= total:
                    break
                handle.writeframes(struct.pack(
                    "<h", max(-32767, min(32767, int(value * 32767)))))
                written += 1
            t += click_n / RATE
    return path


class SoundPlayer:
    """Synthesizes the test sounds once and plays them without blocking."""

    def __init__(self):
        command = next((cmd for binary, cmd in PLAYERS if shutil.which(binary)), None)
        self.available = command is not None
        self._command = command or ("aplay", "-q")
        self._directory = Path(tempfile.gettempdir()) / "heepidermis-test-sounds"
        self._files: dict[str, Path] = {}
        self._processes: list[subprocess.Popen] = []

    def _sound_file(self, name: str) -> Path:
        path = self._files.get(name)
        if path is not None and path.exists():
            return path
        generators = {"buzz": _buzz, "cling": _cling, "tick": _tick}
        path = self._directory / f"{name}.wav"
        _write_wav(path, generators[name]())
        self._files[name] = path
        return path

    def play(self, name: str) -> None:
        """Fire and forget; safe to call from Tk callbacks."""
        return self.play_file(self._sound_file(name))

    def play_file(self, path: Path):
        """Play one WAV without blocking; returns the process handle."""
        if not self.available:
            return None
        self._reap()
        try:
            process = subprocess.Popen(
                [*self._command, str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._processes.append(process)
            return process
        except OSError:
            self.available = False  # Player broke; stay silent from now on.
            return None

    def play_tick_track(self, duration: float):
        """Render (once per duration) and play the accelerating tick track."""
        path = self._directory / f"tick-track-{duration:.1f}s.wav"
        if not path.exists():
            render_tick_track(path, duration)
        return self.play_file(path)

    def _reap(self) -> None:
        self._processes = [p for p in self._processes if p.poll() is None]

    def stop(self) -> None:
        for process in self._processes:
            process.terminate()
        self._processes.clear()
