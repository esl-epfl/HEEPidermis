"""Deterministic checks for adaptive stress timing, scoring and rendering."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import unittest
import random
from unittest.mock import Mock, patch

from gui.tests.Stroop.spec import StroopStressConfig
from gui.tests.Stroop.stress import StroopStressTest
from gui.tests.common.settings import config_kwargs, load_settings


class AdaptiveStressTests(unittest.TestCase):
    def make_test(self, stimulus_time=2.0, **overrides):
        test = StroopStressTest(voice_debug=False, stimulus_time=stimulus_time, **overrides)
        test.after = Mock()
        test._draw_stimulus = Mock()
        test._draw_fixation = Mock()
        test._draw_feedback = Mock()
        test._update_chrome = Mock()
        test._player = Mock()
        test._player.play.return_value = None
        return test

    def test_difficulty_ramps_gradually_and_recovers(self):
        test = self.make_test()
        initial = test._stimulus_time
        for _ in range(100):
            test._score = int(test.config.score_target * 0.9)
            before = (test._stimulus_time, test._feedback_time, test._isi)
            test._adapt_timing(0)
            self.assertLessEqual(test._stimulus_time, before[0])
            self.assertGreaterEqual(test._stimulus_time, before[0] * 0.97 - 1e-9)
            self.assertLessEqual(abs(test._feedback_time - before[1]), 0.01 + 1e-9)
            self.assertLessEqual(test._isi, before[2])
        self.assertLess(test._stimulus_time, initial)
        self.assertEqual(test._stimulus_time, test.config.stimulus_min)
        self.assertEqual(test._feedback_time, test.config.feedback_min)

        for _ in range(200):
            test._score = 0
            before = (test._stimulus_time, test._feedback_time)
            test._adapt_timing(0)
            self.assertLessEqual(test._stimulus_time, before[0] * 1.03 + 1e-9)
            self.assertLessEqual(abs(test._feedback_time - before[1]), 0.01 + 1e-9)
        self.assertEqual(test._stimulus_time, test.config.stimulus_max)
        self.assertEqual(test._feedback_time, test.config.feedback_max)
        self.assertLessEqual(test._isi, test.config.isi_start)
        self.assertGreaterEqual(test._isi, test.config.isi_min)

    def test_pressure_is_mild_in_band_and_stronger_near_target(self):
        pressures = []
        for fraction in (0.5, 0.6, 0.7, 0.8, 0.9):
            test = self.make_test(adaptation_smoothing=1.0)
            test._score = fraction * test.config.score_target
            test._adapt_timing(0)
            pressures.append(test._pressure)
        self.assertEqual(pressures[0], 0)
        self.assertEqual(pressures, sorted(pressures))
        self.assertLess(pressures[2], pressures[4] / 2)
        self.assertAlmostEqual(pressures[4], 1.0)

    def test_trial_timings_are_frozen_and_score_is_not_clamped(self):
        with patch('gui.tests.Stroop.stroop.time.monotonic') as clock:
            clock.return_value = 100.0
            test = self.make_test(score_start=49000, adaptation_smoothing=1.0)
            test._next_stimulus()
            clock.return_value = 101.0
            test._respond(test._trial['ink'])
            self.assertEqual(test._score, 52000)  # Genuine points can exceed target.
            event = test.events[0]
            self.assertEqual(event['stimulus_time_s'], 2.0)
            self.assertEqual(event['feedback_time_s'], 0.5)
            self.assertLess(event['next_stimulus_time_s'], 2.0)
            self.assertLess(event['next_feedback_time_s'], 0.5)
            self.assertIsNone(test._deadline)
            test.after.assert_called_once_with(500, test._hide_feedback)
            test._respond(None)
            self.assertEqual(len(test.events), 1)
            test._player.play.assert_called_once_with('cling')
            clock.return_value = 103.0
            test._next_stimulus()
            self.assertAlmostEqual(test._deadline - 103.0, test._stimulus_time)
            self.assertEqual(test._trial_feedback_time, test._feedback_time)

    def test_errors_keep_their_real_penalties(self):
        test = self.make_test(score_start=0)
        test._next_stimulus()
        test._respond(None, rt=2.0)
        self.assertEqual(test._score, -4000)
        test._next_stimulus()
        wrong = next(c for c in test.keys.values() if c != test._trial['ink'])
        test._respond(wrong, rt=1.25)
        self.assertEqual(test._score, -5250)
        self.assertGreater(test._stimulus_time, 2.0)

    def test_feedback_sound_finishes_before_next_word(self):
        test = self.make_test()
        test._next_stimulus()
        process = Mock()
        process.poll.return_value = None
        test._player.play.return_value = process
        test._respond(test._trial['ink'], rt=1.0)
        test._hide_feedback()
        test._draw_stimulus.reset_mock()
        test._next_stimulus()
        self.assertEqual(test.state, 'isi')
        self.assertIsNone(test._deadline)
        test._draw_stimulus.assert_not_called()
        test.after.assert_called_with(20, test._next_stimulus)
        process.poll.return_value = 0
        test._next_stimulus()
        test._draw_stimulus.assert_called_once()
        self.assertEqual(test.state, 'stimulus')

    def test_fixed_mode_preserves_legacy_timing(self):
        test = self.make_test(adaptive_timing=False, feedback_time=0.2)
        test._next_stimulus()
        test._respond(test._trial['ink'], rt=1.0)
        self.assertEqual(test._stimulus_time, 2.0)
        self.assertEqual(test._feedback_time, 0.2)
        test.after.assert_called_with(200, test._hide_feedback)
        test._hide_feedback()
        self.assertAlmostEqual(test._isi, test.config.isi_start * test.config.isi_decay)

    def test_restart_restores_controller_and_score(self):
        test = self.make_test()
        test._begin = Mock()
        test._score = 48000
        for _ in range(30):
            test._adapt_timing(0)
        test._restart()
        self.assertEqual(test._score, 25000)
        self.assertEqual(test._stimulus_time, 2.0)
        self.assertEqual(test._feedback_time, 0.5)
        self.assertEqual(test._smoothed_fraction, 0.5)
        self.assertEqual(test.events, [])
        test._player.stop.assert_called_once()

    def test_bar_color_and_fill_follow_score_not_controller(self):
        test = StroopStressTest(voice_debug=False)
        test._canvas = Mock()
        test._canvas.winfo_width.return_value = 1000
        test._canvas.winfo_height.return_value = 700
        test.font = Mock(return_value='font')
        test._draw_chrome()
        x0, y0, x1, y1 = test._bar_bounds
        for score, fraction, color in ((-100, 0, '#e04040'),
                                      (20000, 0.4, '#e04040'),
                                      (25000, 0.5, '#40c060'),
                                      (60000, 1.0, '#40c060')):
            test._score = score
            test._update_chrome()
            test._canvas.coords.assert_called_with(
                test._bar_id, x0, y0, x0 + (x1 - x0) * fraction, y1)
            test._canvas.itemconfigure.assert_called_with(test._bar_id, fill=color)
        test._canvas.create_line.assert_called_once()

    def test_heard_word_is_shown_and_highlighted_then_cleared_on_timeout(self):
        import gui.tests.Stroop.stroop as stroop_module
        with patch.object(stroop_module, 'time') as fake_time:
            fake_time.monotonic.return_value = 100.0
            test = self.make_test()
            test._draw_stimulus = Mock()
            test._heard = ""
            test._next_stimulus()
            self.assertEqual(test._heard, "")   # Fresh trial: nothing heard yet.
            fake_time.monotonic.return_value = 101.0
            test._spotter = Mock(ready=True, debug=False)
            test._spotter.drain_words.return_value = [(test._trial['ink'], 101.0)]
            test._spotter.drain_finals.return_value = [101.5]
            test._spotter.speech_idle.return_value = 1.0
            test._poll_voice()
            self.assertEqual(test._heard, test._trial['ink'])
            self.assertEqual(test._highlight, test._trial['ink'])
            test._draw_stimulus.assert_called()  # Shown the moment it is heard.
            fake_time.monotonic.return_value = 101.6
            test._tick()    # Judged on mic-quiet.
            self.assertEqual(len(test.events), 1)
            self.assertEqual(test._heard, test._trial['ink'])  # Kept on an answer.
            test._close_trial(None, 'isi')                      # A timeout must not
            self.assertEqual(test._heard, test._trial['ink'])   # touch a finished trial.
            test._next_stimulus()
            self.assertEqual(test._heard, "")   # Next word: label starts empty.

    def test_heard_word_is_cleared_when_the_trial_times_out(self):
        with patch('gui.tests.Stroop.stroop.time.monotonic') as clock:
            clock.return_value = 100.0
            test = self.make_test()
            test._heard = "leftover"
            test._next_stimulus()
            self.assertEqual(test._heard, "")
            clock.return_value = 102.1
            test._tick()
            self.assertEqual(len(test.events), 1)
            self.assertEqual(test._heard, "")   # Timeout: no heard label.

    def test_configuration_loads_and_invalid_bounds_fail(self):
        settings = load_settings(Path(__file__).resolve().parent / 'configs' / 'defaults.hjson')
        cfg = StroopStressConfig(**config_kwargs('stroop', settings, StroopStressConfig))
        self.assertEqual(cfg.score_target, 50000)
        self.assertEqual(cfg.score_start, 25000)
        self.assertTrue(cfg.adaptive_timing)
        for overrides in ({'score_target': 0}, {'stimulus_min': 5.0},
                          {'feedback_min': 1.0}, {'stimulus_time': 0},
                          {'adaptation_smoothing': 0}, {'score_band': 0.5},
                          {'response_mode': 'voice', 'min_voice_lag': 0.9}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                StroopStressConfig(**overrides)

    def test_simulated_participants_settle_near_middle(self):
        # Different response speeds, imperfect accuracy, real scoring and
        # real deadlines: adaptation must recover rather than run away.
        for mean_rt in (0.9, 1.6, 2.2):
            with self.subTest(mean_rt=mean_rt):
                rng = random.Random(10)
                test = self.make_test(stimulus_time=1.75, sounds=False)
                scores = []
                for _ in range(200):
                    test._next_stimulus()
                    rt = max(0.25, rng.gauss(mean_rt, 0.25))
                    if rt > test._trial_stimulus_time:
                        test._respond(None, rt=test._trial_stimulus_time)
                    else:
                        wrong = next(c for c in test.keys.values() if c != test._trial['ink'])
                        answer = test._trial['ink'] if rng.random() < 0.95 else wrong
                        test._respond(answer, rt=rt)
                    scores.append(test._score / test.config.score_target)
                self.assertGreaterEqual(sum(0.3 <= s <= 0.7 for s in scores[-100:]), 60)
                # A streak can legitimately end outside the band; evaluate
                # sustained behavior rather than forcing the last verdict.
                self.assertTrue(0.4 <= sum(scores[-100:]) / 100 <= 0.6)


if __name__ == '__main__':
    unittest.main()
