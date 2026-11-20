"""Anti-alias analysis and calibration boundaries, without the MP160 or Tk."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gui.sdk.biopac.acquire import VOLTS_PER_COUNT
from gui.sdk.biopac.processing import analyze_recording
from gui.sdk.biopac.recording import Sample, Segment, Settings


class AnalysisTests(unittest.TestCase):
    def signal(self, rate, seconds, function, *, number=0, start=0, sensitivity=2):
        segment = Segment(number, start, replace(Settings(), rate=rate, sensitivity=sensitivity))
        return [Sample(start + i / rate, i, round(function(i / rate) / VOLTS_PER_COUNT), segment)
                for i in range(round(rate * seconds))]

    def test_empty_and_short_inputs(self):
        self.assertEqual(analyze_recording([])["times"], [])
        self.assertEqual(analyze_recording(self.signal(100, 0.02, lambda t: 1))["times"], [])
        result = analyze_recording(self.signal(100, 0.03, lambda t: 1))
        self.assertEqual(len(result["times"]), 1)

    def test_negative_constant_is_not_rectified_or_baseline_subtracted(self):
        samples = self.signal(100, 5, lambda t: -1)
        result = analyze_recording(samples)
        np.testing.assert_allclose(result["tonic"], -2, atol=0.001)
        np.testing.assert_allclose(result["phasic"], 0, atol=1e-10)

    def test_analysis_rate_is_ten_hz_for_every_device_rate(self):
        for rate in (100, 200, 500, 1000):
            result = analyze_recording(self.signal(rate, 2, lambda t: 1))
            with self.subTest(rate=rate):
                self.assertEqual(len(result["times"]), 20)
                np.testing.assert_allclose(np.diff(result["times"]), 0.1, atol=1e-12)

    def test_high_frequency_noise_is_filtered_before_downsampling(self):
        samples = self.signal(1000, 10, lambda t: 1 + 0.3 * np.cos(2 * np.pi * 100 * t))
        result = analyze_recording(samples)
        reconstructed = np.array(result["tonic"]) + result["phasic"]
        np.testing.assert_allclose(reconstructed[10:-10], 2, atol=0.003)
        # Naively taking every 100th value would alias this noise to a +0.6 µS offset.
        self.assertGreater(samples[100].conductance, 2.59)

    def test_pauses_and_calibration_changes_are_separate_analysis_intervals(self):
        samples = self.signal(100, 2, lambda t: 1)
        samples += self.signal(200, 2, lambda t: 1, number=1, start=10, sensitivity=5)
        result = analyze_recording(samples, average=20)
        np.testing.assert_allclose(result["tonic"][:20], 2, atol=0.001)
        np.testing.assert_allclose(result["tonic"][20:], 5, atol=0.001)
        self.assertEqual(result["times"][20], 10)
        self.assertEqual(set(result["segments"]), {0, 1})

    def test_clipping_splits_the_analysis_and_resets_average(self):
        samples = self.signal(100, 4, lambda t: 1)
        samples[200] = replace(samples[200], adc_count=32767)
        result = analyze_recording(samples, average=10)
        self.assertEqual(set(result["segments"]), {0, 1})
        self.assertNotIn(2.0, result["times"])
        np.testing.assert_allclose(result["tonic"], 2, atol=0.001)

    def test_processing_uses_profile_calibration_without_mixing_segments(self):
        count = round(0.2 / VOLTS_PER_COUNT)
        segments = [Segment(0, 0, Settings.from_profile("eda100c")), Segment(1, 10, Settings.from_profile("bionomadix-ppged-r"))]
        samples = [Sample(segment.start_s + i / 100, i, count, segment) for segment in segments for i in range(200)]
        result = analyze_recording(samples, average=20)
        np.testing.assert_allclose(result["tonic"][:20], 0.4, atol=0.002)
        np.testing.assert_allclose(result["tonic"][20:], 1.0, atol=0.002)
        np.testing.assert_allclose(result["phasic"], 0, atol=1e-10)
        self.assertEqual(result["times"][20], 10)


if __name__ == "__main__":
    unittest.main()
