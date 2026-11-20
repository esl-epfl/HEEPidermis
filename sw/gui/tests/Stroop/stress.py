"""Stroop test with adaptive time pressure (the GUI's Stroop entry).

Same task as the plain Stroop (press the number key of the INK color) but
with pressure added on top:

* Wrong answer  -> loud "meeeeh" buzzer and a big red cross.
* Right answer  -> short, quiet cling, a green checkmark and a smiley.
* A score bar fills toward a fixed target, red below halfway and green above.
* Correct answers earn points; wrong answers and timeouts lose points.
* Smoothed score gently adjusts the next response window and feedback time,
  with increasing pressure near the target and recovery when score is low.
* A clock ticks once per second in the background, and the remaining test
  time runs big in the top-left corner.

Run standalone from the repository root:

    python3 sw/gui/tests/run_test.py stroop --test-time 120

Escape aborts (and silences everything) while keeping the trials collected.
"""

from __future__ import annotations

import math
import time

from ..common.audio import SoundPlayer
from .spec import StroopStressConfig
from .stroop import StroopTest

SMILEY = "#f0c020"


class StroopStressTest(StroopTest):
    """Stroop state machine with score, adaptive pacing and sound feedback."""

    NAME = "stroop"
    ConfigClass = StroopStressConfig

    def __init__(self, config=None, **overrides):
        super().__init__(config, **overrides)
        self._player = SoundPlayer()
        self._feedback_process = None
        self._reset_difficulty()

    def _reset_difficulty(self):
        self._score = self.config.score_start
        self._isi = self.config.isi_start
        self._last_delta = 0
        self._last_outcome = "correct"
        self._smoothed_fraction = self._score / self.config.score_target
        self._score_trend = 0.0
        self._pressure = 0.0
        self._stimulus_time = self.config.stimulus_time
        self._feedback_time = self.config.feedback_time
        if self.config.adaptive_timing:
            self._stimulus_time = min(self.config.stimulus_max,
                                      max(self.config.stimulus_min, self._stimulus_time))
            self._feedback_time = min(self.config.feedback_max,
                                      max(self.config.feedback_min, self._feedback_time))
        self._trial_stimulus_time = self._stimulus_time
        self._trial_feedback_time = self._feedback_time
        self._trial_isi = self._isi

    # ------------------------------------------------------------- phases

    def _draw_instructions(self):
        self._clear_all()
        base = (self.config.correct_bonus_ms if self.config.correct_bonus_ms
                is not None else self.config.timeout_penalty_ms)
        self.show_text([
            "STROOP TEST · STRESS",
            "",
            ("Say the INK color (number keys also work)."
             if self.config.response_mode == "voice" else
             "Press the number key of the INK color."),
            "",
            f"Correct: +{base} minus your time in ms"
            f" (at least +{self.config.correct_min_gain})",
            f"Wrong: minus your time in ms  ·  "
            f"timeout: -{self.config.timeout_penalty_ms}",
            f"Fill the bar: {self.config.score_start:,} / {self.config.score_target:,} points.",
            "Keep it green — stay above halfway!",
            ("The pace adjusts gradually as you play."
             if self.config.adaptive_timing else "Answer before the word's time runs out."),
            "",
            "SPACE to start" + (f"  ·  {self.config.rest_time:.0f} s of rest first"
                                if self.config.rest_time > 0 else ""),
            "ESC to quit",
        ])

    def _start_test(self):
        self._reset_difficulty()
        super()._start_test()
        # One continuous accelerating tick track (1 Hz -> 3 Hz) instead of a
        # process per tick: it never loses the audio device to the
        # buzzer/cling, so the clock keeps ticking under the feedback sounds.
        if self.config.tick and self.config.sounds:
            self._tick_process = self._player.play_tick_track(self.config.test_time)

    # ----------------------------------------------------------- stimulus

    def _stimulus_duration(self) -> float:
        return self._trial_stimulus_time

    def _next_stimulus(self):
        if self.result is not None or self.state == "summary":
            return
        # A short feedback symbol must not let the next word start during
        # the previous buzzer (which lasts longer than the success sound).
        if self._feedback_process is not None and self._feedback_process.poll() is None:
            self.after(20, self._next_stimulus)
            return
        self._feedback_process = None
        self._trial_stimulus_time = self._stimulus_time
        self._trial_feedback_time = self._feedback_time
        self._trial_isi = self._isi
        super()._next_stimulus()

    def _adapt_timing(self, score_delta: int):
        """Update only future timings; scores and current deadlines are untouched."""
        cfg = self.config
        if not cfg.adaptive_timing:
            return
        fraction = self._score / cfg.score_target
        self._smoothed_fraction += cfg.adaptation_smoothing * (fraction - self._smoothed_fraction)
        self._score_trend += cfg.adaptation_smoothing * (
            score_delta / cfg.score_target - self._score_trend)
        # A falling score should ease pressure before it collapses, and a
        # sustained winning streak should tighten before it overshoots.
        projected = self._smoothed_fraction + cfg.adaptation_lookahead * self._score_trend
        offset = projected - 0.5
        distance = abs(offset)
        # Gentle in the central band, progressively stronger toward 90%.
        if distance <= cfg.score_band:
            strength = 0.35 * distance / cfg.score_band
        else:
            strength = min(1.0, 0.35 + 0.65 * (distance - cfg.score_band)
                           / (0.4 - cfg.score_band))
        pressure = math.copysign(strength, offset)
        self._pressure = pressure
        factor = 1.0 - cfg.stimulus_step * pressure
        self._stimulus_time = min(cfg.stimulus_max,
                                  max(cfg.stimulus_min, self._stimulus_time * factor))
        self._feedback_time = min(cfg.feedback_max, max(
            cfg.feedback_min, self._feedback_time - cfg.feedback_step * pressure))
        # The gap follows the same bounded adjustment; no independent decay
        # accelerates the test while the controller is trying to ease it.
        self._isi = min(cfg.isi_start, max(cfg.isi_min, self._isi * factor))

    def _respond(self, response, rt=None):
        """Score the answer, show feedback, then adapt the pace."""
        if not self._close_trial(response, "feedback"):
            return
        if rt is None:
            rt = time.monotonic() - self._trial_started
        correct = response is not None and response == self._trial["ink"]
        self._log_outcome(response, correct, rt)
        self._mark_response(response, correct, rt)
        rt_ms = int(round(rt * 1000))
        if response is None:
            outcome, delta = "timeout", -self.config.timeout_penalty_ms
            sound = "buzz"
        elif response == self._trial["ink"]:
            # Fast answers earn more, but being right always pays: the gain
            # is floored at `correct_min_gain` even if the answer was slow.
            # The bonus defaults to the timeout penalty (X points at stake,
            # X points for an instant answer).
            base = (self.config.correct_bonus_ms if self.config.correct_bonus_ms
                    is not None else self.config.timeout_penalty_ms)
            outcome = "correct"
            delta = max(self.config.correct_min_gain, base - rt_ms)
            sound = "cling"
        else:
            outcome, delta = "wrong", -rt_ms
            sound = "buzz"
        self._score += delta
        self._last_delta = delta
        self._last_outcome = outcome
        self._adapt_timing(delta)
        self.record(
            kind="trial",
            word=self._trial["word"],
            ink=self._trial["ink"],
            congruent=self._trial["congruent"],
            response=response or "",
            correct=outcome == "correct",
            timed_out=response is None,
            rt=round(rt_ms / 1000, 4) if response is not None else None,
            outcome=outcome,
            score_delta=delta,
            score=self._score,
            score_fraction=round(self._score / self.config.score_target, 4),
            stimulus_time_s=round(self._trial_stimulus_time, 4),
            feedback_time_s=round(self._trial_feedback_time, 4),
            isi_s=round(self._trial_isi, 4),
            next_stimulus_time_s=round(self._stimulus_time, 4),
            next_feedback_time_s=round(self._feedback_time, 4),
            next_isi_s=round(self._isi, 4),
            difficulty_pressure=round(self._pressure, 4),
            smoothed_score_fraction=round(self._smoothed_fraction, 4),
            score_trend=round(self._score_trend, 4),
        )
        if self.config.voice_debug:
            print(f"[stress] score {self._score}/{self.config.score_target} "
                  f"({self._score / self.config.score_target:.0%}); "
                  f"window {self._trial_stimulus_time:.3f}s -> {self._stimulus_time:.3f}s; "
                  f"feedback {self._trial_feedback_time:.3f}s -> {self._feedback_time:.3f}s",
                  flush=True)
        # Legend halo: mark the color that was just answered (none on a
        # timeout — nothing was recognized, nothing is highlighted).
        self._highlight = response
        if response is None:
            self._heard = ""    # Nothing was accepted: show no heard word.
        # Draw the cross/check FIRST and start the sound immediately after,
        # so the symbol and the buzz/cling land together on screen.
        self._draw_feedback(outcome)
        if self.config.sounds:
            self._feedback_process = self._player.play(sound)
        # Freeze feedback duration for this trial; adjustments apply next time.
        self.after(max(1, int(self._trial_feedback_time * 1000)),
                   self._hide_feedback)

    def _hide_feedback(self):
        if self.result is not None or self.state != "feedback":
            return
        self.state = "isi"
        self._draw_fixation()
        self.after(max(1, int(self._trial_isi * 1000)), self._next_stimulus)
        if not self.config.adaptive_timing:
            self._isi = max(self.config.isi_min, self._isi * self.config.isi_decay)

    # ------------------------------------------------------------ drawing

    def _draw_chrome(self):
        """Remaining time and a target bar with a fixed halfway marker.

        The y position follows the font size so the tall glyphs never clip
        against the top edge, whatever the window height.
        """
        width = self._canvas.winfo_width() or 1000
        height = self._canvas.winfo_height() or 700
        size = max(18, min(height // 18, width // 30))
        big = self.font(size)
        y = size + 14  # Text anchor is the center: keep the glyphs clear of the edge.
        self._time_id = self._canvas.create_text(
            28, y, anchor="w", text="", fill=self.config.foreground, font=big)
        self._score_id = self._canvas.create_text(
            width - 28, y, anchor="e", text="", fill=self.config.foreground,
            font=big)
        x0, x1 = width * 0.25, width - 28
        y0, y1 = y + size * 0.75, y + size * 0.75 + max(14, height * 0.025)
        self._bar_bounds = (x0, y0, x1, y1)
        self._canvas.create_rectangle(x0, y0, x1, y1, fill="#292932",
                                      outline="#777782", tags="score-bar-track")
        self._bar_id = self._canvas.create_rectangle(
            x0, y0, x0, y1, fill="#40c060", outline="", tags="score-bar-fill")
        middle = (x0 + x1) / 2
        self._canvas.create_line(middle, y0 - 4, middle, y1 + 4,
                                 fill=self.config.foreground, width=2, tags="score-bar-midpoint")
        self._canvas.create_text(middle, y1 + 7, text="50%", anchor="n",
                                 fill=self.config.foreground, font=self.font(max(10, size // 3)))
        self._update_chrome()

    def _update_chrome(self):
        elapsed = (0.0 if self._test_started is None
                   else time.monotonic() - self._test_started)
        remaining = max(0.0, self.config.test_time - elapsed)
        fraction = self._score / self.config.score_target
        color = "#40c060" if fraction >= 0.5 else "#e04040"
        try:
            self._canvas.itemconfigure(self._time_id,
                                       text=f"{remaining:5.1f} s")
            self._canvas.itemconfigure(self._score_id,
                                       text=f"{self._score:,} / {self.config.score_target:,}  ({fraction:.0%})",
                                        fill=color)
            x0, y0, x1, y1 = self._bar_bounds
            self._canvas.coords(self._bar_id, x0, y0,
                                x0 + (x1 - x0) * min(1.0, max(0.0, fraction)), y1)
            self._canvas.itemconfigure(self._bar_id, fill=color)
        except Exception:
            pass

    def _draw_feedback(self, outcome: str):
        """Big red cross, or green checkmark + smiley, centered on screen."""
        self._clear_all()
        width = self._canvas.winfo_width() or 1000
        height = self._canvas.winfo_height() or 700
        cx, cy = width / 2, height * 0.42
        arm = min(width, height) * 0.09
        if outcome == "correct":
            self._canvas.create_line(cx - arm, cy, cx - arm * 0.25, cy + arm * 0.75,
                                     cx + arm * 1.1, cy - arm * 0.9,
                                     fill="#40c060", width=max(6, int(arm / 5)),
                                     capstyle="round", joinstyle="round")
            self._draw_smiley(cx, cy + arm * 2.6, arm * 0.85)
        else:
            self._canvas.create_line(cx - arm, cy - arm, cx + arm, cy + arm,
                                     fill="#e04040", width=max(8, int(arm / 4)),
                                     capstyle="round")
            self._canvas.create_line(cx + arm, cy - arm, cx - arm, cy + arm,
                                     fill="#e04040", width=max(8, int(arm / 4)),
                                     capstyle="round")
        # Points earned/deducted, in big letters next to the symbol.
        color = "#40c060" if self._last_delta > 0 else "#e04040"
        size = max(36, height // 10)
        text = f"{self._last_delta:+d}"
        px, py = cx + arm * 2.3, cy
        if px + len(text) * size * 0.62 > width - 20:
            px, py = cx, cy + arm * 2.6  # Narrow window: below the symbol.
            anchor = "center" if outcome == "wrong" else "n"
        else:
            anchor = "w"
        self._canvas.create_text(px, py, anchor=anchor, text=text,
                                 fill=color, font=self.font(size))
        # A timeout is not a wrong color: tell the participant to hurry.
        if outcome == "timeout":
            self._canvas.create_text(
                cx, cy + arm * 2.4, text="HURRY UP!",
                fill="#e04040", font=self.font(max(28, height // 16)))
        self._draw_legend()
        self._draw_chrome()

    def _draw_smiley(self, cx, cy, radius):
        self._canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                                 fill=SMILEY, outline="#7a5c00", width=2)
        eye = radius * 0.13
        offset = radius * 0.42
        for side in (-1, 1):
            self._canvas.create_oval(cx + side * offset - eye, cy - radius * 0.25 - eye,
                                     cx + side * offset + eye, cy - radius * 0.25 + eye,
                                     fill="#101014", outline="")
        mouth = radius * 0.62
        self._canvas.create_arc(cx - mouth, cy - mouth * 0.45,
                                cx + mouth, cy + mouth * 0.95,
                                start=200, extent=140, style="arc",
                                outline="#101014", width=max(3, int(radius / 10)))

    def _redraw_static(self):
        """Window mapped/resized: redraw whatever phase is on screen."""
        if self.state == "feedback":
            self._draw_feedback(self._last_outcome)
        else:
            super()._redraw_static()

    # ------------------------------------------------------------ results

    def _summary_lines(self) -> list:
        lines = super()._summary_lines()
        s = self._summary()
        lines[2:2] = [f"Score: {s['score_start']} → {s['score_final']} "
                      f"({s['score_change']:+d})"]
        return lines

    def _summary(self) -> dict:
        summary = super()._summary()
        trials = [event for event in self.events if event.get("kind") == "trial"]
        correct = [t for t in trials if t.get("outcome") == "correct"]
        wrong = [t for t in trials if t.get("outcome") == "wrong"]
        timeouts = [t for t in trials if t.get("outcome") == "timeout"]
        summary.update({
            "score_start": self.config.score_start,
            "score_final": self._score,
            "score_change": self._score - self.config.score_start,
            "n_correct_fast": len(correct),
            "n_wrong": len(wrong),
            "n_timeout": len(timeouts),
            "final_isi_s": round(self._isi, 3),
            "score_target": self.config.score_target,
            "score_fraction": round(self._score / self.config.score_target, 4),
            "final_stimulus_time_s": round(self._stimulus_time, 4),
            "final_feedback_time_s": round(self._feedback_time, 4),
        })
        return summary

    def _restart(self):
        self._player.stop()
        self._feedback_process = None
        self._reset_difficulty()
        super()._restart()

    def _finish(self, aborted: bool = False) -> None:
        self._player.stop()
        super()._finish(aborted)
