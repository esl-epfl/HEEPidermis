"""Cancellable local audio, reference tones and English speech for tasks.

Playback and speech synthesis run in a separate process group, so leaving a
phase also stops any synthesizer/player children. No shell interpolation.
"""

import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from .audio import RATE, _write_wav


class MediaPlayer:
    def __init__(self):
        self.process = None
        self.started = 0.0
        self.error = ""
        self._directory = tempfile.TemporaryDirectory(prefix="heepidermis-media-")

    def _launch(self, command, env=None):
        self.stop()
        self.error = ""
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True, env=env)
            self.started = time.monotonic()
        except OSError as exc:
            self.error = str(exc)
            return False
        return True

    def play(self, path, offset=0.0, duration=None):
        binary = shutil.which("ffplay")
        if not binary:
            self.error = "Install ffmpeg (ffplay) for audio playback"
            return False
        command = [binary, "-nodisp", "-autoexit", "-loglevel", "error", "-ss", str(offset)]
        if duration is not None:
            command.extend(("-t", str(duration)))
        return self._launch([*command, "-i", str(path)])

    def speak(self, text):
        # This module's worker imports synthesis libraries only in its process.
        # run_test.py can be invoked from any cwd; its sys.path adjustment is
        # not inherited by a new Python process.
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3]) + os.pathsep + env.get("PYTHONPATH", "")
        return self._launch([sys.executable, "-m", "gui.tests.common.media", "--speak", text,
                             str(Path(self._directory.name) / "speech.wav")], env=env)

    def tone(self, frequency, duration):
        path = Path(self._directory.name) / f"tone-{frequency}-{duration}.wav"
        if not path.exists():
            _write_wav(path, (0.22 * min(1, t / .02, (duration - t) / .02)
                             * math.sin(2 * math.pi * frequency * t)
                             for t in (i / RATE for i in range(int(RATE * duration)))))
        return self.play(path)

    @property
    def playing(self):
        return self.process is not None and self.process.poll() is None

    @property
    def failed(self):
        code = self.process.poll() if self.process else None
        return bool(self.error) or (code is not None and code != 0)

    def stop(self):
        if self.process is not None:
            try:
                # The group can still contain a player even if its parent exited.
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=.3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=1)
            self.process = None

    def close(self):
        self.stop()
        self._directory.cleanup()


def synthesize_speech(text, path):
    """Render English speech using the eSpeak command or shared library."""
    command = shutil.which("espeak-ng") or shutil.which("espeak")
    if command:
        subprocess.run([command, "-v", "en", "-s", "145", "-w", str(path), text], check=True)
        return
    import ctypes as ct
    from ctypes.util import find_library
    import wave

    library = find_library("espeak-ng") or find_library("espeak")
    if not library:
        raise RuntimeError("Install espeak-ng for English audio prompts")
    lib = ct.CDLL(library)
    lib.espeak_Initialize.argtypes = [ct.c_int, ct.c_int, ct.c_char_p, ct.c_int]
    rate = lib.espeak_Initialize(2, 0, None, 0)  # Synchronous synthesis, no audio device.
    if rate <= 0:
        raise RuntimeError("Speech synthesizer could not initialize")
    chunks = []
    callback_type = ct.CFUNCTYPE(ct.c_int, ct.POINTER(ct.c_short), ct.c_int, ct.c_void_p)

    @callback_type
    def receive(samples, count, _events):
        if samples and count:
            chunks.append(ct.string_at(samples, count * 2))
        return 0

    lib.espeak_SetSynthCallback.argtypes = [callback_type]
    lib.espeak_SetSynthCallback(receive)
    lib.espeak_SetVoiceByName.argtypes = [ct.c_char_p]
    if lib.espeak_SetVoiceByName(b"en") != 0:
        raise RuntimeError("English speech voice unavailable")
    lib.espeak_SetParameter(1, 145, 0)  # Words per minute.
    lib.espeak_Synth.argtypes = [ct.c_char_p, ct.c_size_t, ct.c_uint, ct.c_int,
                                ct.c_uint, ct.c_uint, ct.c_void_p, ct.c_void_p]
    data = text.encode("utf-8")
    result = lib.espeak_Synth(data, len(data) + 1, 0, 1, 0, 1, None, None)
    lib.espeak_Synchronize()
    lib.espeak_Terminate()
    if result != 0 or not chunks:
        raise RuntimeError("Speech synthesis failed")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(chunks))


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--speak":
        raise SystemExit(2)
    synthesize_speech(sys.argv[2], sys.argv[3])
    raise SystemExit(subprocess.call(["ffplay", "-nodisp", "-autoexit",
                                     "-loglevel", "error", "-i", sys.argv[3]], stdin=subprocess.DEVNULL))
