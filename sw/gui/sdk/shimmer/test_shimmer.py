#!/usr/bin/env python3
"""Unit tests for the Shimmer SDK: GSR decode, timebase and CSV round-trip."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from .acquire import LiveBlock, adc_to_conductance, adc_to_resistance, decode_gsr
from .recording import (CSV_COLUMNS, TICK_WRAP, Recording, Sample, Segment, Settings, csv_row,
                        export_recording, load_recording, samples_from_block)


class GsrDecode(unittest.TestCase):
    def test_decode_splits_range_and_adc(self):
        self.assertEqual(decode_gsr((2 << 14) | 3198), (2, 3198))
        self.assertEqual(decode_gsr(0xC2AA), (3, 682))

    def test_real_session_values(self):
        # Values captured live from Shimmer_6065 (LogAndStream 0.11.0).
        conductance = adc_to_conductance((2 << 14) | 3198)
        self.assertAlmostEqual(conductance, 3.69, delta=0.05)
        self.assertAlmostEqual(adc_to_resistance(2, 3198), 271_100, delta=2_000)

    def test_bias_point_is_open_circuit(self):
        # An unworn electrode settles at the 0.5 V bias (ADC ≈ 682).
        for gsr_range in range(4):
            self.assertIsNone(adc_to_conductance((gsr_range << 14) | 682))

    def test_huge_resistance_is_open_circuit(self):
        # Range 3, ADC 684 → ~1.5 GΩ: physically an open input.
        self.assertIsNone(adc_to_conductance((3 << 14) | 684))

    def test_conductance_is_inverse_resistance(self):
        raw = (1 << 14) | 3102
        resistance = adc_to_resistance(1, 3102)
        self.assertAlmostEqual(adc_to_conductance(raw), 1e6 / resistance, places=6)


class Timebase(unittest.TestCase):
    def test_samples_from_block_device_ticks(self):
        segment = Segment(0, 0.0, Settings(rate=51.2))
        rate_ticks = 32768 / 51.2
        block = LiveBlock(0, [(round(index * rate_ticks), (2 << 14) | 3000)
                              for index in range(4)])
        samples = samples_from_block(segment, block)
        self.assertEqual(len(samples), 4)
        self.assertAlmostEqual(samples[3].time_s, 3 / 51.2, places=6)

    def test_samples_from_block_index_fallback(self):
        segment = Segment(0, 5.0, Settings(rate=51.2))
        block = LiveBlock(0, [(None, (2 << 14) | 3000)] * 4)
        samples = samples_from_block(segment, block)
        self.assertAlmostEqual(samples[2].time_s, 5.0 + 2 / 51.2, places=6)

    def test_24bit_timestamp_wrap_is_unwrapped(self):
        # The Shimmer3 LogAndStream counter wraps every 512 s (2^24 ticks);
        # times must continue increasing instead of jumping ~512 s back.
        segment = Segment(0, 0.0, Settings(rate=51.2))
        near_wrap = TICK_WRAP - 100
        ticks = [near_wrap + index * 640 for index in range(4)]
        ticks[2:] = [tick - TICK_WRAP for tick in ticks[2:]]  # wrapped packets
        samples = samples_from_block(segment, LiveBlock(0, [(t, 100) for t in ticks]))
        self.assertTrue(all(samples[i].time_s > samples[i - 1].time_s
                            for i in range(1, len(samples))))
        # The step over the wrap is the nominal sample period, not -512 s.
        self.assertAlmostEqual(samples[2].time_s - samples[1].time_s, 1 / 51.2, places=6)

    def test_repeated_device_timestamps_are_bridged(self):
        # Observed at 10.24 Hz: consecutive packets repeat the same tick value
        # and then jump two periods (steps 0 / 3200 alternating).
        segment = Segment(0, 0.0, Settings(rate=10.24))
        ticks = [index // 2 * 3200 for index in range(6)]
        samples = samples_from_block(segment, LiveBlock(0, [(t, 100) for t in ticks]))
        times = [sample.time_s for sample in samples]
        self.assertTrue(all(times[i] > times[i - 1] for i in range(1, len(times))))
        for i in range(1, len(times)):
            self.assertAlmostEqual(times[i] - times[i - 1], 1 / 10.24, places=9)

    def test_missing_timestamp_bridges_from_last_time(self):
        segment = Segment(0, 0.0, Settings(rate=51.2))
        ticks = [0, 640, None, None, 2560]
        samples = samples_from_block(segment, LiveBlock(0, [(t, 100) for t in ticks]))
        times = [sample.time_s for sample in samples]
        self.assertTrue(all(times[i] > times[i - 1] for i in range(1, len(times))))
        self.assertAlmostEqual(times[4], 4 / 51.2, places=6)


class CsvRoundTrip(unittest.TestCase):
    def test_export_then_load(self):
        segment = Segment(0, 0.0, Settings(rate=51.2, device_name="Shimmer_TEST"))
        block = LiveBlock(0, [(round(index * 640), (2 << 14) | 3000) for index in range(10)])
        samples = samples_from_block(segment, block)
        recording = Recording()
        recording.begin(segment)
        recording.samples.extend(samples)
        recording.times.extend(sample.time_s for sample in samples)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.csv"
            export_recording(path, recording, {})
            loaded, metadata = load_recording(path)
            self.assertEqual(len(loaded.samples), 10)
            self.assertEqual(metadata["status"], "exported")
            row = csv_row(0, loaded.samples[0])
            self.assertEqual(len(row), len(CSV_COLUMNS))
            self.assertEqual(row[11], "Shimmer_TEST")
            self.assertLess(abs(loaded.samples[0].conductance - samples[0].conductance), 1e-9)


if __name__ == "__main__":
    unittest.main()
