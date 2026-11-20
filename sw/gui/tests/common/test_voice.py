"""Speaker-vector voice isolation: enrollment and gating, without hardware.

Run with: python3 -m unittest discover -s tests/common -p 'test_*.py'  (from sw/gui)
"""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gui.tests.common.voice import VoiceSpotter, cosine_similarity


class CosineTests(unittest.TestCase):
    def test_similarity_bounds(self):
        a = [1.0, 0.0, 2.0]
        self.assertAlmostEqual(cosine_similarity(a, a), 1.0)
        self.assertAlmostEqual(cosine_similarity(a, [-1.0, 0.0, -2.0]), -1.0)
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [0.0, 3.0]), 0.0)
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 1.0]), 0.0)


class SpeakerFilterTests(unittest.TestCase):
    WORDS = ("one", "two")

    def make_spotter(self, **kwargs):
        spotter = VoiceSpotter(self.WORDS, **kwargs)  # start() does hardware.
        spotter.paused = False
        return spotter

    def test_finish_learning_without_samples_does_not_enroll(self):
        spotter = self.make_spotter(speaker_filter=True)
        spotter.start_learning()
        self.assertFalse(spotter.finish_learning())
        self.assertFalse(spotter.speaker_enrolled)

    def test_filter_off_learns_nothing_and_passes_every_voice(self):
        spotter = self.make_spotter(speaker_filter=False)
        spotter.start_learning()
        spotter._emit("one", 1.0, [1.0, 0.0])
        self.assertFalse(spotter.finish_learning())
        self.assertEqual(spotter.drain_words(), [("one", 1.0)])
        spotter._spoken.clear()
        spotter._emit("two", 2.0, [-5.0, 0.0])
        self.assertEqual(spotter.drain_words(), [("two", 2.0)])

    def test_enrollment_averages_samples_and_gates_other_voices(self):
        spotter = self.make_spotter(speaker_filter=True, speaker_threshold=0.5)
        spotter.start_learning()
        spotter._emit("one", 1.0, [1.0, 1.0])
        spotter._emit("two", 2.0, [3.0, 1.0])
        self.assertTrue(spotter.finish_learning())
        self.assertEqual(spotter._speaker_profile, [2.0, 1.0])
        self.assertEqual(spotter.drain_words(), [("one", 1.0), ("two", 2.0)])
        spotter._spoken.clear()
        spotter._emit("one", 3.0, [-1.0, -1.0])   # A different voice: dropped.
        self.assertEqual(spotter.drain_words(), [])
        self.assertEqual(spotter.dropped_voices, 1)
        spotter._spoken.clear()
        spotter._emit("two", 4.0, [2.0, 1.0])     # The participant: accepted.
        self.assertEqual(spotter.drain_words(), [("two", 4.0)])

    def test_missing_vector_cannot_be_judged_so_the_word_passes(self):
        spotter = self.make_spotter(speaker_filter=True, speaker_threshold=0.5)
        spotter.start_learning()
        spotter._emit("one", 1.0, [1.0, 1.0])
        spotter.finish_learning()
        self.assertEqual(spotter.drain_words(), [("one", 1.0)])
        spotter._spoken.clear()
        spotter._emit("one", 2.0, None)  # No speaker data: never reject.
        self.assertEqual(spotter.drain_words(), [("one", 2.0)])

    def test_paused_input_still_drops_words(self):
        spotter = self.make_spotter(speaker_filter=True)
        spotter.paused = True
        spotter._emit("one", 1.0, [1.0, 0.0])
        self.assertEqual(spotter.drain_words(), [])


if __name__ == "__main__":
    unittest.main()
