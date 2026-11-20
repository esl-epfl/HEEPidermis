"""Stroop color-word interference test.

The participant sees a color word (e.g. ``red``) printed in an ink color that
may or may not match the word, and must indicate the INK color as fast as
possible.  Congruent trials (word == ink) act as the baseline; the extra
response time on incongruent trials is the classic Stroop effect.

Run standalone from the repository root:

    python tests/run_test.py stroop --n-colors 4 --rest-time 10 --test-time 60

Answers come from the keyboard (``1..n``, number row or keypad) or, with
``--response-mode voice``, from keyword spotting: say the color out loud and
the reaction clock stops at the moment the word is heard (recognition
latency never penalizes the participant).  The last word heard is shown
below the color legend.  ``Escape`` aborts and still reports the trials
collected so far.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .base import CognitiveTest, TestConfig

# Color pool, in default preference order; ``n_colors`` takes the first N.
COLOR_POOL = {
    "red": "#e04040",
    "green": "#40c060",
    "blue": "#4070e8",
    "yellow": "#e8c830",
    "purple": "#a050d0",
    "orange": "#f08020",
    "cyan": "#30b8c8",
    "pink": "#f070b0",
    "brown": "#9c6b30",
    "gray": "#a0a0a0",
}

RESPONSE_KEYS = "1234567890"

CALIBRATION_TIME = 5.0      # Seconds per color in the voice calibration.
CALIBRATION_ATTEMPTS = 2    # Retries before a color is marked as missed.

# A spoken answer can never arrive this soon after the word appears; hits
# faster than this were carried over from before the stimulus and are
# dropped instead of judged.
MIN_VOICE_LAG = 0.05


@dataclass
class StroopConfig(TestConfig):
    """Parameters of one Stroop run.

    ``n_colors``       how many colors from the pool are used (2..10).
    ``rest_time``      countdown seconds before the test phase starts.
    ``test_time``      duration of the test phase in seconds.
    ``stimulus_time``  max seconds a word stays on screen; 0 = until response.
    ``isi``            seconds of fixation cross between two stimuli.
    ``congruent_ratio`` probability that the word matches its ink color.
    ``seed``           RNG seed for reproducible trial sequences; 0 = random.
    ``color_pool``     subset of COLOR_POOL names to draw colors from.
    ``response_mode``  "keys" (number keys) or "voice" (keyword spotting).
    ``voice_calibration`` say each color once before the test (voice mode).
    ``mic_device``     sounddevice input device index; -1 = system default.
    ``vosk_model``     path to a local Vosk model; empty = auto-managed.
    """

    n_colors: int = 4
    stimulus_time: float = 4.0
    isi: float = 0.5
    congruent_ratio: float = 0.25
    seed: int = 0
    color_pool: tuple = tuple(COLOR_POOL)
    # Optional name -> hex overrides on top of the built-in palette (set
    # automatically when color_pool is written as {name: hex} in the
    # configuration file).
    color_hexes: dict | None = None
    show_legend: bool = True
    response_mode: str = "keys"
    voice_calibration: bool = True
    mic_device: int = -1
    vosk_model: str = ""
    # After a spoken answer is recognized, the judgment waits until the
    # speaker is done: either the recognizer's silence endpoint, the mic
    # going quiet for `voice_quiet_s` (fast path), or this cap.
    voice_quiet_s: float = 0.15
    voice_max_wait: float = 0.5
    # A spoken answer can never arrive this soon after the word appears;
    # faster hits are carried over from before the stimulus and dropped.
    min_voice_lag: float = 0.05
    # Print everything recognized (and what reaches the test) to the
    # terminal.  Silence with --voice-debug false.
    voice_debug: bool = True

    def __post_init__(self):
        self.n_colors = max(2, min(len(self.color_pool), int(self.n_colors)))
        self.congruent_ratio = min(1.0, max(0.0, float(self.congruent_ratio)))
        self.response_mode = "voice" if self.response_mode == "voice" else "keys"


class StroopTest(CognitiveTest):
    """State machine: instructions -> rest countdown -> stimuli -> summary."""

    NAME = "stroop"
    ConfigClass = StroopConfig

    def __init__(self, config=None, **overrides):
        super().__init__(config, **overrides)
        cfg = self.config
        self._pool_hexes = dict(COLOR_POOL)
        if cfg.color_hexes:
            self._pool_hexes.update(cfg.color_hexes)
        names = list(cfg.color_pool)[:cfg.n_colors]
        self.colors = [(name, self._pool_hexes.get(name, "#ffffff"))
                       for name in names]
        self.keys = {key: name for key, name in zip(RESPONSE_KEYS, names)}
        self.rng = random.Random(cfg.seed or None)
        self.state = "instructions"
        self._trial = None          # dict describing the current stimulus
        self._trial_started = 0.0   # monotonic reference for reaction times
        self._test_started = None    # monotonic reference for test_time
        self._deadline = None
        self._static_ids = []       # legend / countdown canvas items

        # Voice input (optional; keyboard always works as a fallback).
        self._spotter = None
        self._heard = ""            # Last word recognized, shown under the legend.
        self._cal_results = {}      # voice calibration: color -> "ok"/"missed"
        self._pending = None        # Word heard; judgment held until speech ends.
        self._highlight = None      # Color swatch to highlight (last answer).
        if cfg.response_mode == "voice":
            from .voice import VoiceSpotter
            self._spotter = VoiceSpotter(
                words=[name for name, _ in self.colors],
                model_override=cfg.vosk_model,
                device=cfg.mic_device if cfg.mic_device >= 0 else None,
                debug=getattr(cfg, "voice_debug", False))

    # ------------------------------------------------------------- phases

    def _begin(self):
        self.state = "instructions"
        self._end_marked = False
        self.mark("instructions")
        if self._spotter is not None:
            self._spotter.start()
            self._spotter.paused = True  # Not listening yet.
        self._draw_instructions()

    def _draw_instructions(self):
        self._clear_all()
        if self._spotter is not None:
            lines = [
                "STROOP TEST",
                "",
                "A color word will appear on screen.",
                "Say the INK color out loud",
                "(the number keys also work).",
            ]
        else:
            lines = [
                "STROOP TEST",
                "",
                "A color word will appear on screen.",
                "Press the number key that matches the INK color,",
                "not what the word says.",
            ]
        lines += [
            "",
            "SPACE to start" + (f"  ·  {self.config.rest_time:.0f} s of rest first"
                                if self.config.rest_time > 0 else ""),
        ]
        if self._spotter is not None and self.config.voice_calibration:
            lines.append("Voice calibration runs first (S skips it,"
                         " +/- changes the number of colors).")
        lines.append("ESC to quit")
        self.show_text(lines)

    def _handle_key(self, keysym: str):
        if keysym == "space" and self.state == "instructions":
            self._after_instructions()
        elif keysym == "s" and self.state == "calibration":
            self._start_rest()          # Deliberately skip the calibration.
        elif keysym in ("plus", "equal", "KP_Add") and self.state == "calibration":
            self._resize_colors(+1)     # Listen for one more color.
        elif keysym in ("minus", "KP_Subtract") and self.state == "calibration":
            self._resize_colors(-1)     # Listen for one color fewer.
        elif keysym == "space" and self.state == "rest":
            self._start_test()          # Skip the remaining rest time.
        elif keysym == "space" and self.state == "summary":
            self._restart()             # Run the whole test again.
        elif self.state == "stimulus" and keysym in self.keys:
            self._respond(self.keys[keysym])

    def _resize_colors(self, delta: int):
        """Calibration only: grow/shrink the active color set (+/- keys)."""
        pool = list(self.config.color_pool)
        new_n = max(2, min(len(pool), self.config.n_colors + delta))
        if new_n == self.config.n_colors:
            return
        self.config.n_colors = new_n
        names = pool[:new_n]
        self.colors = [(name, self._pool_hexes.get(name, "#ffffff"))
                       for name in names]
        self.keys = {key: name for key, name in zip(RESPONSE_KEYS, names)}
        self._cal_index = 0
        self._cal_attempt = 0
        self._cal_results = {}
        self._cal_until = time.monotonic() + CALIBRATION_TIME
        if self._spotter is not None:
            self._spotter.set_words(names)
        self._heard = ""
        self._draw_calibration()

    def _after_instructions(self):
        if self._spotter is not None and self.config.voice_calibration:
            self._start_calibration()
        else:
            self._start_rest()

    # --------------------------------------------------- voice calibration

    def _start_calibration(self):
        self.state = "calibration"
        self.mark("calibration")
        self._cal_index = 0
        self._cal_attempt = 0
        self._cal_until = time.monotonic() + CALIBRATION_TIME
        if self._spotter is not None:
            self._spotter.paused = False  # Listening during calibration.
        self._draw_calibration()
        self.after(100, self._cal_tick)

    def _cal_tick(self):
        if self.state != "calibration":
            return
        spotter = self._spotter
        if not spotter.ready:
            if spotter.error:
                self._heard = ""
                self._start_rest()  # Voice unavailable: fall back to keys.
                return
            self._draw_calibration()   # Updates the status line.
            self.after(200, self._cal_tick)
            return
        expected = self.colors[self._cal_index][0]
        for word, _ts in spotter.drain_words():
            self._heard = word
            if word == expected:
                self._cal_results[expected] = "ok"
                self._advance_calibration()
                return
        if time.monotonic() >= self._cal_until:
            self._cal_attempt += 1
            if self._cal_attempt >= CALIBRATION_ATTEMPTS:
                self._cal_results[expected] = "missed"
                self._advance_calibration()
                return
            self._cal_until = time.monotonic() + CALIBRATION_TIME
        self._draw_calibration()
        self.after(100, self._cal_tick)

    def _advance_calibration(self):
        self._cal_index += 1
        self._cal_attempt = 0
        self._cal_until = time.monotonic() + CALIBRATION_TIME
        if self._spotter is not None:
            ack = getattr(self._spotter, "ack", None)
            if callable(ack):
                ack()  # Allow the same color again in the next round.
        if self._cal_index >= len(self.colors):
            self._start_rest()
            return
        self._draw_calibration()
        self.after(100, self._cal_tick)

    def _draw_calibration(self):
        self._clear_all()
        width = self._canvas.winfo_width() or 1000
        height = self._canvas.winfo_height() or 700
        spotter = self._spotter
        self.show_text(["VOICE CALIBRATION", "",
                        "Say each color out loud when it is highlighted."],
                       y=0.2, color=self.config.foreground)
        if spotter is not None and not spotter.ready:
            self._canvas.create_text(
                width / 2, height * 0.45, text=spotter.status,
                fill="#8a8a92", font=self.font(18))
            return
        # Progress: one swatch per color, ✓ / ✗ / highlighted if current.
        n = len(self.colors)
        cell = width / (n + 1)
        for index, (name, hex_color) in enumerate(self.colors):
            x = cell * (index + 1)
            current = index == self._cal_index
            outline = self.config.foreground if current else ""
            self._canvas.create_rectangle(
                x - cell * 0.4, height * 0.5, x + cell * 0.4, height * 0.5 + 60,
                fill=hex_color, outline=outline, width=4)
            mark = self._cal_results.get(name, "")
            mark_text = "✓" if mark == "ok" else "✗" if mark == "missed" else ""
            self._canvas.create_text(
                x, height * 0.5 + 80, text=mark_text, fill=self.config.foreground,
                font=self.font(18))
        name, hex_color = self.colors[self._cal_index]
        self._canvas.create_text(
            width / 2, height * 0.32, text=f"Say: {name.upper()}",
            fill=hex_color, font=self.font(max(32, height // 12)))
        # Microphone level bar, so it is obvious the mic is alive.
        level = spotter.level if spotter is not None else 0.0
        bar_width = width * 0.5
        self._canvas.create_rectangle(
            width / 2 - bar_width / 2, height * 0.68,
            width / 2 + bar_width / 2, height * 0.68 + 14,
            outline="#5a5a62", width=1)
        self._canvas.create_rectangle(
            width / 2 - bar_width / 2, height * 0.68,
            width / 2 - bar_width / 2 + bar_width * min(1.0, level * 4),
            height * 0.68 + 14, fill="#40c060", outline="")
        self._canvas.create_text(
            width / 2, height * 0.78,
            text="S to skip calibration  ·  ESC to quit",
            fill="#8a8a92", font=self.font(14, bold=False))

    def _start_rest(self):
        if self.state == "calibration":
            self.mark("calibration end")
        if self.config.rest_time <= 0:
            self._start_test()
            return
        self.state = "rest"
        self.mark("rest")
        self._rest_until = time.monotonic() + self.config.rest_time
        if self._spotter is not None:
            self._spotter.paused = True  # Not listening during the break.
        self._draw_rest()
        self.after(100, self._rest_tick)

    def _rest_tick(self):
        if self.state != "rest":
            return
        remaining = self._rest_until - time.monotonic()
        if remaining <= 0:
            self._start_test()
            return
        self._update_rest(remaining)
        self.after(100, self._rest_tick)

    def _start_test(self):
        if self.state == "rest":
            self.mark("rest end")
        self.state = "stimulus"
        self._test_started = time.monotonic()
        self.mark("test start", at=self._test_started, config=self.config.to_dict())
        if self._spotter is not None:
            self._spotter.paused = True  # Listening resumes with the first word.
            self._spotter.drain_words()  # Drop calibration leftovers.
            self._spotter.drain_finals()
            ack = getattr(self._spotter, "ack", None)
            if callable(ack):
                ack()
        self._clear_static()
        self._draw_chrome()
        self._next_stimulus()
        self.after(50, self._tick)

    # ----------------------------------------------------------- stimulus

    def _stimulus_duration(self) -> float:
        """Response window for the next word; variants may adapt it."""
        return self.config.stimulus_time

    def _next_stimulus(self):
        if self.state == "summary":
            return  # Test over; ignore stale scheduling.
        cfg = self.config
        self._pending = None
        self._highlight = None
        self._heard = ""    # Fresh trial: nothing heard yet.
        if self._spotter is not None:
            # Before a new word: wipe every buffer (words and stale
            # utterance-ends), consume the previous utterance, and only
            # then re-enable the input — recognition is live exactly while
            # a word is on screen and unanswered.
            self._spotter.drain_words()
            self._spotter.drain_finals()
            ack = getattr(self._spotter, "ack", None)
            if callable(ack):
                ack()
            self._spotter.paused = False
            if getattr(self._spotter, "debug", False):
                print("[voice] --- input armed for the next word ---",
                      flush=True)
        names = [name for name, _hex in self.colors]
        word = self.rng.choice(names)
        if self.rng.random() < cfg.congruent_ratio:
            ink = word
        else:
            ink = self.rng.choice([n for n in names if n != word])
        self._trial = {"word": word, "ink": ink, "congruent": ink == word}
        self._trial_started = time.monotonic()
        duration = self._stimulus_duration()
        self._deadline = (self._trial_started + duration
                          if duration > 0 else None)
        self.state = "stimulus"
        self._draw_stimulus()
        self.mark("stimulus", at=self._trial_started, trial=len(self.events) + 1, **self._trial)

    def _tick(self):
        """Periodic check for stimulus timeouts and the end of the test."""
        if self.result is not None:
            return
        if self.state == "stimulus":
            self._poll_voice()
            # Polling can finish the trial and enter feedback in this call.
            # Recheck the state before considering a timeout.
            if self.state == "stimulus" \
                    and self._deadline is not None \
                    and time.monotonic() >= self._deadline \
                    and self._pending is None:
                self._respond(None)  # Timed out.
        elif self._spotter is not None and self.state != "calibration":
            # Recognition is blocked whenever no color is on screen: anything
            # heard in between is dropped, never queued for the next word.
            self._spotter.drain_words()
        self._update_chrome()
        if (self._test_started is not None
                and time.monotonic() - self._test_started >= self.config.test_time):
            self._show_summary()
            return
        self.after(50, self._tick)

    def _poll_voice(self):
        """Consume recognized words.

        The reaction clock stops at the audio timestamp of the detected
        word, but the judgment is held until the utterance ends (the
        spotter's silence endpoint, capped by ``voice_max_wait``) so the
        feedback does not interrupt the participant mid-word.
        """
        if self._spotter is None or not self._spotter.ready:
            return
        debug = getattr(self._spotter, "debug", False)
        for word, detected_at in self._spotter.drain_words():
            lag = detected_at - self._trial_started
            if debug:
                print(f"[voice] test received {word!r} (+{lag:.3f} s after onset)",
                      flush=True)
            if lag < getattr(self.config, "min_voice_lag", MIN_VOICE_LAG):
                # Nobody can name a color this fast: carried over from
                # before the word was shown.
                if debug:
                    print(f"[voice]   dropped: only +{lag * 1000:.0f} ms "
                          "after onset — carried over from the previous word")
                continue
            if word in self.keys.values() and self._pending is None:
                self._pending = word
                # Stop the per-word timer now, not when speech-end feedback
                # is finally shown. It stays cancelled for this stimulus.
                self._deadline = None
                self._pending_rt = round(detected_at - self._trial_started, 4)
                self._pending_ts = detected_at
                self._pending_since = time.monotonic()
                # Answer received: pause the input and throw away anything
                # already buffered until the next word is shown.
                self._spotter.paused = True
                self._spotter.drain_words()
                # Show right away what was heard: highlight the swatch and
                # write the word under the legend.
                self._heard = word
                self._highlight = word
                self._draw_stimulus()
                if debug:
                    print("[voice]   accepted as the answer; timeout cancelled; "
                          "input paused", flush=True)
            elif debug:
                print("[voice]   ignored (not while an answer is pending)")
        finals = self._spotter.drain_finals()
        if self._pending is None:
            return
        # "Finished speaking" comes from whichever signal arrives first:
        # the recognizer's silence endpoint, or the mic going quiet right
        # after the word (much faster; the endpointer can lag 0.5 s+).
        speech_idle = None
        idle_fn = getattr(self._spotter, "speech_idle", None)
        if callable(idle_fn):
            try:
                speech_idle = idle_fn()
            except Exception:
                speech_idle = None
        utterance_ended = any(ts >= self._pending_ts - 0.02 for ts in finals)
        mic_went_quiet = (speech_idle is not None
                          and speech_idle >= self.config.voice_quiet_s)
        waited_too_long = (time.monotonic() - self._pending_since
                           > self.config.voice_max_wait)
        if utterance_ended or mic_went_quiet or waited_too_long:
            word, rt = self._pending, self._pending_rt
            self._pending = None
            if getattr(self._spotter, "debug", False):
                why = ("utterance ended" if utterance_ended else
                       "mic went quiet" if mic_went_quiet else "wait cap hit")
                print(f"[voice]   judging {word!r} ({why})", flush=True)
            self._respond(word, rt=rt)

    def _log_outcome(self, response, correct: bool, rt: float) -> None:
        """Terminal debug line for a judged trial (voice mode)."""
        if self._spotter is None or not getattr(self._spotter, "debug", False):
            return
        outcome = "timeout" if response is None else (
            "CORRECT" if correct else f"wrong (said {response!r}, "
            f"ink was {self._trial['ink']!r})")
        print(f"[voice] trial {len(self.events) + 1}: {outcome}, "
              f"rt={rt:.3f} s", flush=True)

    def _close_trial(self, response, next_state: str) -> bool:
        """Claim this stimulus once, before recording or playing feedback."""
        if self.result is not None or self.state != "stimulus":
            return False
        if response is None and self._pending is not None:
            return False  # A detected answer has already cancelled timeout.
        self.state = next_state
        self._deadline = None
        self._pending = None
        if self._spotter is not None:
            self._spotter.paused = True
            self._spotter.drain_words()
            self._spotter.drain_finals()
            ack = getattr(self._spotter, "ack", None)
            if callable(ack):
                ack()
        return True

    def _respond(self, response, rt=None):
        if not self._close_trial(response, "isi"):
            return
        if rt is None:
            rt = time.monotonic() - self._trial_started
        correct = response is not None and response == self._trial["ink"]
        self._log_outcome(response, correct, rt)
        self._mark_response(response, correct, rt)
        self.record(
            kind="trial",
            word=self._trial["word"],
            ink=self._trial["ink"],
            congruent=self._trial["congruent"],
            response=response or "",
            correct=correct,
            timed_out=response is None,
            rt=round(rt, 4) if response is not None else None,
        )
        self._highlight = response if response is not None else None
        if response is None:
            self._heard = ""    # Nothing was accepted: show no heard word.
        self._draw_fixation()
        self.after(max(1, int(self.config.isi * 1000)), self._next_stimulus)

    def _mark_response(self, response, correct, rt):
        # Spoken answers use the detection time (RT), not delayed feedback time.
        self.mark("timeout" if response is None else "correct" if correct else "wrong",
                  at=self._trial_started + rt, trial=len(self.events) + 1,
                  response=response or "", rt=rt if response is not None else None,
                  **self._trial)

    # ------------------------------------------------------------ drawing

    def _draw_stimulus(self):
        self._clear_all()
        hex_color = dict(self.colors)[self._trial["ink"]]
        width = self._canvas.winfo_width() or 1000
        height = self._canvas.winfo_height() or 700
        size = self.config.font_size or max(40, min(140, height // 6))
        self._canvas.create_text(
            width / 2, height * 0.42, text=self._trial["word"].upper(),
            fill=hex_color, font=self.font(size))
        self._draw_legend()
        self._draw_chrome()

    def _draw_fixation(self):
        self._clear_all()
        width = self._canvas.winfo_width() or 1000
        height = self._canvas.winfo_height() or 700
        self._canvas.create_text(width / 2, height * 0.42, text="+",
                                 fill=self.config.foreground,
                                 font=self.font(max(24, height // 20)))
        self._draw_legend()
        self._draw_chrome()

    def _draw_legend(self):
        """Key -> color legend along the bottom edge."""
        if not self.config.show_legend:
            return
        width = self._canvas.winfo_width() or 1000
        height = self._canvas.winfo_height() or 700
        n = len(self.colors)
        cell = width / (n + 1)
        for index, (name, hex_color) in enumerate(self.colors):
            x = cell * (index + 1)
            highlight = name == self._highlight
            if highlight:
                # Thick white halo behind the answered color's swatch; it
                # stays as long as the check/cross of that answer is up.
                self._canvas.create_rectangle(
                    x - cell * 0.42 - 9, height - 96 - 9,
                    x + cell * 0.42 + 9, height - 40 + 9,
                    fill="#ffffff", outline="#ffffff", width=4)
            self._canvas.create_rectangle(
                x - cell * 0.42, height - 96, x + cell * 0.42, height - 40,
                fill=hex_color,
                outline="#ffffff" if highlight else "",
                width=4 if highlight else 0)
            self._canvas.create_text(
                x, height - 68, text=RESPONSE_KEYS[index],
                fill="#101014" if name in ("yellow", "cyan", "pink") else "#f5f5f5",
                font=self.font(22 if highlight else 20))
            self._canvas.create_text(
                x, height - 24, text=name,
                fill=self.config.foreground if highlight else "#8a8a92",
                font=self.font(15 if highlight else 13, bold=highlight))
        # Voice mode: while the voice input is not up, say so (errors must
        # never be silent).  Once it listens, show the word that was heard,
        # in its own color, below the legend.
        if (self.config.response_mode == "voice" and self._spotter is not None
                and not self._spotter.ready):
            status = (f"voice: {self._spotter.error}" if self._spotter.error
                      else f"voice: {self._spotter.status}")
            self._canvas.create_text(
                width / 2, height - 140, text=status,
                fill="#f2bd62" if self._spotter.error else "#8a8a92",
                font=self.font(16))
        elif (self.config.response_mode == "voice" and self._heard
              and self.state in ("stimulus", "feedback", "isi")):
            hex_color = dict(self.colors).get(self._heard, self.config.foreground)
            self._canvas.create_text(
                width / 2, height - 140, text=f"heard: {self._heard}",
                fill=hex_color, font=self.font(20, bold=True))

    def _draw_chrome(self):
        """Elapsed-time readout in the top-right corner."""
        width = self._canvas.winfo_width() or 1000
        self._static_ids.append(self._canvas.create_text(
            width - 24, 24, anchor="e", text="", fill="#8a8a92",
            font=self.font(14, bold=False)))
        self._time_id = self._static_ids[-1]
        self._update_chrome()

    def _update_chrome(self):
        elapsed = (0.0 if self._test_started is None
                   else time.monotonic() - self._test_started)
        remaining = max(0.0, self.config.test_time - elapsed)
        try:
            self._canvas.itemconfigure(self._time_id, text=f"{remaining:4.0f} s")
        except Exception:
            pass

    def _draw_rest(self):
        self._clear_all()
        width = self._canvas.winfo_width() or 1000
        height = self._canvas.winfo_height() or 700
        self.show_text(["Get ready…", "", "Press SPACE to skip the rest"],
                       y=0.35, color=self.config.foreground)
        self._rest_id = self._canvas.create_text(
            width / 2, height * 0.62, text="", fill=self.config.foreground,
            font=self.font(max(40, height // 8)))
        self._draw_legend()
        self._update_rest(self.config.rest_time)

    def _update_rest(self, remaining):
        try:
            self._canvas.itemconfigure(self._rest_id, text=f"{remaining:0.0f}")
        except Exception:
            pass

    def _redraw_static(self):
        """Window mapped/resized: redraw whatever phase is on screen.

        The first Configure event arrives after the window has its real
        size, so this is also what makes the initial screen visible.
        """
        if self.state == "instructions":
            self._draw_instructions()
        elif self.state == "calibration":
            self._draw_calibration()
        elif self.state == "summary":
            self._draw_summary()
        elif self.state in ("stimulus", "isi"):
            self._draw_stimulus() if self.state == "stimulus" else self._draw_fixation()
        elif self.state == "rest":
            self._draw_rest()

    # ----------------------------------------------------- summary/restart

    def _show_summary(self):
        """Test time is over: freeze on the stats until SPACE or ESC."""
        self._mark_end()
        self.state = "summary"
        if self._spotter is not None:
            self._spotter.paused = True  # Test over: stop listening.
            self._spotter.drain_words()
        self._draw_summary()

    def _summary_lines(self) -> list:
        s = self._summary()
        lines = ["RESULTS", ""]
        trials = s["n_trials"]
        lines.append(f"Trials: {trials}    Correct: {s['n_correct']}    "
                     f"Missed: {s['n_timeouts']}")
        if trials:
            lines.append(f"Accuracy: {s['accuracy'] * 100:.1f}%")
        if s["mean_rt_s"] is not None:
            lines.append(f"Reaction time: {s['mean_rt_s'] * 1000:.0f} ms mean"
                         f" · {s['median_rt_s'] * 1000:.0f} ms median")
        if s["stroop_effect_s"] is not None:
            lines.append(f"Stroop effect (incongruent − congruent): "
                         f"{s['stroop_effect_s'] * 1000:.0f} ms")
        if s.get("voice_calibration"):
            ok = sum(1 for v in s["voice_calibration"].values() if v == "ok")
            lines.append(f"Voice calibration: {ok}/{len(s['voice_calibration'])} "
                         "colors recognized")
        return lines

    def _draw_summary(self):
        self._clear_all()
        lines = self._summary_lines()
        lines += ["", "SPACE to run it again  ·  ESC to quit"]
        self.show_text(lines)

    def _restart(self):
        """From the very beginning: fresh events, clock and instructions."""
        self.events = []
        self._reset_markers()
        self._heard = ""
        self._highlight = None
        self._pending = None
        self._test_started = None    # clock restarts when the test phase does
        self._deadline = None
        self._started_monotonic = time.monotonic()
        self._started_iso = time.strftime("%Y-%m-%d %H:%M:%S")
        self._begin()

    def _clear_all(self):
        self._static_ids = []
        self._canvas.delete("all")

    def _clear_static(self):
        for item in list(self._static_ids):
            try:
                self._canvas.delete(item)
            except Exception:
                pass
        self._static_ids = []

    def _finish(self, aborted: bool = False) -> None:
        if self.result is not None:
            return
        self._mark_end(aborted=aborted)
        if self._spotter is not None:
            self._spotter.stop()
        super()._finish(aborted)

    def _mark_end(self, aborted=False):
        if not getattr(self, "_end_marked", False):
            if aborted:
                self.mark("test aborted", phase=self.state)
            self.mark("test end", aborted=aborted)
            self._end_marked = True

    # ------------------------------------------------------------ results

    def _summary(self) -> dict:
        trials = [event for event in self.events if event.get("kind") == "trial"]
        responded = [t for t in trials if not t["timed_out"]]
        correct = [t for t in responded if t["correct"]]

        def rt_stats(subset):
            rts = sorted(t["rt"] for t in subset if t["rt"] is not None)
            if not rts:
                return None, None
            mid = len(rts) // 2
            median = rts[mid] if len(rts) % 2 else (rts[mid - 1] + rts[mid]) / 2
            return round(sum(rts) / len(rts), 4), round(median, 4)

        def condition(congruent):
            subset = [t for t in responded if t["congruent"] == congruent]
            hits = [t for t in subset if t["correct"]]
            mean_rt, median_rt = rt_stats(hits)
            return {
                "n_trials": len(subset),
                "n_correct": len(hits),
                "accuracy": round(len(hits) / len(subset), 4) if subset else None,
                "mean_rt_s": mean_rt,
                "median_rt_s": median_rt,
            }

        congruent = condition(True)
        incongruent = condition(False)
        # Overall RTs over every valid response; per condition over correct
        # trials only (standard Stroop practice).
        overall = rt_stats(responded)
        effect = None
        if congruent["mean_rt_s"] is not None and incongruent["mean_rt_s"] is not None:
            effect = round(incongruent["mean_rt_s"] - congruent["mean_rt_s"], 4)
        summary = {
            "n_trials": len(trials),
            "n_responses": len(responded),
            "n_timeouts": len(trials) - len(responded),
            "n_correct": len(correct),
            "accuracy": round(len(correct) / len(trials), 4) if trials else None,
            "mean_rt_s": overall[0],
            "median_rt_s": overall[1],
            "congruent": congruent,
            "incongruent": incongruent,
            "stroop_effect_s": effect,
        }
        if self._cal_results:
            summary["response_mode"] = self.config.response_mode
            summary["voice_calibration"] = dict(self._cal_results)
        return summary
