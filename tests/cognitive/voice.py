"""Offline keyword spotting for the test color words, based on Vosk.

Captures the microphone with ``sounddevice`` and feeds a Vosk
``KaldiRecognizer`` restricted to a fixed grammar (the color names), so the
only things it can hear are the words we care about.  Detection happens on
the *partial* results, i.e. as soon as a word is decoded — not when the
utterance ends — which keeps the reaction-time timestamp honest.

Design notes:

* All heavy work (model download/load, recognition) happens on a background
  thread; the tests only *poll* this class from the Tk thread, so no Tk
  call ever happens off the main thread.
* ``drain_words()`` returns ``(word, monotonic_timestamp)`` pairs where the
  timestamp is taken in the audio callback, when the chunk containing the
  word was captured.  The tests use it directly as the reaction time, so
  recognition latency never penalizes the participant.
* Everything degrades gracefully: without vosk/sounddevice installed, or
  without a microphone, the tests fall back to the keyboard.
"""

from __future__ import annotations

import array
from collections import deque
import json
import math
from pathlib import Path
import queue
import shutil
import struct
import tempfile
import threading
import time
import urllib.request
import zipfile

try:
    import sounddevice as _sd
except Exception:  # ImportError or missing PortAudio library.
    _sd = None

try:
    from vosk import Model as _VoskModel, KaldiRecognizer as _KaldiRecognizer
    from vosk import SetLogLevel as _vosk_set_log_level
except Exception:
    _VoskModel = _KaldiRecognizer = None
    _vosk_set_log_level = None

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_NAME = "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000
BLOCKSIZE = 800  # 50 ms chunks; the timestamp resolution for reactions.

LIBS_MISSING = "pip install vosk sounddevice"


def rms(data: bytes) -> float:
    """RMS level of an int16 mono chunk, normalized to 0..1."""
    samples = array.array("h")
    samples.frombytes(data)
    if not samples:
        return 0.0
    acc = 0
    for value in samples:
        acc += value * value
    return math.sqrt(acc / len(samples)) / 32768.0


def model_path(override: str = "") -> Path:
    if override:
        return Path(override)
    return MODEL_DIR / MODEL_NAME


def ensure_model(override: str = "", progress=None) -> Path:
    """Return a local Vosk model directory, downloading the small model
    once (about 40 MB) if needed.  ``progress(done, total)`` is optional."""
    path = model_path(override)
    if (path / "conf" / "model.conf").exists():
        return path
    if override:
        raise RuntimeError(f"Vosk model not found at {path}")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    archive = Path(tempfile.gettempdir()) / f"{MODEL_NAME}.zip"
    if not archive.exists():
        _download(MODEL_URL, archive, progress)
    with zipfile.ZipFile(archive) as handle:
        handle.extractall(MODEL_DIR)
    if not (path / "conf" / "model.conf").exists():
        raise RuntimeError(f"Model archive did not contain {MODEL_NAME}")
    return path


def _download(url: str, destination: Path, progress=None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "heepidermis-tests"})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length", 0))
        done = 0
        with open(destination, "wb") as handle:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if progress is not None:
                    try:
                        progress(done, total)
                    except Exception:
                        pass


