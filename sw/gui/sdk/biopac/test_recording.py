"""Session persistence and shared plotting math; no hardware required."""

import csv
from dataclasses import replace
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gui.sdk.biopac.acquire import LiveBlock, VOLTS_PER_COUNT
from gui.sdk.biopac.configuration import Configuration, ProcessingSettings, read_configuration, write_configuration
from gui.sdk.biopac.recording import (OUTPUT_RATES, Recording, Segment, SessionWriter, Settings,
                               acquisition_rate, export_recording, load_recording, moving_average,
                               samples_from_block)
from gui.sdk.biopac.profiles import get_profile
from gui.common.plots import TimeViewport, envelope, ticks


def block(first, *values):
    return LiveBlock(first, struct.pack(f">{len(values)}h", *values))


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def history(self):
        history = Recording()
        history.begin(Segment(0, 0, Settings.from_profile("eda100c")))
        history.append(0, block(0, -1000, 0, 1000))
        history.finish(0)
        history.begin(Segment(1, 2.0, replace(Settings.from_profile("eda100c"), sensitivity=5, filters=("1 Hz", "DC", "DC"))))
        history.append(1, block(0, 1000, 2000))
        return history

    def test_old_samples_keep_calibration_and_pause_gaps(self):
        history = self.history()
        self.assertLess(history.samples[0].conductance, 0)
        self.assertAlmostEqual(history.samples[2].conductance, 1000 * VOLTS_PER_COUNT * 2)
        self.assertAlmostEqual(history.samples[3].conductance, 1000 * VOLTS_PER_COUNT * 5)
        self.assertEqual(history.times, [0.0, 0.01, 0.02, 2.0, 2.01])
        self.assertEqual(history.samples[2].segment.settings.filters[0], "10 Hz")

    def test_blocks_cannot_be_missing_or_duplicated(self):
        history = self.history()
        for bad in (block(0, 100), LiveBlock(2, b"x"), block(4, 100)):
            with self.subTest(block=bad), self.assertRaises(ValueError):
                history.append(1, bad)

    def test_cross_segment_time_must_increase(self):
        history = self.history()
        history.begin(Segment(2, 0, Settings()))
        with self.assertRaisesRegex(ValueError, "timestamps"):
            history.append(2, block(0, 100))

    def test_average_restarts_at_calibration_changes_and_clipping(self):
        history = self.history()
        history.append(1, block(2, 32767, 10, 30))
        volts, conductance, identities = moving_average(history.samples, 3)
        self.assertAlmostEqual(volts[3], 1000 * VOLTS_PER_COUNT)
        self.assertAlmostEqual(conductance[3], volts[3] * 5)
        self.assertTrue(math.isnan(volts[5]))
        self.assertAlmostEqual(volts[6], 10 * VOLTS_PER_COUNT)
        self.assertAlmostEqual(volts[7], 20 * VOLTS_PER_COUNT)
        self.assertEqual(identities, [0, 0, 0, 1, 1, 1, 1, 1])
        with self.assertRaises(ValueError):
            moving_average([], 0)

    def test_writer_autosaves_before_close_and_records_verification(self):
        writer = SessionWriter(self.directory, {"serial": "test"})
        self.addCleanup(writer.close)
        segment = Segment(0, 0, Settings())
        writer.begin(segment)
        writer.append(segment, block(0, -1, 2, 32767))
        with writer.path.open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 3)
        self.assertEqual(Path(str(writer.path) + ".raw").read_bytes(), block(0, -1, 2, 32767).raw)
        self.assertFalse(json.loads(Path(str(writer.path) + ".json").read_text())["segments"][0]["verified"])
        writer.finish(segment)
        loaded, metadata = load_recording(writer.path)
        self.assertEqual([s.adc_count for s in loaded.samples], [-1, 2, 32767])
        self.assertTrue(loaded.segments[0].verified)
        self.assertEqual(metadata["samples"], 3)

    def test_output_rates_cover_the_heepidermis_steps(self):
        self.assertEqual(min(OUTPUT_RATES), 0.1)
        self.assertEqual(OUTPUT_RATES[-1], 1000.0)
        self.assertIn(10.0, OUTPUT_RATES)
        self.assertEqual(acquisition_rate(10), 100)
        self.assertEqual(acquisition_rate(100), 100)
        self.assertEqual(acquisition_rate(101), 200)
        with self.assertRaises(ValueError):
            acquisition_rate(1001)
        with self.assertRaises(ValueError):
            acquisition_rate(0.05)

    def test_downsampling_keeps_evenly_spaced_samples_across_block_boundaries(self):
        segment = Segment(0, 0, replace(Settings(), rate=100, output_rate=10))
        kept = []
        for first, values in ((0, list(range(1, 21))), (20, list(range(21, 41))), (40, list(range(41, 51)))):
            kept.extend(samples_from_block(segment, block(first, *values)))
            segment.samples = first + len(values)
        self.assertEqual([sample.index for sample in kept], [0, 10, 20, 30, 40])
        self.assertEqual([sample.time_s for sample in kept], [0.0, 0.1, 0.2, 0.3, 0.4])
        self.assertEqual([sample.adc_count for sample in kept], [1, 11, 21, 31, 41])
        self.assertEqual(segment.samples, 50)
        full = Segment(0, 0, Settings())
        self.assertEqual([s.index for s in samples_from_block(full, block(0, 7, 8))], [0, 1])

    def test_writer_and_loader_roundtrip_a_downsampled_recording(self):
        writer = SessionWriter(self.directory, {"serial": "test"})
        self.addCleanup(writer.close)
        segment = Segment(0, 0, replace(Settings(), rate=100, output_rate=10))
        writer.begin(segment)
        for first in (0, 10, 20):
            writer.append(segment, block(first, *range(first + 1, first + 11)))
        writer.finish(segment)
        loaded, metadata = load_recording(writer.path)
        self.assertEqual(loaded.segments[0].settings.sample_rate, 10)
        self.assertEqual(loaded.segments[0].settings.rate, 100)
        self.assertEqual([s.adc_count for s in loaded.samples], [1, 11, 21])
        self.assertEqual([s.time_s for s in loaded.samples], [0.0, 0.1, 0.2])
        self.assertEqual(metadata["hardware_samples"], 30)
        self.assertEqual(metadata["samples"], 3)

    def test_analysis_grid_matches_the_output_rate(self):
        from gui.sdk.biopac.processing import analyze_recording
        for output in (10, 5):
            with self.subTest(output=output):
                history = Recording()
                history.begin(Segment(0, 0, replace(Settings(), rate=100, output_rate=output)))
                step = 100 // output
                values = [int(1000 * math.sin(i / step)) for i in range(200)]
                history.append(0, block(0, *values))
                result = analyze_recording(history.samples)
                self.assertGreater(len(result["tonic"]), 0)
                self.assertTrue(all(math.isfinite(value) for value in result["tonic"]))

    def test_sensitivities_are_profile_specific_choices(self):
        self.assertEqual(get_profile("eda100c").sensitivities, (2.0, 5.0, 10.0, 20.0))
        self.assertEqual(get_profile("bionomadix-ppged-r").sensitivities, (5.0,))

    def test_failed_segment_is_preserved_and_shaded(self):
        writer = SessionWriter(self.directory, {})
        self.addCleanup(writer.close)
        segment = Segment(0, 0, Settings())
        writer.begin(segment)
        writer.append(segment, block(0, 123, 456))
        writer.finish(segment, "final snapshot mismatch")
        history, metadata = load_recording(writer.path)
        self.assertFalse(history.segments[0].verified)
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(history.invalid_spans(), [(0, 0.02)])

    def test_writer_creation_closes_files_on_metadata_failure(self):
        with patch("gui.sdk.biopac.recording.write_json", side_effect=OSError("disk full")), self.assertRaises(OSError):
            SessionWriter(self.directory, {})

    def test_export_roundtrip_preserves_segments_and_simulation_label(self):
        path = self.directory / "export.csv"
        history = self.history()
        export_recording(path, history, {"average": 5}, {"simulated": True})
        loaded, metadata = load_recording(path)
        self.assertEqual([s.conductance for s in loaded.samples], [s.conductance for s in history.samples])
        self.assertEqual(loaded.times, history.times)
        self.assertEqual(loaded.segments[1].settings, history.segments[1].settings)
        self.assertFalse(loaded.segments[1].verified)
        self.assertTrue(metadata["simulated"])
        self.assertEqual(metadata["processing"]["average"], 5)
        with self.assertRaises(FileExistsError):
            export_recording(path, history, {})

    def test_load_original_batch_csv_with_its_metadata(self):
        path = self.directory / "batch.csv"
        path.write_text("sample_index,time_s_nominal,channel,adc_count,voltage_v_nominal,conductance_us_nominal,adc_clipped\n0,0,3,-100,0,0,0\n1,0.002,3,200,0,0,0\n")
        Path(str(path) + ".json").write_text(json.dumps({"sample_rate_hz_nominal": 500,
            "sensitivity_us_per_v_manual": 2.0, "calibrated_zero_voltage_v": 0.1,
            "hardware_filters_manual": ["10 Hz", "DC", "DC"], "frozen_buffer_verified": True}))
        history, _ = load_recording(path)
        self.assertEqual(history.segments[0].settings.rate, 500)
        self.assertTrue(history.segments[0].verified)
        self.assertLess(history.samples[0].conductance, 0)

    def test_invalid_csv_reports_row_without_loading_partial_history(self):
        path = self.directory / "bad.csv"
        for value in ("NaN", "-1", "inf"):
            path.write_text(f"time_s,adc_count\n0,2\n{value},3\n")
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "row 3"):
                load_recording(path)

    def test_mixed_profile_autosave_and_export_retain_each_calibration(self):
        writer = SessionWriter(self.directory, {})
        self.addCleanup(writer.close)
        original = Segment(0, 0, Settings.from_profile("eda100c"))
        wireless = Segment(1, 3, replace(Settings.from_profile("bionomadix-ppged-r"), zero_voltage=0.01))
        data = block(0, -10, 655, 6554)
        for segment in (original, wireless):
            writer.begin(segment)
            writer.append(segment, data)
            writer.finish(segment)
        history, metadata = load_recording(writer.path)
        self.assertEqual([s.settings for s in history.segments.values()], [original.settings, wireless.settings])
        self.assertEqual(metadata["segments"][1]["settings"]["profile"], "bionomadix-ppged-r")
        self.assertEqual(metadata["hardware_profile_defaults"]["bionomadix-ppged-r"]["sensitivity"], 5)
        self.assertEqual(Path(str(writer.path) + ".raw").read_bytes(), data.raw * 2)
        for left, right in zip(history.samples[:3], history.samples[3:]):
            self.assertEqual(left.voltage, right.voltage)
            self.assertEqual(left.adc_count, right.adc_count)
            self.assertAlmostEqual(left.conductance, left.voltage * 2)
            self.assertAlmostEqual(right.conductance, (right.voltage - 0.01) * 5)
        path = self.directory / "mixed.csv"
        export_recording(path, history, {})
        for with_sidecar in (True, False):
            if not with_sidecar:
                Path(str(path) + ".json").unlink()
            with self.subTest(with_sidecar=with_sidecar):
                reloaded, _ = load_recording(path)
                self.assertEqual([s.conductance for s in reloaded.samples], [s.conductance for s in history.samples])
                self.assertEqual([s.settings for s in reloaded.segments.values()], [original.settings, wireless.settings])

    def test_legacy_live_csv_without_profile_keeps_explicit_calibration(self):
        path = self.directory / "legacy.csv"
        path.write_text("sample_index,time_s,segment,segment_sample_index,channel,sample_rate_hz,adc_count,"
                        "voltage_v_nominal,conductance_us_nominal,sensitivity_us_per_v,zero_voltage_v,"
                        "filter_1,filter_2,filter_3,adc_clipped\n"
                        "0,0,0,0,4,200,1000,0,0,10,0.1,1 Hz,DC,DC,0\n")
        history, _ = load_recording(path)
        self.assertEqual(history.segments[0].settings, Settings(4, 200, 10, 0.1, ("1 Hz", "DC", "DC")))
        self.assertAlmostEqual(history.samples[0].conductance, (1000 * VOLTS_PER_COUNT - 0.1) * 10)

    def test_segment_sidecar_profile_is_used_when_csv_has_no_profile_column(self):
        path = self.directory / "sidecar.csv"
        settings = replace(Settings.from_profile("bionomadix-ppged-r"), sensitivity=4.8, zero_voltage=0.02)
        path.write_text("time_s,segment,adc_count\n0,0,6554\n")
        Path(str(path) + ".json").write_text(json.dumps({"segments": [{"number": 0, "settings": settings.mapping()}]}))
        history, _ = load_recording(path)
        self.assertEqual(history.segments[0].settings, settings)


class ConfigurationTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            configuration = replace(Configuration(), interface="test0", ip="169.254.164.181")
            write_configuration(path, configuration)
            self.assertEqual(read_configuration(path), configuration)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_invalid_acquisition_is_rejected(self):
        for values in ({"channel": 0}, {"rate": 50}, {"sensitivity": -1}, {"sensitivity": "NaN"},
                       {"zero_voltage": "inf"}, {"filters": ["", "DC", "DC"]}, {"filters": "DC"}, []):
            with self.subTest(values=values), self.assertRaises((ValueError, TypeError)):
                Settings.parse(values)

    def test_invalid_processing_is_rejected(self):
        for values in ({"average": 0}, {"smoothing": "NaN"}, {"smoothing": -1},
                       {"prominence": 0}, {"algorithm": "not an algorithm"}, []):
            with self.subTest(values=values), self.assertRaises((ValueError, TypeError)):
                ProcessingSettings.parse(values)

    def test_invalid_configuration_is_rejected(self):
        for values in ({"schema_version": 99}, {"ip": "example.com"}, {"span": "NaN"},
                       {"span": 0}, {"output_dir": ""}, {"acquisition": []}, []):
            with self.subTest(values=values), self.assertRaises((ValueError, TypeError)):
                Configuration.parse(values)


