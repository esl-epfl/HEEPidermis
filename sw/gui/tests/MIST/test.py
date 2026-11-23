"""Runnable MIST adaptation: practice, rest, control and stress blocks."""

import random
import statistics
import time

from ..common.audio import SoundPlayer
from ..common.base import CognitiveTest
from ..common.visuals import GREEN, RED, draw_instructions_panel, draw_verdict
from .arithmetic import generate_problem
from .spec import Config, TITLE

DIGITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


class Test(CognitiveTest):
    NAME = "mist"
    ConfigClass = Config

    def __init__(self, config=None, **overrides):
        super().__init__(config, **overrides)
        self._player = SoundPlayer()
        self._spotter = None
        if self.config.response_mode == "voice":
            from ..common.voice import VoiceSpotter
            self._spotter = VoiceSpotter(
                DIGITS, model_override=self.config.vosk_model,
                device=None if self.config.mic_device < 0 else self.config.mic_device,
                debug=self.config.voice_debug,
                speaker_filter=self.config.speaker_filter,
                speaker_threshold=self.config.speaker_threshold)
        self._reset()

    def _reset(self):
        self.state = "instructions"
        self._rng = random.Random(self.config.seed or None)
        self._blocks = self.config.block_order.split() * self.config.repetitions
        self._block_index = -1
        self._condition = "practice"
        self._phase_end = None
        self._trial = None
        self._trial_number = 0
        self._limit = self.config.response_time
        self._correct_rts = []
        self._failures = 0
        self._cal_results = {}
        self._cal_index = 0
        self._cal_start = None
        self._cal_end = None
        self._test_started = None
        self._test_ended = None
        self._feedback_process = None
        self._voice_error = ""
        self._outcome = ""

    def _begin(self):
        if self._spotter:
            self._spotter.paused = True
            self._spotter.start()
        self.mark("instructions", config=self.config.to_dict())
        self._draw()
        self.after(20, self._tick)

    def _listen(self, active):
        if self._spotter:
            self._spotter.paused = True
            self._spotter.drain_words()
            self._spotter.drain_finals()
            self._spotter.ack()
            self._spotter.paused = not active

    def _handle_key(self, keysym):
        if keysym == "space" and self.state == "instructions":
            if self._spotter and self.config.voice_calibration:
                self.state = "calibration"
                self.mark("calibration start", color="yellow", kind="voice")
                self._listen(True)
                if self.config.speaker_filter:
                    self._spotter.start_learning()
                self._draw()
            else:
                self._start_session()
        elif keysym.lower() in ("s", "space") and self.state == "calibration":
            self._end_calibration(skipped=True)
        elif keysym == "space" and self.state == "rest":
            self._end_rest(skipped=True)
        elif self.state == "stimulus" and keysym in "0123456789" and len(keysym) == 1:
            # Voice mode always retains the keyboard fallback.
            self._respond(int(keysym), time.monotonic(), "keyboard")
        elif (keysym == "space" and self.state in ("stimulus", "feedback", "isi")):
            self._end_block(time.monotonic(), skipped=True)
        elif keysym == "space" and self.state == "summary":
            self._player.stop()
            self.events = []
            self._reset_markers()
            self._reset()
            self._started_monotonic = time.monotonic()
            self._started_iso = time.strftime("%Y-%m-%d %H:%M:%S")
            self._begin()

    def _tick(self):
        if self.result is not None or self.state == "summary":
            return
        now = time.monotonic()
        if self._spotter and self._spotter.error != self._voice_error:
            self._voice_error = self._spotter.error
            if self._voice_error:
                self.mark("voice unavailable", error=self._voice_error, fallback="keyboard")
        if self.state == "calibration":
            self._poll_calibration(now)
        elif self.state == "rest":
            if now >= self._phase_end:
                self._end_rest()
        elif self.state in ("stimulus", "feedback", "isi"):
            # Drain captured answers before testing deadlines; use source time.
            if self.state == "stimulus":
                self._poll_voice(now)
            if self.state == "stimulus":
                deadline = self._trial["deadline"]
                grace = (self.config.voice_grace if self._spotter and self._spotter.ready else 0)
                if deadline is not None and deadline <= self._phase_end and now >= deadline + grace:
                    self._respond(None, deadline, "timeout")
            if now >= self._phase_end:
                self._end_block(self._phase_end)
            elif self.state == "feedback" and now >= self._transition_at:
                self.mark("feedback end", trial=self._trial_number, condition=self._condition)
                self.state = "isi"
                self._transition_at = now + self.config.isi
                self._draw()
            elif self.state == "isi" and now >= self._transition_at:
                # Never let the previous buzzer become the next spoken answer.
                if self._feedback_process is None or self._feedback_process.poll() is not None:
                    self._next_trial()
        if self.state != "summary":
            self._update_clock(time.monotonic())
            self.after(20, self._tick)

    def _poll_calibration(self, now):
        if not self._spotter.ready:
            self._draw()  # Display model-loading / microphone error status.
            return
        if self._cal_start is None:
            self._cal_start = now
            self._cal_end = now + self.config.calibration_time
            self.mark("calibration stimulus", digit=self._cal_index)
        hits = self._spotter.drain_words()
        matched = any(word == DIGITS[self._cal_index] and self._cal_start <= stamp <= self._cal_end
                      for word, stamp in hits)
        if matched or now >= self._cal_end:
            self._cal_results[str(self._cal_index)] = "ok" if matched else "missed"
            self.mark("calibration result", digit=self._cal_index, recognized=matched)
            self._cal_index += 1
            self._cal_start = None
            self._listen(True)
            if self._cal_index == len(DIGITS):
                self._end_calibration()
                return
        self._draw()

    def _end_calibration(self, skipped=False):
        enrolled = False
        if self.config.speaker_filter and self._spotter is not None:
            enrolled = self._spotter.finish_learning()
        self.mark("calibration end", color="yellow", skipped=skipped, results=dict(self._cal_results),
                  voice_enrolled=enrolled)
        self._listen(False)
        self._start_session()

    def _start_session(self):
        self._test_started = time.monotonic()
        self.mark(f"{TITLE} start", color="yellow", at=self._test_started, config=self.config.to_dict())
        if self.config.practice_time > 0:
            self._start_block("practice", self.config.practice_time)
        else:
            self._advance_block()

    def _advance_block(self):
        self._block_index += 1
        if self._block_index >= len(self._blocks):
            self._show_summary()
        elif self.config.rest_time > 0:
            self.state = "rest"
            self._phase_end = time.monotonic() + self.config.rest_time
            self.mark("rest start", color="yellow", block=self._block_index + 1,
                      next_condition=self._blocks[self._block_index])
            self._draw()
        else:
            self._start_block(self._blocks[self._block_index], self.config.test_time)

    def _end_rest(self, skipped=False):
        self.mark("rest end", color="yellow", block=self._block_index + 1, skipped=skipped)
        self._start_block(self._blocks[self._block_index], self.config.test_time)

    def _start_block(self, condition, duration):
        self._condition = condition
        self._correct_rts = []
        self._failures = 0
        if condition == "stress" and self.config.tick and self.config.sounds:
            self._player.play_tick_track(duration)
        now = time.monotonic()
        self._phase_end = now + duration
        self.mark(f"{condition} start", color="yellow", at=now, block=self._block_index + 1,
                  duration_s=duration, response_limit_s=self._limit if condition == "stress" else None,
                  benchmark=self.config.benchmark if condition == "stress" else None)
        self._next_trial()

    def _next_trial(self):
        problem = generate_problem(self._rng, self.config.difficulty)
        self._trial_number += 1
        self.state = "stimulus"
        self._trial = {"trial": self._trial_number, "block": self._block_index + 1,
                       "condition": self._condition, "expression": problem.expression,
                       "answer": problem.answer, "difficulty": problem.difficulty,
                       "response_limit_s": self._limit if self._condition == "stress" else None}
        # Draw before taking the software onset timestamp. The renderer does
        # not pump Tk events, so keys cannot race the creation of the deadline.
        self._draw()
        onset = time.monotonic()
        self._trial["onset"] = onset
        self._trial["deadline"] = onset + self._limit if self._condition == "stress" else None
        self.mark("stimulus", at=onset, **self._trial)
        self._listen(True)

    def _poll_voice(self, now):
        if not self._spotter or not self._spotter.ready:
            return
        for word, stamp in self._spotter.drain_words():
            deadline = self._trial["deadline"]
            if deadline is not None and now > deadline + self.config.voice_grace:
                continue
            if (word in DIGITS and self.state == "stimulus"
                    and self._trial["onset"] + self.config.min_voice_lag <= stamp <= now):
                self._respond(DIGITS.index(word), stamp, "voice")

    def _respond(self, answer, stamp, source):
        if self.state != "stimulus":
            return
        trial = self._trial
        deadline = trial["deadline"]
        if answer is not None and (stamp < trial["onset"] or stamp >= self._phase_end
                                   or (deadline is not None and stamp > deadline)):
            return
        self.state = "feedback"  # Close the trial before publishing anything.
        self._listen(False)
        rt = stamp - trial["onset"] if answer is not None else None
        outcome = "timeout" if answer is None else "correct" if answer == trial["answer"] else "wrong"
        self._outcome = outcome
        self._adapt(outcome, rt)
        details = {**trial, "response": answer, "source": source, "rt": rt,
                   "outcome": outcome, "correct": outcome == "correct", "timed_out": answer is None,
                   "response_monotonic_s": stamp, "next_response_limit_s": self._limit}
        self.record(kind="trial", **details)
        self.mark(outcome, at=stamp, color=GREEN if outcome == "correct" else RED, **details)
        self._draw()
        self.mark("feedback", trial=self._trial_number, condition=self._condition, outcome=outcome)
        self._feedback_process = None
        if self._condition == "stress" and self.config.sounds:
            self._feedback_process = self._player.play("cling" if outcome == "correct" else "buzz")
        self._transition_at = time.monotonic() + self.config.feedback_time

    def _adapt(self, outcome, rt):
        if self._condition != "stress" or not self.config.adaptive_timing:
            return
        old = self._limit
        if outcome == "correct":
            self._failures = 0
            self._correct_rts.append(rt)
            if len(self._correct_rts) >= self.config.adaptation_streak:
                self._limit = self._clamp(min(old, statistics.mean(self._correct_rts) * self.config.time_factor))
                self._correct_rts.clear()
        else:
            self._correct_rts.clear()
            self._failures += 1
            if self._failures >= self.config.adaptation_streak:
                self._limit = self._clamp(old * self.config.recovery_factor)
                self._failures = 0
        if old != self._limit:
            self.mark("difficulty change", trial=self._trial_number,
                      previous_limit_s=old, next_limit_s=self._limit, reason=outcome)

    def _clamp(self, value):
        return min(self.config.response_max, max(self.config.response_min, value))

    def _interrupt_trial(self, stamp, reason):
        if self.state == "stimulus":
            self.record(kind="interrupted", **self._trial, reason=reason)
            self.mark("trial interrupted", at=stamp, **self._trial, reason=reason)
            self.state = "isi"

    def _end_block(self, stamp, aborted=False, skipped=False):
        self._interrupt_trial(stamp, "abort" if aborted else "block end")
        if self.state == "feedback":
            self.mark("feedback end", at=stamp, trial=self._trial_number, condition=self._condition)
        self._listen(False)
        self._player.stop()
        self.mark(f"{self._condition} end", color="yellow", at=stamp, block=self._block_index + 1,
                  aborted=aborted, skipped=skipped)
        if self._condition == "practice" and not aborted:
            rts = [e["rt"] for e in self.events
                   if e.get("kind") == "trial" and e["condition"] == "practice" and e["correct"]]
            self._limit = self._clamp(statistics.mean(rts) * self.config.time_factor) if rts else self.config.response_time
            self.mark("pace calibrated", samples=len(rts), response_limit_s=self._limit,
                      fallback=not rts)
        if not aborted:
            self._advance_block()

    def _show_summary(self):
        self._test_ended = time.monotonic()
        self.state = "summary"
        self._listen(False)
        self._player.stop()
        self.mark(f"{TITLE} end", color="yellow", summary=self._summary())
        self._draw()

    def _summary(self):
        groups = {}
        for condition in ("practice", "control", "stress"):
            trials = [e for e in self.events if e.get("kind") == "trial" and e["condition"] == condition]
            rts = [e["rt"] for e in trials if e["rt"] is not None]
            groups[condition] = {
                "n_trials": len(trials), "n_correct": sum(e["correct"] for e in trials),
                "n_wrong": sum(e["outcome"] == "wrong" for e in trials),
                "n_timeout": sum(e["timed_out"] for e in trials),
                "accuracy": sum(e["correct"] for e in trials) / len(trials) if trials else None,
                "mean_rt_s": statistics.mean(rts) if rts else None}
        return {"conditions": groups, "final_response_limit_s": self._limit,
                "voice_calibration": dict(self._cal_results),
                "n_interrupted": sum(e.get("kind") == "interrupted" for e in self.events),
                "task_duration_s": ((self._test_ended or time.monotonic()) - self._test_started
                                    if self._test_started is not None else 0.0)}

    def _finish(self, aborted=False):
        if self.result is not None:
            return
        if self.state != "summary":
            now = time.monotonic()
            if self.state in ("stimulus", "feedback", "isi"):
                self._end_block(now, aborted=True)
            elif self.state in ("rest", "calibration"):
                self.mark(f"{self.state} end", color="yellow", at=now, aborted=True)
            self._test_ended = now
            self.mark(f"{TITLE} aborted" if aborted else f"{TITLE} end",
                      color="yellow", at=now, phase=self.state)
        self._player.stop()
        if self._spotter:
            self._spotter.stop()
        super()._finish(aborted)

    # -------------------------------------------------------------- drawing

    def _redraw_static(self):
        self._draw()

    def _text(self, x, y, text, size=0, color=None, **kwargs):
        return self._canvas.create_text(x, y, text=text, font=self.font(size),
                                        fill=color or self.config.foreground, **kwargs)

    def _draw(self):
        if self._canvas is None:
            return
        c = self._canvas
        c.delete("all")
        w, h = max(640, c.winfo_width()), max(480, c.winfo_height())
        self._text(w / 2, 30, "MIST · MENTAL ARITHMETIC", 22, "#8ab4c8")
        if self.state == "instructions":
            draw_instructions_panel(c, w, h, self.config.background)
            lines = ["MONTREAL IMAGING STRESS TASK", "",
                     "Solve the expression. Every answer is a digit from 0 to 9.",
                     "Say the answer or press its digit." if self._spotter else "Press the answer's digit (0–9). No Enter needed.",
                     "Multiplication and division come before addition and subtraction.",
                     "Practice calibrates the pace; control has no answer deadline.",
                     "Stress blocks add time pressure and a performance target.",
                     "Voice calibration runs first." if self._spotter and self.config.voice_calibration else "",
                     "SPACE to begin  ·  ESC to quit"]
            self.show_text(lines, y=0.48, size=max(14, min(24, w // 48)))
        elif self.state == "calibration":
            self._text(w / 2, h * 0.25, "MICROPHONE CHECK", 28)
            self._text(w / 2, h * 0.44, f'Say “{DIGITS[self._cal_index]}”', 48)
            self._text(w / 2, h * 0.62, f"{self._cal_index + 1} / 10", 22)
        elif self.state == "rest":
            self._text(w / 2, h * 0.34, "REST · Keep your eyes on the cross", 26)
            self._text(w / 2, h * 0.5, "+", 64)
            self._text(w / 2, h * 0.72, f"Next: {self._blocks[self._block_index].upper()}", 24)
        elif self.state == "summary":
            lines = ["RESULTS", ""]
            for name, s in self._summary()["conditions"].items():
                if s["n_trials"]:
                    lines.append(f"{name.capitalize()}: {s['n_correct']} / {s['n_trials']} correct"
                                 f" · {s['accuracy']:.0%} · {s['n_timeout']} timeouts")
            lines += ["", "SPACE to run again  ·  ESC to close"]
            self.show_text(lines, y=0.48, size=24)
        else:
            self._text(26, 72, self._condition.upper(), 22, "#8ab4c8", anchor="w")
            if self._condition == "stress" and self.config.show_comparison:
                trials = [e for e in self.events if e.get("kind") == "trial"
                          and e["block"] == self._block_index + 1 and e["condition"] == "stress"]
                accuracy = sum(e["correct"] for e in trials) / len(trials) if trials else 0
                color = GREEN if accuracy >= self.config.benchmark else RED
                self._text(w / 2, h * 0.2, f"Your accuracy: {accuracy:.0%}   |   Expected: {self.config.benchmark:.0%}", 22, color)
                x0, x1, y = w * 0.15, w * 0.85, h * 0.25
                c.create_rectangle(x0, y, x1, y + 16, fill="#292932", outline="")
                c.create_rectangle(x0, y, x0 + (x1 - x0) * accuracy, y + 16, fill=color, outline="")
                target = x0 + (x1 - x0) * self.config.benchmark
                c.create_line(target, y - 5, target, y + 21, fill=self.config.foreground, width=3)
            if self.state == "stimulus":
                self._text(w / 2, h * 0.44, self._trial["expression"] + " = ?",
                           max(24, min(58, w // 20)))
            elif self.state == "feedback":
                if self._condition == "stress":
                    draw_verdict(c, w / 2, h * 0.42, min(w, h) * 0.065, self._outcome == "correct")
                label = {"correct": "CORRECT", "wrong": "INCORRECT", "timeout": "HURRY UP! · TIMEOUT"}[self._outcome]
                self._text(w / 2, h * 0.7, label, 28, GREEN if self._outcome == "correct" else RED)
                if self._condition != "stress":
                    # Same rendering as the stimulus, with the answer in place
                    # of the interrogation mark.
                    self._text(w / 2, h * 0.44,
                               f"{self._trial['expression']} = {self._trial['answer']}",
                               max(24, min(58, w // 20)))
            else:
                self._text(w / 2, h * 0.45, "+", 60)
            self._text(w / 2, h - 65, "0   1   2   3   4   5   6   7   8   9", 28, "#8ab4c8")
        self._update_clock(time.monotonic())

    def _update_clock(self, now):
        if self._canvas is None:
            return
        c = self._canvas
        c.delete("clock")
        w, h = max(640, c.winfo_width()), max(480, c.winfo_height())
        if self.state == "rest" or (self._condition == "stress" and self.state in ("stimulus", "feedback", "isi")):
            self._text(w - 26, 72, f"{max(0, self._phase_end - now):.1f} s", 26, anchor="e", tags="clock")
        if self.state == "stimulus" and self._trial.get("deadline") is not None:
            remaining = max(0, self._trial["deadline"] - now)
            fraction = min(1, remaining / self._trial["response_limit_s"])
            x0, x1, y = w * 0.15, w * 0.85, h * 0.57
            c.create_rectangle(x0, y, x1, y + 18, fill="#292932", outline="", tags="clock")
            c.create_rectangle(x0, y, x0 + (x1 - x0) * fraction, y + 18,
                               fill=RED if fraction < 0.3 else GREEN, outline="", tags="clock")
            self._text(w / 2, y + 40, f"{remaining:.1f} s", 20, tags="clock")
        if self._spotter:
            status = (f"Voice: {self._spotter.error or self._spotter.status} · keyboard available"
                      if not self._spotter.ready else "Voice ready · say a digit or use the keyboard")
            self._text(w / 2, h - 25, status, 14, "#8a8a92", width=w - 40, tags="clock")
