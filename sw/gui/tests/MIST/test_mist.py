"""MIST arithmetic, timing, event and GUI integration regressions."""

import dataclasses
import json
from pathlib import Path
import random
import re
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from gui.tests import available_tests, load_test
from gui.tests.common import settings
from gui.tests.MIST.arithmetic import generate_problem
from gui.tests.MIST.spec import Config, FORM
from gui.tests.MIST.test import DIGITS, Test
from gui.tests.run_test import build_parser


class ContractTests(unittest.TestCase):
    def test_discovery_config_form_and_cli_agree(self):
        entry = available_tests()["mist"]
        self.assertTrue(entry["implemented"])
        self.assertIs(load_test("mist"), Test)
        data = settings.config_kwargs("mist", settings.load_settings(
            entry["folder"] / "configs" / "defaults.hjson"), Config)
        keys = {f.name for f in dataclasses.fields(Config)}
        self.assertTrue(set(data) <= keys)  # No unknown keys in the file.
        Config(**data)  # The file's values validate.
        self.assertEqual(keys, {f["key"] for f in FORM if "key" in f})
        args = build_parser().parse_args(["mist", "--response-mode", "voice", "--difficulty", "5"])
        self.assertEqual(args.response_mode, "voice")
        self.assertEqual(args.difficulty, 5)

    def test_invalid_configuration_fails_early(self):
        for values in ({"difficulty": 0}, {"difficulty": 6}, {"difficulty": 2.5},
                       {"practice_time": -1}, {"test_time": 0}, {"isi": float("nan")},
                       {"response_time": 99}, {"block_order": ""}, {"block_order": "bogus"},
                       {"response_mode": "mouse"}, {"benchmark": 1.5}, {"sounds": "false"},
                       {"time_factor": 1}, {"recovery_factor": 1}, {"adaptation_streak": 0},
                       {"repetitions": 0}, {"response_mode": "voice", "min_voice_lag": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config(**values)

    def test_arithmetic_has_exact_digit_answers_and_is_reproducible(self):
        for difficulty in range(1, 6):
            rng = random.Random(14)
            problems = [generate_problem(rng, difficulty) for _ in range(1000)]
            again = random.Random(14)
            self.assertEqual(problems, [generate_problem(again, difficulty) for _ in problems])
            self.assertEqual({p.answer for p in problems}, set(range(10)))
            for problem in problems:
                # Independently evaluate only our generated numeric expressions.
                expression = problem.expression.translate(str.maketrans("×÷−", "*/-"))
                self.assertEqual(eval(expression, {"__builtins__": {}}), problem.answer)
                self.assertTrue(all(0 <= int(n) <= 99 for n in re.findall(r"\d+", expression)))


class TimingTests(unittest.TestCase):
    def setUp(self):
        timer = patch("gui.tests.MIST.test.time.monotonic", return_value=100.0)
        self.clock = timer.start()
        self.addCleanup(timer.stop)
        self.task = Test(practice_time=0, rest_time=0, test_time=60, seed=7,
                         block_order="stress", response_time=4)
        self.task.after = Mock()
        self.task._player = Mock()
        self.task._player.play.return_value = None
        self.task._start_session()

    def answer(self, correct=True, rt=1):
        task = self.task
        self.clock.return_value = task._trial["onset"] + rt
        answer = task._trial["answer"] if correct else (task._trial["answer"] + 1) % 10
        task._handle_key(str(answer))

    def test_keyboard_one_verdict_per_trial_and_source_timestamps(self):
        self.answer()
        self.task._handle_key("1")
        self.clock.return_value = 105
        self.task._tick()
        self.assertEqual(len(self.task.events), 1)
        event = self.task.events[0]
        self.assertEqual(event["rt"], 1)
        self.assertEqual(event["source"], "keyboard")
        verdict = next(e for e in self.task.timeline if e["label"] == "correct")
        self.assertEqual(verdict["monotonic_s"], 101)
        self.assertEqual(verdict["color"], "#40c060")
        self.assertEqual(verdict["test"], "mist")
        self.task._player.play.assert_called_once_with("cling")

    def test_wrong_and_timeout_have_distinct_red_events_and_buzzer(self):
        self.answer(correct=False)
        self.task._player.play.assert_called_once_with("buzz")
        self.assertEqual(self.task.events[0]["outcome"], "wrong")
        self.task._next_trial()
        deadline = self.task._trial["deadline"]
        self.clock.return_value = deadline + 0.1
        self.task._tick()
        self.task._tick()
        self.assertEqual(len(self.task.events), 2)
        self.assertEqual(self.task.events[1]["outcome"], "timeout")
        self.assertIsNone(self.task.events[1]["rt"])
        verdict = next(e for e in self.task.timeline if e["label"] == "timeout")
        self.assertEqual(verdict["monotonic_s"], deadline)
        self.assertEqual(verdict["color"], "#e04040")

    def test_deadline_rejects_late_keyboard_answer(self):
        self.clock.return_value = 104.1
        self.task._handle_key(str(self.task._trial["answer"]))
        self.assertFalse(self.task.events)
        self.task._tick()
        self.assertEqual(self.task.events[0]["outcome"], "timeout")

    def attach_voice(self):
        voice = Mock(ready=True, error="", paused=False)
        voice.drain_words.return_value = []
        self.task._spotter = voice
        return voice

    def test_voice_delivery_grace_uses_capture_time_and_rejects_stale_hits(self):
        voice = self.attach_voice()
        answer = DIGITS[self.task._trial["answer"]]
        self.clock.return_value = 104.1
        voice.drain_words.return_value = [(answer, 99), (answer, 100.01), (answer, 103.9)]
        self.task._tick()
        self.assertEqual(len(self.task.events), 1)
        self.assertEqual(self.task.events[0]["source"], "voice")
        self.assertAlmostEqual(self.task.events[0]["rt"], 3.9)
        self.assertTrue(voice.paused)
        self.task._tick()
        self.assertEqual(len(self.task.events), 1)

    def test_voice_grace_does_not_extend_capture_deadline(self):
        voice = self.attach_voice()
        voice.drain_words.return_value = [(DIGITS[self.task._trial["answer"]], 104.1)]
        self.clock.return_value = 104.1
        self.task._tick()
        self.assertEqual(self.task.events, [])
        self.clock.return_value = 104.31
        self.task._tick()
        self.assertEqual(self.task.events[0]["outcome"], "timeout")

    def test_voice_result_delivered_beyond_grace_cannot_rescue_timeout(self):
        voice = self.attach_voice()
        voice.drain_words.return_value = [(DIGITS[self.task._trial["answer"]], 103.9)]
        self.clock.return_value = 104.5
        self.task._tick()
        self.assertEqual(self.task.events[0]["outcome"], "timeout")

    def test_adaptation_is_bounded_and_only_changes_future_trials(self):
        for _ in range(3):
            previous_deadline = self.task._trial["deadline"]
            self.answer(rt=1)
            self.assertEqual(self.task._trial["deadline"], previous_deadline)
            self.task._next_trial()
        self.assertAlmostEqual(self.task._limit, 0.9)
        for _ in range(3):
            self.answer(correct=False, rt=0.4)
            self.task._next_trial()
        self.assertAlmostEqual(self.task._limit, 0.99)
        self.task._limit = self.task.config.response_min
        for _ in range(3):
            self.task._adapt("correct", 0.01)
        self.assertEqual(self.task._limit, self.task.config.response_min)
        self.task._limit = self.task.config.response_max
        for _ in range(3):
            self.task._adapt("timeout", None)
        self.assertEqual(self.task._limit, self.task.config.response_max)

    def test_control_has_no_per_trial_deadline_or_pressure_audio(self):
        self.task._player.reset_mock()
        self.task._start_block("control", 60)
        self.assertIsNone(self.task._trial["deadline"])
        self.answer(rt=30)
        self.assertEqual(self.task.events[0]["outcome"], "correct")
        self.assertEqual(self.task._limit, 4)
        self.task._player.play.assert_not_called()
        self.task._player.play_tick_track.assert_not_called()

    def test_practice_calibrates_from_correct_answers_only(self):
        self.task._start_block("practice", 60)
        self.answer(rt=2)
        self.task._next_trial()
        self.answer(correct=False, rt=10)
        self.task._next_trial()
        self.answer(rt=4)
        self.task._end_block(self.clock.return_value)
        self.assertAlmostEqual(self.task._limit, 2.7)
        calibrated = next(e for e in self.task.timeline if e["label"] == "pace calibrated")
        self.assertEqual(calibrated["details"]["samples"], 2)

    def test_space_skips_practice_early_and_calibrates(self):
        self.task._reset()
        self.task._start_block("practice", 600)
        self.answer(rt=2)
        self.task._next_trial()
        self.answer(rt=4)
        self.task._next_trial()
        self.task._handle_key("space")  # Skip in the middle of problem 3.
        # block_order="stress" with no rest: skipping goes straight to stress.
        self.assertEqual(self.task.state, "stimulus")
        self.assertEqual(self.task._condition, "stress")
        self.assertAlmostEqual(self.task._limit, 2.7)  # Correct answers only.
        self.assertTrue(any(e["label"] == "trial interrupted"
                            for e in self.task.timeline))

    def test_block_boundary_interrupts_unfinished_trial_without_wrong_verdict(self):
        self.task._phase_end = 101
        self.clock.return_value = 101.1
        self.task._tick()
        self.assertEqual(self.task.state, "summary")
        self.assertEqual(self.task.events[0]["kind"], "interrupted")
        self.assertEqual(self.task._summary()["conditions"]["stress"]["n_trials"], 0)
        self.task._player.stop.assert_called()

    def test_overdue_deadline_is_not_disguised_as_block_interruption(self):
        self.task._phase_end = 105
        self.clock.return_value = 106
        self.task._tick()
        self.assertEqual(self.task.state, "summary")
        self.assertEqual(self.task.events[0]["outcome"], "timeout")

    def test_abort_preserves_trials_and_stops_mic_and_audio(self):
        voice = self.attach_voice()
        self.answer()
        self.task._next_trial()
        self.task._finish(aborted=True)
        self.assertTrue(self.task.result.aborted)
        self.assertEqual(len(self.task.result.events), 2)
        self.assertEqual(self.task.result.timeline[-1]["label"], "MIST aborted")
        self.task._player.stop.assert_called()
        voice.stop.assert_called_once()

    def test_voice_calibration_waits_for_readiness_and_can_be_skipped(self):
        voice = self.attach_voice()
        voice.ready = False
        self.task._reset()
        self.task._handle_key("space")
        self.assertEqual(self.task.state, "calibration")
        self.clock.return_value = 150
        self.task._tick()
        self.assertIsNone(self.task._cal_start)
        voice.ready = True
        self.task._tick()
        voice.drain_words.return_value = [("zero", 150.3)]
        self.clock.return_value = 150.3
        self.task._tick()
        self.assertEqual(self.task._cal_results, {"0": "ok"})
        self.task._handle_key("space")  # SPACE skips the voice check too.
        self.assertEqual(self.task.state, "stimulus")
        self.assertTrue(any(e["label"] == "calibration end" for e in self.task.timeline))

    def test_space_skips_rest_and_any_block(self):
        task = Test(practice_time=0, rest_time=5, test_time=60, seed=3,
                    block_order="control stress", response_time=4)
        task.after = Mock()
        task._player = Mock()
        task._start_session()
        self.assertEqual(task.state, "rest")  # Rest precedes the first block.
        task._handle_key("space")             # Skip the rest.
        self.assertEqual(task.state, "stimulus")
        self.assertEqual(task._condition, "control")
        self.clock.return_value = task._trial["onset"] + 0.5
        task._handle_key(str(task._trial["answer"]))
        task._handle_key("space")             # Skip the control block.
        self.assertEqual(task.state, "rest")
        skipped = [e for e in task.timeline if e["label"] == "control end"]
        self.assertTrue(skipped and skipped[-1]["details"]["skipped"])
        task._handle_key("space")             # Skip the second rest.
        self.assertEqual(task._condition, "stress")
        task._handle_key("space")             # Skip the stress block: done.
        self.assertEqual(task.state, "summary")
        self.assertTrue(any(e["label"] == "trial interrupted"
                            for e in task.timeline))

    def test_voice_calibration_completes_all_digits_then_enters_task(self):
        voice = self.attach_voice()
        self.task._reset()
        self.task._handle_key("space")
        for digit in DIGITS:
            voice.drain_words.return_value = []
            self.task._tick()  # Establish this digit's capture interval.
            self.clock.return_value += 0.3
            voice.drain_words.return_value = [(digit, self.clock.return_value)]
            self.task._tick()
        self.assertEqual(self.task.state, "stimulus")
        self.assertEqual(list(self.task._cal_results.values()), ["ok"] * 10)

    def test_speaker_filter_learns_voice_at_calibration_and_gates_noise(self):
        from gui.tests.common.voice import VoiceSpotter
        spotter = VoiceSpotter(DIGITS, speaker_filter=True, speaker_threshold=0.5)
        spotter.ready = True
        spotter.error = ""
        spotter.paused = False
        self.task._spotter = spotter
        self.task._reset()
        self.task._handle_key("space")   # Calibration starts voice learning.
        self.assertTrue(spotter._speaker_learning)
        self.clock.return_value = 120
        self.task._tick()                # Opens the capture window for "zero".
        voice_vector = [1.0] + [0.0] * 15
        spotter._emit("zero", 120.2, voice_vector)
        self.clock.return_value = 120.3
        self.task._tick()
        self.assertEqual(self.task._cal_results, {"0": "ok"})
        self.task._handle_key("space")   # Finish calibration: enroll profile.
        self.assertTrue(spotter.speaker_enrolled)
        self.assertEqual(self.task.state, "stimulus")
        enrolled = next(e for e in self.task.timeline if e["label"] == "calibration end")
        self.assertTrue(enrolled["details"]["voice_enrolled"])
        answer = DIGITS[self.task._trial["answer"]]
        spotter._spoken.clear()
        spotter._emit(answer, 121.0, [-1.0] + [0.0] * 15)  # Someone else.
        self.task._poll_voice(121.1)
        self.assertEqual(self.task.events, [])
        spotter._spoken.clear()
        spotter._emit(answer, 121.3, voice_vector)         # The participant.
        self.task._poll_voice(121.4)
        self.assertEqual(len(self.task.events), 1)
        self.assertEqual(self.task.events[0]["source"], "voice")

    def test_restart_resets_run_id_trials_and_calibration(self):
        self.answer()
        self.task._show_summary()
        old_run = self.task._run_id
        self.task._cal_results = {"0": "ok"}
        self.task._handle_key("space")
        self.assertEqual(self.task.state, "instructions")
        self.assertEqual(self.task.events, [])
        self.assertEqual(self.task._cal_results, {})
        self.assertNotEqual(old_run, self.task._run_id)
        self.assertEqual(self.task.timeline[0]["sequence"], 0)


class GUIIntegrationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_configuration_file_workspace_snapshot_and_launch(self):
        from gui.sdk.tests.panel import TestsPanel
        panel = TestsPanel(self.root, host=tk.Frame(self.root), reports=tk.Frame(self.root))
        self.addCleanup(panel.close)
        panel.test_selection.set("MIST")
        panel._build_form()
        mode = "keys" if panel.form_base["response_mode"] == "voice" else "voice"
        path = Path(self.directory.name) / "mist voice.hjson"
        path.write_text(f'{{mist: {{response_mode: "{mode}", difficulty: 5, benchmark: 0.9}}}}')
        with patch("gui.sdk.tests.panel.filedialog.askopenfilename", return_value=str(path)):
            panel.load_config_file()
        self.assertEqual(panel.form_values["difficulty"].get(), "5")
        voice_on = panel.form_values["response_mode"].get() == "voice"
        for _rule, ws in panel._section_rules:  # Voice options follow the mode.
            self.assertEqual(any(w.instate(["disabled"]) for w in ws), not voice_on)
        snapshot = panel.configuration_snapshot()
        panel.test_selection.set("Stroop")
        panel._build_form()
        panel.apply_configuration(json.loads(json.dumps(snapshot)))
        self.assertEqual(panel.configuration_snapshot(), snapshot)
        saved = Path(self.directory.name) / "saved.hjson"
        with patch("gui.sdk.tests.panel.filedialog.asksaveasfilename", return_value=str(saved)):
            panel.save_config_file()
        config = Config(**settings.config_kwargs("mist", settings.load_settings(saved), Config))
        self.assertEqual(config.difficulty, 5)
        self.assertEqual(config.response_mode, mode)
        with patch("gui.sdk.tests.panel.subprocess.Popen") as popen:
            panel.launch()
        args = popen.call_args.args[0]
        self.assertIn("mist", args)
        self.assertEqual(args[args.index("--difficulty") + 1], "5")
        self.assertEqual(args[args.index("--response-mode") + 1], mode)
        panel.close_task()

    def test_real_window_runs_all_phases_and_publishes_events(self):
        path = Path(self.directory.name) / "events.jsonl"
        # Blocks long enough that a slow interpreter (e.g. Python 3.8) still
        # gets a stimulus inside each block and can answer it.
        task = Test(practice_time=0.36, rest_time=0.12, test_time=0.48,
                    feedback_time=0.05, isi=0, sounds=False)
        task.run(parent=self.root, event_path=path)
        self.addCleanup(lambda: task._finish(task.state != "summary"))
        task._handle_key("space")
        import time
        until = time.monotonic() + 10
        while task.state != "summary" and time.monotonic() < until:
            self.root.update()
            if task.state == "stimulus":
                task._handle_key(str(task._trial["answer"]))
            time.sleep(0.01)
        self.assertEqual(task.state, "summary")
        task._finish()
        messages = [json.loads(line) for line in path.read_text().splitlines()]
        labels = [m["label"] for m in messages]
        for phase in ("instructions", "MIST start", "practice start", "pace calibrated", "rest start",
                      "control start", "control end", "stress start", "stress end", "MIST end"):
            self.assertIn(phase, labels)
        self.assertEqual([m["sequence"] for m in messages], list(range(len(messages))))
        self.assertTrue(all(m["test"] == "mist" and m["schema_version"] == 2 for m in messages))
        self.assertFalse(task.result.aborted)
        self.assertTrue(task.result.summary["conditions"]["stress"]["n_correct"])


if __name__ == "__main__":
    unittest.main()
