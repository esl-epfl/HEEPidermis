"""Physical invariants and recording/configuration regression checks.

Run: python -m unittest discover -s sw/gui -p 'test_*.py'
"""

import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from analysis import ALGORITHMS, analyze
from configuration import read_configuration, validate_configuration, write_configuration
from recording import MotionDetector, Sample, conductance_us, invalid_periods, read_recording


class ConductanceTests(unittest.TestCase):
    def test_fixed_resistor_is_independent_of_current(self):
        # 500 kΩ, currents spanning the entire GUI slider.
        for current in (0.04, 0.2, 0.52, 1.0, 10.2):
            self.assertAlmostEqual(conductance_us(current * 500000, current), 2.0)

    def test_invalid_drops_are_not_rectified(self):
        for drop, current in ((-1000, .52), (0, .52), (1000, 0), (1000, -1), (math.nan, .52), (math.inf, .52)):
            self.assertIsNone(conductance_us(drop, current))

    def test_measured_offset_correction_preserves_current_invariance(self):
        for current in (.04, .2, .6):
            drop = current * 500000 + 30000
            self.assertAlmostEqual(conductance_us(drop, current, 30000), 2.0)


class MotionTests(unittest.TestCase):
    @staticmethod
    def sample(t, p=730000, n=600000, **kwargs):
        return Sample(t, p, n, **kwargs)

    def test_common_step_excluded_and_recovers(self):
        detector = MotionDetector(window_s=1)
        self.assertFalse(detector.classify(self.sample(0)))
        self.assertTrue(detector.classify(self.sample(.1, 733000, 603000)))
        self.assertTrue(detector.classify(self.sample(.5, 733000, 603000)))
        self.assertFalse(detector.classify(self.sample(2, 733000, 603000)))

    def test_signal_only_reference_only_and_opposite_motion_not_excluded(self):
        for p, n in ((730000, 603000), (733000, 600000), (733000, 597000)):
            detector = MotionDetector()
            detector.classify(self.sample(0))
            self.assertFalse(detector.classify(self.sample(.1, p, n)))

    def test_gradual_correlated_motion(self):
        detector = MotionDetector()
        flags = [detector.classify(self.sample(i / 10, 730000 + i * 300, 600000 + i * 300)) for i in range(10)]
        self.assertFalse(flags[0])
        self.assertTrue(flags[-1])

    def test_current_change_and_gap_reset_evidence(self):
        for sample in (self.sample(.1, 733000, 603000, current_ua=.8),
                       self.sample(20, 733000, 603000)):
            detector = MotionDetector()
            detector.classify(self.sample(0))
            self.assertFalse(detector.classify(sample))

    def test_duplicate_event_timestamp_keeps_exclusion(self):
        detector = MotionDetector()
        detector.classify(self.sample(0))
        sample = self.sample(.1, 733000, 603000)
        self.assertTrue(detector.classify(sample))
        self.assertTrue(detector.classify(sample))

    def test_exclusions_split_analysis(self):
        times = np.arange(100) / 10
        values = np.full(100, 2.)
        values[30:50] = np.nan
        values[50:] = 5.
        tonic, phasic, peaks = analyze(times, values, np.zeros(100), ALGORITHMS[1], 10, .01)
        np.testing.assert_allclose(tonic[:30], 2)
        np.testing.assert_allclose(tonic[50:], 5)
        self.assertTrue(np.all(np.isnan(tonic[30:50])))
        self.assertFalse(np.any(peaks))

    def test_all_invalid_reasons_create_red_periods(self):
        samples = [self.sample(0), self.sample(1), self.sample(2), self.sample(3)]
        samples[0].motion_artifact = True
        samples[1].valid = False
        samples[2].voltage_invalid = True
        self.assertEqual(list(invalid_periods(samples)), [(0, 3)])


class FileTests(unittest.TestCase):
    def test_old_session_infers_rate_and_retains_current(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.csv"
            path.write_text("time_s,vco_p_uV,vco_n_uV,injected_current_uA\n0,730000,630000,0.2\n0.1,730000,430000,0.6\n")
            samples = read_recording(path)
            self.assertEqual([s.current_ua for s in samples], [.2, .6])
            self.assertAlmostEqual(samples[0].sample_hz, 10)
            self.assertEqual([conductance_us(s.delta_uv, s.current_ua) for s in samples], [2, 2])

    def test_legacy_recording_without_current_uses_import_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.csv"
            path.write_text("time_s,vco_p_uV,vco_n_uV\n0,730000,630000\n")
            sample = read_recording(path, default_current_ua=.2)[0]
            self.assertEqual(sample.current_ua, .2)
            self.assertEqual(conductance_us(sample.delta_uv, sample.current_ua), 2)

    def test_recording_recovers_acquisition_validity_for_rescreening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.csv"
            path.write_text("time_s,vco_p_uV,vco_n_uV,injected_current_uA,valid,acquisition_valid,motion_artifact\n0,730000,630000,0.2,0,1,1\n")
            sample = read_recording(path)[0]
            self.assertTrue(sample.valid)
            self.assertTrue(sample.motion_artifact)

    def test_malformed_csv_rejected(self):
        for text in ("bad,columns\n1,2\n", "time_s,vco_p_uV,vco_n_uV,injected_current_uA\n0,nan,600000,0.2\n",
                     "time_s,vco_p_uV,vco_n_uV,injected_current_uA\n1,700000,600000,0.2\n0,700000,600000,0.2\n"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.csv"
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_recording(path)

    def test_configuration_roundtrip_and_validation(self):
        values = {"current_code": 13, "motion_enabled": True, "filter_window": 50, "voltage_offset_mv": 0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "configuration.json"
            write_configuration(path, values)
            self.assertEqual(validate_configuration(read_configuration(path), {}, 46), values)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())
        for invalid in ({"current_code": 0}, {"motion_window": "nan"}, {"motion_enabled": "false"}, {"dlc_width": 3}, {"version": 2}):
            with self.assertRaises(ValueError):
                validate_configuration(invalid, {}, 46)


if __name__ == "__main__":
    unittest.main()