class ViewportTests(unittest.TestCase):
    def test_follow_pan_zoom_and_scrollbar(self):
        view = TimeViewport(span=10)
        view.update(100)
        self.assertEqual(view.start, 90)
        view.scroll(-1)
        self.assertFalse(view.follow)
        view.update(110)
        self.assertAlmostEqual(view.start, 88.8)
        view.zoom(2)
        self.assertAlmostEqual(view.start, 78.8)
        view.moveto(0)
        self.assertEqual(view.fractions(), (0, 20 / 110))
        view.live()
        self.assertEqual(view.start, 90)
        view.zoom(100000)
        self.assertEqual(view.span, 7200)
        self.assertEqual(view.start, 0)

    def test_envelope_preserves_extrema_and_temporal_order(self):
        times = [i / 1000 for i in range(1000)]
        values = [0.0] * 1000
        values[101], values[102] = 100, -100
        result = envelope(times, values, 0, 1, 10)
        self.assertIn((0.101, 100), result)
        self.assertIn((0.102, -100), result)
        self.assertEqual(result, sorted(result))
        self.assertLessEqual(len(result), 40)

    def test_ticks_are_nice_and_bounded(self):
        self.assertEqual(ticks(-1, 1, 4), [-1.0, -0.5, 0.0, 0.5, 1.0])
        self.assertEqual(ticks(0, 30, 7), [0, 5, 10, 15, 20, 25, 30])


if __name__ == "__main__":
    unittest.main()