class VoiceSpotter:
    """Background mic -> Vosk keyword spotter; polled from the Tk thread."""

    # Mic RMS above this counts as "someone is talking" (used for the fast
    # speech-end signal; the recognizer's own endpointer is slower).
    VOICE_LEVEL = 0.015

    def __init__(self, words, model_override: str = "", device=None,
                 sample_rate: int = SAMPLE_RATE, debug: bool = False):
        self.words = set(words)
        self.model_override = model_override
        self.device = device
        self.sample_rate = sample_rate
        self.ready = False
        self.error = ""
        self.status = "starting…"
        self.level = 0.0        # Latest mic RMS, 0..1.
        self.partial = ""       # Latest partial recognition text.
        self.on_status = None   # Optional callable(str), recognizer thread.
        self.debug = bool(debug)  # Print everything recognized to the terminal.
        self.paused = False     # Test gate: True = emit nothing (no word on
                                # screen, or the answer was already given).
        self._was_paused = False  # For the one-line pause/resume debug log.

        self._hits: deque = deque()          # (word, timestamp)
        self._finals: deque = deque()        # timestamps of utterance ends
        self._last_voice_ts = None           # last time the mic heard voice
        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._thread = None
        self._stream = None
        self._recognizer = None
        self._spoken: set = set()
        self._suppress_final = False        # ack(): the utterance is consumed
        self._pending_words = None
        self._model = None
        self._has_vosk = _VoskModel is not None and _KaldiRecognizer is not None
        self._has_audio = _sd is not None

    # ------------------------------------------------------------- control

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def drain_words(self):
        """Pop every detected word as (word, monotonic timestamp)."""
        items = []
        while self._hits:
            items.append(self._hits.popleft())
        return items

    def drain_finals(self):
        """Pop the timestamps of ended utterances (silence endpoints).

        The endpointer fires when the speaker stops talking, so a test can
        wait for it before judging an answer.
        """
        items = []
        while self._finals:
            items.append(self._finals.popleft())
        return items

    def speech_idle(self) -> float:
        """Seconds since the mic last picked up a voice-level signal.

        The recognizer's own endpointer can lag behind by several hundred
        milliseconds; the raw mic level drops the instant the word is
        finished, so this gives a much faster "done speaking" signal.
        """
        if self._last_voice_ts is None:
            return float("inf")
        return time.monotonic() - self._last_voice_ts

    def ack(self) -> None:
        """The current utterance has been consumed by the test.

        Clears the once-per-utterance dedup set so a color can be
        recognized again in the very next trial, and marks the utterance's
        eventual silence-endpoint final as consumed too — otherwise the
        final would re-emit the already-answered word with a late
        timestamp and judge the next trial instantly.
        """
        self._spoken.clear()
        self._suppress_final = True

    def set_words(self, words) -> None:
        """Swap the recognition grammar (e.g. calibration +/- colors).

        The model stays loaded; the recognizer thread rebuilds only the
        grammar-restricted recognizer on its next loop iteration.
        """
        self._pending_words = list(words)

    def _rebuild_recognizer(self) -> None:
        """Fresh recognizer: same model and grammar, empty decode state.

        Used after a listening pause (where Vosk would otherwise re-output
        the utterance it had buffered before the pause) and whenever the
        grammar changes.
        """
        if self._model is None:
            return
        try:
            self._recognizer = _KaldiRecognizer(
                self._model, self.sample_rate,
                json.dumps(sorted(self.words)))
        except Exception:
            # Fall back to a reset if reconstruction ever fails.
            reset = getattr(self._recognizer, "Reset", None)
            if callable(reset):
                try:
                    reset()
                except Exception:
                    pass

    # --------------------------------------------------------- worker thread

    def _run(self):
        try:
            if not self._has_vosk or not self._has_audio:
                raise RuntimeError(LIBS_MISSING)
            self._status("checking voice model…")
            path = ensure_model(self.model_override, progress=self._progress)
            self._status("loading voice model…")
            if _vosk_set_log_level is not None:
                _vosk_set_log_level(-1)
            model = _VoskModel(str(path))
            self._model = model
            grammar = json.dumps(sorted(self.words))
            self._recognizer = _KaldiRecognizer(model, self.sample_rate, grammar)
            self._stream = _sd.InputStream(
                samplerate=self.sample_rate, channels=1, dtype="int16",
                blocksize=BLOCKSIZE, callback=self._on_audio,
                device=self.device)
            self._stream.start()
            self.ready = True
            self._status("listening")
            self._recognize_loop()
        except Exception as error:
            self.error = str(error)
            self._status(f"voice unavailable ({error})")

    def _progress(self, done: int, total: int) -> None:
        if total:
            self._status(f"downloading voice model… {done / 1e6:.0f}"
                         f"/{total / 1e6:.0f} MB")

    def _status(self, text: str) -> None:
        self.status = text
        if self.on_status is not None:
            try:
                self.on_status(text)
            except Exception:
                pass

    def _on_audio(self, indata, frames, time_info, status) -> None:
        data = bytes(indata)
        self.level = rms(data)  # Cheap enough for the audio thread.
        now = time.monotonic()
        if self.level >= self.VOICE_LEVEL:
            self._last_voice_ts = now  # Someone is talking.
        self._queue.put((now, data))

    def _recognize_loop(self):
        while self._running:
            if self._pending_words is not None:
                self.words = set(self._pending_words)
                self._pending_words = None
                self._rebuild_recognizer()
                self._spoken.clear()
            try:
                timestamp, data = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.paused:
                # Not listening: throw the audio away without feeding the
                # recognizer, so nothing outside the listening phases
                # (instructions, rest, summary, post-answer) is processed,
                # recognized or printed.  The queue is still drained to
                # avoid a backlog, and the stream stays open so no device
                # reopen latency is added when listening resumes.
                if not self._was_paused and self.debug:
                    self._log("microphone paused (not listening)")
                self._was_paused = True
                self._last_voice_ts = 0.0
                self.partial = ""
                self._spoken.clear()
                self._suppress_final = False
                continue
            if self._was_paused:
                if self.debug:
                    self._log("microphone listening")
                self._was_paused = False
                # The recognizer kept decoding state from before the pause:
                # without a full rebuild it re-outputs the stale utterance
                # as a "fresh" result right after listening resumes, with a
                # new timestamp that lands inside the current trial.
                # (Reset() alone proved unreliable across vosk versions.)
                self._rebuild_recognizer()
                self._last_voice_ts = time.monotonic()
            if self._recognizer.AcceptWaveform(data):
                text = json.loads(self._recognizer.Result()).get("text", "")
                self._accept_result(text, timestamp, final=True)
            else:
                text = json.loads(self._recognizer.PartialResult()).get("partial", "")
                self._accept_result(text, timestamp, final=False)

    def _emit(self, text: str, timestamp: float) -> None:
        """Queue every grammar word of ``text`` not yet emitted this utterance."""
        for word in text.split():
            if word in self.words and word not in self._spoken:
                self._spoken.add(word)
                if self.paused:
                    if self.debug:
                        self._log(f"dropped (input paused): {word!r}")
                    continue
                if self.debug:
                    self._log(f"recognized: {word!r}")
                self._hits.append((word, timestamp))

    def _log(self, message: str) -> None:
        print(f"[voice] {message}", flush=True)

    def _accept_result(self, text: str, timestamp: float, final: bool) -> None:
        """Process one recognizer result (partials and finals alike).

        A word is emitted at most once per utterance: partials already
        emitted it while it was being spoken, and re-emitting it from the
        final would fire a phantom answer up to ~0.5 s later — landing in
        the NEXT trial and judging it instantly (the recognizer's endpoint
        fires long after the word itself ended).  After :meth:`ack` the
        whole utterance counts as consumed, so its final emits nothing.
        """
        if final:
            if self.debug and text:
                self._log(f"final: {text!r}")
            self._finals.append(timestamp)  # Utterance ended (silence).
            if not self._suppress_final:
                self._emit(text, timestamp)
            self._spoken.clear()   # A new utterance may repeat a color.
            self._suppress_final = False
            self.partial = ""
        else:
            if text and text != self.partial and self.debug:
                self._log(f"partial: {text!r}")
            if text:
                self.partial = text
            self._emit(text, timestamp)
