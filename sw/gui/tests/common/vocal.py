"""One microphone stream for phrase recognition and real-time vocal pitch.

Optional libraries are loaded in the worker. Audio never enters Tk callbacks;
bounded queues and generation tokens prevent stale results crossing phases.
"""

from collections import deque
import json
import math
import queue
import re
import threading
import time


def words(text):
    return re.findall(r"[a-z]+(?:'[a-z]+)?", text.lower())


def phrase_score(expected, recognized):
    """Word edit similarity, accounting for order, insertions and repetitions."""
    a, b = words(expected), words(recognized)
    if not a:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (left != right)))
        previous = current
    return max(0.0, 1 - previous[-1] / max(len(a), len(b)))


def estimate_pitch(samples, sample_rate=16000, threshold=.01):
    """Normalized autocorrelation with interpolation; None for silence/noise.

    Select the first strong local maximum (fundamental period rather than its
    multiples). Analysis range is 65–900 Hz; input is normalized mono PCM.
    """
    import numpy as np
    x = np.asarray(samples, dtype=float)
    if len(x) < sample_rate / 30:
        return None
    x = x - x.mean()
    if float(np.sqrt(np.mean(x * x))) < threshold:
        return None
    low, high = int(sample_rate / 900), min(int(sample_rate / 65), len(x) // 2)
    correlations = np.zeros(high + 2)
    for lag in range(low - 1, high + 2):
        a, b = x[:-lag], x[lag:]
        denominator = np.sqrt(np.dot(a, a) * np.dot(b, b))
        correlations[lag] = np.dot(a, b) / denominator if denominator else 0
    peaks = [lag for lag in range(low, high + 1)
             if correlations[lag] > correlations[lag - 1]
             and correlations[lag] >= correlations[lag + 1]]
    if not peaks:
        return None
    best = max(correlations[lag] for lag in peaks)
    if best < .75:
        return None
    lag = next(lag for lag in peaks if correlations[lag] >= max(.75, best * .95))
    a, b, c = correlations[lag - 1:lag + 2]
    divisor = a - 2 * b + c
    adjustment = .5 * (a - c) / divisor if divisor else 0
    return float(sample_rate / (lag + adjustment))


def pitch_cents(frequency, target):
    return 1200 * math.log2(frequency / target)


class VocalMonitor:
    def __init__(self, device=-1, model_path="", threshold=.01):
        self.device, self.model_path, self.threshold = device, model_path, threshold
        self.ready = False
        self.error = ""
        self.recognition_error = ""
        self.status = "Starting microphone…"
        self._stop = threading.Event()
        self._audio = queue.Queue(maxsize=8)
        self._results = deque(maxlen=100)
        self._lock = threading.Lock()
        self._generation = 0
        self._mode = "off"
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def listen(self, mode):
        with self._lock:
            self._generation += 1
            self._mode = mode
            self._results.clear()

    def drain(self):
        with self._lock:
            result = list(self._results)
            self._results.clear()
        return result

    def stop(self):
        self._stop.set()
        self.listen("off")

    def _run(self):
        try:
            import numpy as np
            import sounddevice as sd
            model = None
            try:
                from vosk import Model, KaldiRecognizer, SetLogLevel
                from .voice import ensure_model
                self.status = "Loading English recognition model…"
                SetLogLevel(-1)
                model = Model(str(ensure_model(self.model_path)))
            except Exception as exc:
                self.recognition_error = f"Speech recognition unavailable: {exc}"
            if self._stop.is_set():
                return

            def capture(data, _frames, _timing, status):
                with self._lock:
                    generation, mode = self._generation, self._mode
                if mode == "off" or self._stop.is_set():
                    return
                try:
                    self._audio.put_nowait((generation, mode, time.monotonic(), bytes(data), bool(status)))
                except queue.Full:
                    pass

            with sd.RawInputStream(samplerate=16000, blocksize=1600, channels=1,
                                   dtype="int16", device=None if self.device < 0 else self.device,
                                   callback=capture):
                self.ready = True
                self.status = "Microphone ready"
                generation, recognizer = -1, None
                while not self._stop.is_set():
                    try:
                        token, mode, stamp, data, overflow = self._audio.get(timeout=.1)
                    except queue.Empty:
                        continue
                    with self._lock:
                        if token != self._generation:
                            continue
                    if token != generation:
                        generation = token
                        recognizer = (KaldiRecognizer(model, 16000)
                                      if model and mode in ("speech", "sing") else None)
                    samples = np.frombuffer(data, dtype="<i2").astype(float) / 32768
                    result = {"at": stamp, "level": float(np.sqrt(np.mean(samples * samples))),
                              "pitch": estimate_pitch(samples, threshold=self.threshold)
                              if mode in ("pitch", "sing") else None,
                              "text": "", "final": False, "overflow": overflow}
                    if recognizer:
                        final = bool(recognizer.AcceptWaveform(data))
                        parsed = json.loads(recognizer.Result() if final else recognizer.PartialResult())
                        result.update(text=parsed.get("text" if final else "partial", ""), final=final)
                    with self._lock:
                        if token == self._generation:
                            self._results.append(result)
        except Exception as exc:
            self.error = f"Microphone unavailable: {exc}"
        finally:
            self.ready = False
