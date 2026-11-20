"""Standalone microphone / keyword-spotting check.

Listens continuously and shows, live, whatever the spotter hears: the mic
level, the partial recognition text and the log of recognized words with
their timestamps.  Nothing is scored — use it to verify that the microphone
works and that the color words are recognized before running a real test.

    python tests/run_test.py voice-test
    python tests/run_test.py voice-test --n-colors 6 --mic-device 2

ESC quits and prints a small report.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .base import CognitiveTest, TestConfig
from .stroop import COLOR_POOL


@dataclass
class VoiceTestConfig(TestConfig):
    """Parameters of the listen-only voice check."""

    # How many colors from the pool to listen for (2..10).
    n_colors: int = 10
    # sounddevice input device index; -1 = system default.
    mic_device: int = -1
    # Path to a local Vosk model; empty = auto-managed (downloads once).
    vosk_model: str = ""

    def __post_init__(self):
        self.n_colors = max(2, min(len(COLOR_POOL), int(self.n_colors)))


class VoiceTest(CognitiveTest):
    """Listen-only screen: mic level, partial text and recognized words."""

    NAME = "voice-test"
    ConfigClass = VoiceTestConfig

    def __init__(self, config=None, **overrides):
        super().__init__(config, **overrides)
        names = list(COLOR_POOL)[:self.config.n_colors]
        from .voice import VoiceSpotter
        self.spotter = VoiceSpotter(
            words=names,
            model_override=self.config.vosk_model,
            device=self.config.mic_device if self.config.mic_device >= 0 else None)
        self.log = []               # (word, monotonic timestamp)

    def _begin(self):
        self.spotter.start()
        self.after(100, self._tick)

    def _handle_key(self, keysym):
        if keysym == "c":
            self.log.clear()           # Clear the log.
            self._heard_reset = True

    def _tick(self):
        if self.result is not None:
            return
        for word, ts in self.spotter.drain_words():
            self.log.append((word, ts))
        self._draw()
        self.after(80, self._tick)

    def _draw(self):
        self._clear_all()
        width = self._canvas.winfo_width() or 1000
        height = self._canvas.winfo_height() or 700
        self.show_text(["VOICE TEST", "",
                        "Just say the colors out loud — this screen only listens."],
                       y=0.14, color=self.config.foreground)
        status = self.spotter.status if not self.spotter.ready else "listening"
        if self.spotter.error:
            status = f"ERROR: {self.spotter.error}"
        self._canvas.create_text(width / 2, height * 0.26, text=status,
                                 fill="#f2bd62" if self.spotter.error else "#8a8a92",
                                 font=self.font(16))

        # Microphone level bar.
        bar = width * 0.6
        top = height * 0.34
        self._canvas.create_rectangle(width / 2 - bar / 2, top,
                                      width / 2 + bar / 2, top + 16,
                                      outline="#5a5a62", width=1)
        fill = min(1.0, self.spotter.level * 4)
        self._canvas.create_rectangle(
            width / 2 - bar / 2, top, width / 2 - bar / 2 + bar * fill, top + 16,
            fill="#40c060" if self.spotter.level > 0.01 else "#e04040", outline="")
        self._canvas.create_text(width / 2, top + 34,
                                 text=f"mic level {self.spotter.level:.3f}",
                                 fill="#8a8a92", font=self.font(12, bold=False))

        # Live partial recognition text.
        partial = self.spotter.partial.strip()
        self._canvas.create_text(
            width / 2, height * 0.46,
            text=partial if partial else "…",
            fill="#4070e8" if partial else "#5a5a62",
            font=self.font(22))

        # Log of recognized words, newest last.
        self._canvas.create_text(width / 2, height * 0.56,
                                 text="recognized words:",
                                 fill=self.config.foreground, font=self.font(15))
        recent = self.log[-10:]
        for index, (word, ts) in enumerate(recent):
            hex_color = COLOR_POOL.get(word, self.config.foreground)
            age = time.monotonic() - ts
            self._canvas.create_text(
                width / 2, height * 0.60 + index * 30,
                text=f"“{word}”   (+{age:4.1f}s ago)" if index < len(recent) - 1
                else f"“{word}”",
                fill=hex_color, font=self.font(17))

        self._canvas.create_text(
            width / 2, height - 30,
            text="C clears the log  ·  ESC to quit",
            fill="#8a8a92", font=self.font(13, bold=False))

    def _clear_all(self):
        self._canvas.delete("all")

    def _summary(self) -> dict:
        return {
            "n_words": len(self.log),
            "words": [word for word, _ts in self.log],
            "spotter_status": self.spotter.status,
            "spotter_error": self.spotter.error,
        }

    def _finish(self, aborted: bool = False) -> None:
        self.spotter.stop()
        super()._finish(aborted)
