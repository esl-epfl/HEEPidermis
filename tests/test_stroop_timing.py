"""Headless regression checks for one verdict per displayed stimulus.

Run with: python3 -m unittest discover -s tests -p 'test_stroop_timing.py'
"""

import unittest
from unittest.mock import Mock, patch

from cognitive import StroopTest, StroopStressTest


class StroopDeadlineTests(unittest.TestCase):
    def make_test(self, cls):
        test = cls(stimulus_time=2.0, voice_max_wait=0.5,
                   min_voice_lag=0.05, seed=7, voice_debug=False)
        test.after = Mock()
        test._draw_stimulus = Mock()
        test._draw_fixation = Mock()
        test._draw_feedback = Mock()
        test._update_chrome = Mock()
        test._player = Mock()
        test._spotter = Mock(ready=True, debug=False)
        test._spotter.drain_words.return_value = []
        test._spotter.drain_finals.return_value = []
        test._spotter.speech_idle.return_value = 0.0
        test._next_stimulus()
        return test

    def answer(self, test, correct):
        ink = test._trial['ink']
        return ink if correct else next(c for c in test.keys.values() if c != ink)

    def check_verdict(self, test, response):
        self.assertEqual(len(test.events), 1)
        event = test.events[0]
        self.assertEqual(event['response'], response or '')
        self.assertEqual(event['timed_out'], response is None)
        self.assertIsNone(test._deadline)
        self.assertTrue(test._spotter.paused)
        self.assertIsNone(test._pending)
        if isinstance(test, StroopStressTest):
            correct = response == test._trial['ink']
            test._player.play.assert_called_once_with('cling' if correct else 'buzz')
            self.assertEqual(test._score, test.config.score_start + event['score_delta'])
            test._draw_feedback.assert_called_once()
        # Exactly one next-phase callback, in addition to periodic ticks.
        transitions = [call for call in test.after.call_args_list
                       if call.args[1] != test._tick]
        self.assertEqual(len(transitions), 1)

    def test_detection_cancels_deadline_while_judgment_is_pending(self):
        for cls in (StroopTest, StroopStressTest):
            with self.subTest(test=cls.NAME), patch('cognitive.stroop.time.monotonic') as clock:
                clock.return_value = 100.0
                test = self.make_test(cls)
                clock.return_value = 101.8
                test._spotter.drain_words.return_value = [(test._trial['ink'], 101.8)]
                test._tick()
                test._spotter.drain_words.return_value = []
                self.assertIsNone(test._deadline)
                clock.return_value = 102.1
                test._tick()
                self.assertEqual(test.events, [])
                self.assertIsNotNone(test._pending)
                test._respond(None)  # A stale timeout cannot consume the pending answer.
                self.assertEqual(test.events, [])

    def test_judgment_after_deadline_never_also_times_out(self):
        for cls in (StroopTest, StroopStressTest):
            for correct in (True, False):
                for end_signal in ('quiet', 'final', 'cap'):
                    with self.subTest(test=cls.NAME, correct=correct, signal=end_signal), \
                            patch('cognitive.stroop.time.monotonic') as clock:
                        clock.return_value = 100.0
                        test = self.make_test(cls)
                        response = self.answer(test, correct)
                        clock.return_value = 101.8
                        test._spotter.drain_words.return_value = [(response, 101.8)]
                        test._tick()
                        test._spotter.drain_words.return_value = []
                        clock.return_value = 102.4 if end_signal == 'cap' else 102.1
                        if end_signal == 'quiet':
                            test._spotter.speech_idle.return_value = 1.0
                        elif end_signal == 'final':
                            test._spotter.drain_finals.return_value = [102.1]
                        test._tick()
                        self.check_verdict(test, response)
                        # Delayed callbacks/keys during feedback must not judge again.
                        test._respond(None)
                        test._respond(response)
                        test._tick()
                        self.check_verdict(test, response)

    def test_unanswered_timeout_fires_once_and_next_word_gets_new_deadline(self):
        for cls in (StroopTest, StroopStressTest):
            with self.subTest(test=cls.NAME), patch('cognitive.stroop.time.monotonic') as clock:
                clock.return_value = 100.0
                test = self.make_test(cls)
                clock.return_value = 102.1
                test._tick()
                test._tick()
                test._respond(None)
                self.check_verdict(test, None)
                clock.return_value = 103.0
                test._next_stimulus()
                self.assertEqual(test._deadline, 103.0 + test._stimulus_duration())
                self.assertFalse(test._spotter.paused)
                clock.return_value = test._deadline + 0.1
                test._tick()
                self.assertEqual(len(test.events), 2)
                self.assertTrue(all(e['timed_out'] for e in test.events))

    def test_keyboard_answer_cancels_deadline(self):
        for cls in (StroopTest, StroopStressTest):
            with self.subTest(test=cls.NAME), patch('cognitive.stroop.time.monotonic') as clock:
                clock.return_value = 100.0
                test = self.make_test(cls)
                response = self.answer(test, True)
                clock.return_value = 101.0
                test._respond(response)
                clock.return_value = 102.1
                test._tick()
                test._respond(None)
                self.check_verdict(test, response)


if __name__ == '__main__':
    unittest.main()
