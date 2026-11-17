"""Profile defaults, CLI persistence and nominal calibration; no physical loads."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biopac.acquire import Capture, VOLTS_PER_COUNT, csv_rows, main, parse_arguments
from biopac.configuration import Configuration, read_configuration, write_configuration
from biopac.profiles import DEFAULT_PROFILE, get_profile
from biopac.recording import Sample, Segment, Settings, load_recording


BN = "bionomadix-ppged-r"


class ProfileTests(unittest.TestCase):
    def test_default_is_the_existing_eda100c_configuration(self):
        settings = Settings.parse({})
        self.assertEqual(settings, Settings())
        self.assertEqual(settings, Settings.from_profile("eda100c"))
        self.assertEqual((settings.channel, settings.rate, settings.sensitivity, settings.zero_voltage), (3, 100, 2, 0))
        self.assertEqual(settings.filters, ("10 Hz", "DC", "DC"))
        self.assertEqual(settings.profile, DEFAULT_PROFILE)

    def test_bionomadix_defaults_and_filter_slots(self):
        settings = Settings.from_profile(BN)
        self.assertEqual((settings.channel, settings.rate, settings.sensitivity, settings.zero_voltage), (9, 100, 5, 0))
        self.assertEqual(settings.filters, ("DC to 3 Hz", "N/A", "N/A"))
        self.assertEqual(Settings.parse(settings.mapping()), settings)
        self.assertIn("output B, bank X", get_profile(BN).notes)
        self.assertIn("pairing", get_profile(BN).notes)

    def test_explicit_overrides_are_preserved_on_parse(self):
        values = {"profile": BN, "channel": 10, "rate": 200, "sensitivity": 4.95,
                  "zero_voltage": -0.02, "filters": ["DC to 1 Hz", "N/A", "N/A"]}
        settings = Settings.parse(values)
        self.assertEqual(settings, Settings(10, 200, 4.95, -0.02, ("DC to 1 Hz", "N/A", "N/A"), BN))
        self.assertEqual(Settings.parse(settings.mapping()), settings)

    def test_unknown_profile_is_not_silently_treated_as_eda100c(self):
        for value in ("typo", "", None, []):
            with self.subTest(profile=value), self.assertRaisesRegex(ValueError, "Unknown EDA hardware profile"):
                Settings.parse({"profile": value})

    def test_legacy_configuration_keeps_custom_acquisition_values(self):
        legacy = {"schema_version": 1, "acquisition": {"channel": 4, "rate": 500, "sensitivity": 10,
                  "zero_voltage": 0.2, "filters": ["1 Hz", "DC", "DC"]}}
        configuration = Configuration.parse(legacy)
        self.assertEqual(configuration.acquisition, Settings(4, 500, 10, 0.2, ("1 Hz", "DC", "DC")))

    def test_profile_and_calibration_overrides_survive_config_save_load(self):
        configuration = replace(Configuration(), acquisition=replace(Settings.from_profile(BN), zero_voltage=0.03, sensitivity=4.9))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "configuration.json"
            write_configuration(path, configuration)
            self.assertEqual(read_configuration(path), configuration)

    def test_nominal_open_and_resistor_conversions_in_gui_and_cli(self):
        # Synthetic counts, not measurements: rounding allows half an ADC LSB.
        for key, sensitivity in ((DEFAULT_PROFILE, 2), (BN, 5)):
            settings = Settings.from_profile(key)
            for expected_us in (0, 1, 10):
                voltage = expected_us / sensitivity
                count = round(voltage / VOLTS_PER_COUNT)
                sample = Sample(0, 0, count, Segment(0, 0, settings))
                capture = Capture(struct.pack(">h", count), settings.channel, settings.rate, 0.01, 0.01, "test", False)
                row = next(csv_rows(capture, settings.sensitivity, settings.zero_voltage))
                with self.subTest(profile=key, us=expected_us):
                    self.assertAlmostEqual(sample.voltage, voltage, delta=VOLTS_PER_COUNT / 2)
                    self.assertAlmostEqual(sample.conductance, expected_us, delta=sensitivity * VOLTS_PER_COUNT / 2)
                    self.assertEqual((row[3], row[4], row[5]), (count, sample.voltage, sample.conductance))

    def test_gain_and_offset_leave_raw_adc_and_voltage_unchanged(self):
        count = round(0.2 / VOLTS_PER_COUNT)
        raw = Sample(0, 0, count, Segment(0, 0, Settings()))
        calibrated = Sample(0, 0, count, Segment(0, 0, replace(Settings.from_profile(BN), zero_voltage=0.3)))
        self.assertEqual(calibrated.adc_count, raw.adc_count)
        self.assertEqual(calibrated.voltage, raw.voltage)
        self.assertAlmostEqual(calibrated.conductance, (raw.voltage - 0.3) * 5)
        self.assertLess(calibrated.conductance, 0)  # Never rectify unexpected offsets.

    def test_clipped_samples_are_not_hidden_by_profile_conversion(self):
        for count in (-32768, 32767):
            sample = Sample(0, 0, count, Segment(0, 0, Settings.from_profile(BN)))
            self.assertTrue(sample.clipped)
            self.assertEqual(sample.adc_count, count)
            self.assertAlmostEqual(sample.conductance, count * VOLTS_PER_COUNT * 5)


class CLIProfileTests(unittest.TestCase):
    def test_cli_default_and_explicit_profile(self):
        for extra, channel, gain, filters in (([], 3, 2, ("10 Hz", "DC", "DC")),
                (["--profile", BN], 9, 5, ("DC to 3 Hz", "N/A", "N/A"))):
            args = parse_arguments(["--interface", "test0"] + extra)
            self.assertEqual((args.channel, args.rate, args.sensitivity, args.zero_voltage, args.filters),
                             (channel, 100, gain, 0, filters))

    def test_explicit_cli_values_override_profile_regardless_of_option_order(self):
        args = parse_arguments(["--interface", "test0", "--channel", "10", "--rate", "200", "--sensitivity", "4.9",
                                "--zero-voltage", "0.1", "--filters", "DC to 1 Hz", "N/A", "N/A", "--profile", BN])
        self.assertEqual((args.channel, args.rate, args.sensitivity, args.zero_voltage, args.filters),
                         (10, 200, 4.9, 0.1, ("DC to 1 Hz", "N/A", "N/A")))

    def test_invalid_cli_options_fail_before_discovery(self):
        for extra in (["--profile", "typo"], ["--profile", BN, "--channel", "0"],
                      ["--profile", BN, "--channel", "17"], ["--sensitivity", "nan"],
                      ["--filters", "", "N/A", "N/A"]):
            with self.subTest(extra=extra), patch("biopac.acquire.discover") as discovery, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--interface", "test0"] + extra)
                discovery.assert_not_called()

    def test_cli_files_roundtrip_both_profiles_and_manual_overrides(self):
        raw = struct.pack(">4h", -10, 0, 655, 6554)
        device = {"serial": "fake", "ip": "169.254.1.2"}
        for key, firmware, channel, sensitivity, extra in ((DEFAULT_PROFILE, "2.1.06", 3, 2, []),
                (BN, "2.0.00", 9, 5, []), (BN, "2.0.00", 10, 4.9, ["--channel", "10", "--sensitivity", "4.9"])):
            with self.subTest(profile=key, channel=channel), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "capture.csv"
                client = Mock()
                client.information.return_value = {"firmware": firmware, "buffer_capacity_bytes": 10000, "status": 128}
                client.capture.return_value = Capture(raw, channel, 100, 0.04, 0.04, "test", False)
                with patch("biopac.acquire.discover", return_value=[device]), patch("biopac.acquire.MP160", return_value=client), redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["--interface", "test0", "--profile", key, "--seconds", "0.04", "--output", str(path)] + extra), 0)
                client.capture.assert_called_once_with(0.04, channel, 100, 10000)
                client.close.assert_called_once()
                self.assertEqual(Path(str(path) + ".raw").read_bytes(), raw)
                history, metadata = load_recording(path)
                settings = history.segments[0].settings
                self.assertEqual((settings.profile, settings.channel, settings.sensitivity), (key, channel, sensitivity))
                self.assertEqual(settings.filters, get_profile(key).filters)
                self.assertEqual(metadata["hardware_profile"], key)
                self.assertEqual(metadata["hardware_profile_defaults"]["channel"], get_profile(key).channel)
                self.assertEqual([s.adc_count for s in history.samples], [-10, 0, 655, 6554])
                self.assertAlmostEqual(history.samples[-1].conductance, 6554 * VOLTS_PER_COUNT * sensitivity)

    def test_unknown_firmware_still_blocks_acquisition(self):
        client = Mock()
        client.information.return_value = {"firmware": "9.9.99"}
        with patch("biopac.acquire.discover", return_value=[{"ip": "169.254.1.2"}]), patch("biopac.acquire.MP160", return_value=client), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--interface", "test0", "--profile", BN]), 1)
        client.capture.assert_not_called()
        client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
