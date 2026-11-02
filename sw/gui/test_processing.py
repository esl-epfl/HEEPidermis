"""Physical invariants and recording/configuration regression checks.

Run: python -m unittest discover -s sw/gui -p 'test_*.py'
"""

import math
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from analysis import ALGORITHMS, analyze, interpolate_signal
from acquisition import DLCDecoder
from configuration import read_configuration, validate_configuration, write_configuration
from recording import (MotionDetector, Sample, conductance_us, invalid_periods,
                       buffer_loss_periods, restore_buffer_loss, read_recording, recording_configuration_path,
                       recording_uart_path)


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


class OpenOCDTests(unittest.TestCase):
    def test_failed_hart_examination_stops_before_serial_open(self):
        from gui import VCOGui

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".openocd.log").write_text(
                "Error: Fatal: Hart 0 failed to halt during examine()\n")
            messages = []
            closed = []
            gui = SimpleNamespace(shutting_down=False, connection_generation=1,
                                  recording_state="starting",
                                  _log=lambda message, **_kwargs: messages.append(message),
                                  close_device=lambda: closed.append(True),
                                  _open_serial=lambda: self.fail("Serial must not open"))
            with patch("gui.ROOT", Path(directory)):
                VCOGui._finish_open(gui, 1)
        self.assertEqual(closed, [True])
        self.assertTrue(any("could not halt" in message for message in messages))


class DLCDecoderTests(unittest.TestCase):
    def test_time_and_amplitude_overflow_are_distinct(self):
        decoder = DLCDecoder(0, 0, 3, 0, 10, 100000)
        self.assertEqual(decoder.event_kind(0b00100000), "time_overflow")
        self.assertEqual(decoder.event_kind(0b00000111, 0b00000001), "amplitude_up")
        self.assertEqual(decoder.event_kind(0b00010001), "amplitude_down")
        self.assertIsNone(decoder.event_kind(0b00100001))

    def test_amplitude_continuations_share_the_same_tick(self):
        decoder = DLCDecoder(0, 0, 3, 0, 10, 100000)
        points = list(decoder.decode(bytes((0b00100111, 0b00000111, 0b00000001))))
        self.assertEqual([point[0] for point in points], [1, 1, 1])

    def test_sixteen_narrow_amplitude_packets_form_one_crossing(self):
        decoder = DLCDecoder(0, 0, 6, 0, 10, 100000)
        packets = bytes((0x05,) + (0x01,) * 15)
        events = list(decoder.decode_events(packets))
        self.assertEqual([event[0] for event in events], [1] * 16)
        self.assertEqual(events[-1][3], 16)
        self.assertEqual(decoder.event_kind(packets[0], packets[1]), "amplitude_up")
        self.assertEqual(interpolate_signal([0, 1, 1], [0, 1, 16], [1], "Linear")[0], 16)

    def test_time_overflow_advances_only_the_next_crossing_time(self):
        decoder = DLCDecoder(0, 0, 3, 0, 10, 100000)
        payload = bytes((0b00100000, 0b00100000, 0b00100001))
        events = list(decoder.decode_events(payload))
        self.assertEqual([event[0] for event in events], [1, 2, 3])
        self.assertEqual([event[4] for event in events], [0, 0, 1])
        overflow = Sample(.1, 0, 0, source="dlc", dlc_byte=payload[0],
                          dlc_kind="time_overflow", dlc_level=0, dlc_level_change=0)
        self.assertFalse(overflow.dlc_amplitude_knot)
        self.assertTrue(Sample(.3, 0, 0, source="dlc", dlc_byte=payload[-1],
                               dlc_level=1, dlc_level_change=1).dlc_amplitude_knot)

    def test_decoder_exposes_level_and_signed_change(self):
        decoder = DLCDecoder(10, 0, 3, 0, 10, 100000)
        events = list(decoder.decode_events(bytes((0b00100011, 0b00010010))))
        self.assertEqual([(event[3], event[4]) for event in events], [(13, 3), (11, -2)])


class DLCBufferTests(unittest.TestCase):
    def test_buffer_configuration_accepts_arbitrary_sizes(self):
        self.assertEqual(validate_configuration({"dlc_buffer_events": 24}, {}, 10)["dlc_buffer_events"], 24)
        for size in (0, 2048):
            with self.assertRaises(ValueError):
                validate_configuration({"dlc_buffer_events": size}, {}, 10)

    def test_loss_excludes_sample_and_creates_yellow_period(self):
        sample = Sample(2.0, 700000, 600000, source="dlc", sample_hz=10, buffer_loss=True)
        self.assertFalse(sample.analysis_valid)
        self.assertEqual(list(buffer_loss_periods([sample])), [(2.0, 2.1)])

    def test_uart_sidecar_restores_loss_on_previous_reference(self):
        first = Sample(0, 700000, 600000, source="dlc")
        second = Sample(1, 700000, 600000, source="dlc")
        restore_buffer_loss([first, second], ["A,1", "B,1", "R,0", "X,100,16", "A,1", "B,1", "R,1"])
        self.assertTrue(first.buffer_loss)
        self.assertFalse(second.buffer_loss)

    def test_gui_marks_live_buffer_loss(self):
        from gui import VCOGui

        reference = Sample(1, 700000, 600000, source="dlc")
        messages = []
        gui = SimpleNamespace(recording_state="running", samples=[reference],
                              dlc_decoder=DLCDecoder(0, 0, 6, 0, 10, 700000),
                              _dlc_time=lambda cycle: cycle / 1000,
                              _analysis_changed=lambda: None,
                              _draw_timeline=lambda: None, _log=messages.append)
        self.assertTrue(VCOGui._handle_dlc_line(gui, "X,1000,64", 1))
        self.assertTrue(reference.buffer_loss)
        self.assertIsNone(gui.dlc_decoder)
        self.assertTrue(any("64 packets" in message for message in messages))

    def test_metadata_reports_selected_capacity(self):
        from gui import VCOGui

        gui = SimpleNamespace(recording_state="running", _current_code_valid=lambda *_: True,
                              _elapsed=lambda *_: 0.0, _update_uart_title=lambda: None,
                              _log=lambda *_: None, segment_id=0, pending_dlc_config=None)
        self.assertTrue(VCOGui._handle_dlc_line(gui,
                        "B,10000000,10000,8,6,8,10,700000,1200,30,100,1,10000,128", 1))
        self.assertEqual(gui.applied_dlc_buffer_events, 128)


class DLCInterpolationTests(unittest.TestCase):
    def test_reconstruction_modes_and_duplicate_events(self):
        times = [0, 1, 1, 2]
        values = [0, 1, 2, 0]
        grid = [.5, 1, 1.5]
        np.testing.assert_allclose(interpolate_signal(times, values, grid, "ZOH"), [0, 2, 2])
        np.testing.assert_allclose(interpolate_signal(times, values, grid, "Linear"), [1, 2, 1])
        spline = interpolate_signal(times, values, grid, "Spline")
        self.assertEqual(spline[1], 2)
        self.assertFalse(np.allclose(spline, interpolate_signal(times, values, grid, "Linear")))

    def test_quadratic_interpolation_uses_packet_knots(self):
        result = interpolate_signal([0, 1, 2], [0, 1, 0], [.5, 1.5], "Quadratic")
        np.testing.assert_allclose(result, [.75, .75])
        self.assertFalse(np.allclose(result, [.5, .5]))

    def test_narrow_width_preserves_selected_time_bits(self):
        from types import SimpleNamespace
        from gui import VCOGui

        class Value:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        width, time_bits = Value("1"), Value("6")
        gui = SimpleNamespace(dlc_width=width, dlc_time_bits=time_bits,
                              use_dlc=Value(True))
        self.assertEqual(VCOGui._requested_dlc_config(gui), (True, 0, 6))
        self.assertEqual(time_bits.get(), "6")
        width.set("128")
        self.assertEqual(VCOGui._requested_dlc_config(gui), (True, 7, 6))
        self.assertEqual(time_bits.get(), "6")

    def test_uart_legend_only_describes_selected_mode(self):
        from types import SimpleNamespace
        from gui import VCOGui

        class Title:
            text = ""

            def cget(self, _name):
                return self.text

            def configure(self, *, text):
                self.text = text

        title = Title()
        gui = SimpleNamespace(uart_title=title, loaded_recording=None,
                              recording_state="stopped",
                              differential_mode=SimpleNamespace(get=lambda: "GUI P−N"),
                              use_dlc=SimpleNamespace(get=lambda: False))
        VCOGui._update_uart_title(gui)
        self.assertIn("GUI P−N", title.text)
        self.assertIn("R:", title.text)
        self.assertNotIn("D:", title.text)
        self.assertNotIn("A:", title.text)
        gui.loaded_recording = "recording.csv"
        gui.samples = [Sample(0, 0, 0, source="dlc", differential_mode=0)]
        gui.times = [0]
        gui.cursor_time = 0
        VCOGui._update_uart_title(gui)
        self.assertIn("D:", title.text)
        self.assertNotIn("H:", title.text)
        self.assertIn("\nRaw comparison · R:", title.text)
        self.assertNotIn("B:", title.text)
        self.assertIn("Hardware P−N", title.text)

    def test_selected_reconstruction_changes_decomposition_input(self):
        times = np.array([0., 1., 2., 3., 4.])
        values = np.array([1., 1., 4., 1., 1.])
        arguments = (times, values, np.zeros(len(times)), ALGORITHMS[1], 10, .01,
                     np.full(len(times), 10.), ["dlc"] * len(times))
        zoh = analyze(*arguments, "ZOH")[0]
        linear = analyze(*arguments, "Linear")[0]
        spline = analyze(*arguments, "Spline")[0]
        self.assertFalse(np.allclose(zoh, linear))
        self.assertFalse(np.allclose(linear, spline))

    def test_reference_updates_do_not_anchor_dlc_trajectory(self):
        times = np.arange(5, dtype=float)
        values = np.array([100., 2., 200., 4., 300.])
        arguments = (times, values, np.zeros(5), ALGORITHMS[1], 10, .01,
                     np.full(5, 10.), ["dlc"] * 5, "Linear", [False, True, False, True, False])
        tonic, phasic, _ = analyze(*arguments)
        self.assertTrue(np.isnan(tonic[[0, 4]]).all())
        np.testing.assert_allclose(tonic[1:4] + phasic[1:4], [2., 3., 4.])
        changed = analyze(times, [0., 2., 0., 4., 0.], *arguments[2:])
        np.testing.assert_allclose(tonic[1:4], changed[0][1:4])

    def test_conductance_trace_uses_only_packet_samples(self):
        from gui import SignalPlot

        class PlotCapture:
            samples = [Sample(time_s, 700000, 600000, source="dlc", dlc_byte=byte)
                       for time_s, byte in ((0, None), (1, 1), (2, None), (3, 1), (4, None))]
            start_s = 0
            view_seconds = 4
            dlc_interpolation = "Linear"

            def __init__(self):
                self.lines = []

            def _average(self, index, _channel):
                return (1.0, 2.0, 100.0, 4.0, 5.0)[index]

            def create_line(self, *coordinates, **_kwargs):
                self.lines.append(coordinates)

        plot = PlotCapture()
        SignalPlot._draw_dlc_reconstruction(plot, plot.samples, 0, 0, 400, 100, 0, 5, "white")
        self.assertEqual(plot.lines[0][::2], (100.0, 300.0))

    def test_time_overflow_is_not_an_interpolation_knot(self):
        from gui import SignalPlot

        class PlotCapture:
            samples = [Sample(0, 700000, 600000, source="dlc", dlc_byte=1),
                       Sample(1, 700000, 600000, source="dlc", dlc_byte=32,
                              dlc_kind="time_overflow", dlc_level=1),
                       Sample(2, 700000, 600000, source="dlc", dlc_byte=1)]
            start_s = 0
            view_seconds = 2
            dlc_interpolation = "Linear"

            def __init__(self):
                self.lines = []

            def _average(self, index, _channel):
                return (1.0, 99.0, 3.0)[index]

            def create_line(self, *coordinates, **_kwargs):
                self.lines.append(coordinates)

        plot = PlotCapture()
        SignalPlot._draw_dlc_reconstruction(plot, plot.samples, 0, 0, 200, 100, 0, 5, "white")
        self.assertEqual(plot.lines[0], (0.0, 80.0, 200.0, 40.0))

        times = np.array([0., 1., 2., 3.])
        values = np.array([1., 99., 3., 5.])
        tonic, phasic, _ = analyze(times, values, np.zeros(4), ALGORITHMS[1], 1, .01,
                                   np.full(4, 10.), ["dlc"] * 4, "Linear",
                                   [True, False, True, True])
        self.assertAlmostEqual(tonic[1] + phasic[1], 2.0)

    def test_amplitude_continuation_replaces_previous_plot_knot(self):
        from gui import SignalPlot

        class PlotCapture:
            samples = [Sample(time_s, 700000, 600000, source="dlc", dlc_byte=byte)
                       for time_s, byte in ((0, 1), (1, 7), (1, 7), (1, 2), (2, 1))]
            start_s = 0
            view_seconds = 2
            dlc_interpolation = "Linear"

            def __init__(self):
                self.lines = []

            def _average(self, index, _channel):
                return (1.0, 2.0, 3.0, 4.0, 5.0)[index]

            def create_line(self, *coordinates, **_kwargs):
                self.lines.append(coordinates)

        plot = PlotCapture()
        SignalPlot._draw_dlc_reconstruction(plot, plot.samples, 0, 0, 200, 100, 0, 5, "white")
        self.assertEqual(plot.lines[0], (0.0, 80.0, 100.0, 20.0, 200.0, 0.0))

    def test_reference_frames_and_packets_do_not_duplicate_raw_markers(self):
        from gui import SignalPlot

        plot = SimpleNamespace(compare_dlc=True, offset_uv=0)
        reference = Sample(1, 700000, 600000, source="dlc", p_hz=700000, n_hz=600000)
        packet = Sample(1, 700000, 600000, source="dlc", p_hz=700000, n_hz=600000,
                        dlc_byte=7)
        self.assertEqual(SignalPlot._raw_value(plot, reference, "conductance_us"), 5.2)
        for channel in ("conductance_us", "p_uv", "n_uv"):
            self.assertIsNone(SignalPlot._raw_value(plot, packet, channel))
        references = [reference, Sample(1.01, 700000, 600000, source="dlc",
                                        p_hz=700000, n_hz=600000)]
        self.assertTrue(SignalPlot._redundant_dlc_reference(references, 0))
        self.assertFalse(SignalPlot._redundant_dlc_reference(references, 1))

    def test_heartbeat_does_not_duplicate_a_raw_reference_stream(self):
        from gui import VCOGui

        captured = []
        decoder = DLCDecoder(0, 4, 6, 0, 10, 600000, reference_p=True)
        gui = SimpleNamespace(recording_state="running", dlc_decoder=decoder,
                              last_raw_fields={"p_count": 1},
                              _dlc_time=lambda cycle: cycle / 10000000,
                              _record_reconstructed=lambda *args, **kwargs: captured.append(args),
                              _log=lambda *_args: None)
        self.assertTrue(VCOGui._handle_dlc_line(gui, "H,10000000,700000,600000,640,16", 1))
        self.assertEqual(captured, [])
        self.assertEqual(decoder.n_hz, 700000)
        gui.last_raw_fields = {}
        VCOGui._handle_dlc_line(gui, "H,20000000,700000,600000,640,16", 2)
        self.assertEqual(len(captured), 1)

    def test_level_grid_tracks_reference_and_recorded_width_boundary(self):
        from gui import SignalPlot

        class PlotCapture:
            start_s = 0
            view_seconds = 4

            def __init__(self):
                self.lines = []

            def _dlc_level_conductance(self, sample, level):
                return 10 - 2 * math.log1p(level) + sample.time_s / 5

            def create_line(self, *coordinates, **kwargs):
                self.lines.append((coordinates, kwargs))

        samples = [Sample(time_s, 700000, 600000, p_hz=700000, n_hz=600000,
                          source="dlc", dlc_level=10, dlc_log_width=log_width,
                          dlc_discard_bits=0, dlc_reference_p=True)
                   for time_s, log_width in ((0, 4), (1, 4), (2, 5), (3, 5))]
        samples.insert(2, Sample(1, 700000, 600000, p_hz=700000, n_hz=600000,
                                 source="dlc", dlc_level=11, dlc_log_width=4,
                                 dlc_discard_bits=0, dlc_reference_p=True))
        samples.append(Sample(4, 700000, 600000))
        plot = PlotCapture()
        SignalPlot._draw_dlc_level_grid(plot, samples, 0, 0, 400, 100, 0, 10)
        self.assertEqual({(line[0], line[-2]) for line, _ in plot.lines},
                         {(0.0, 200.0), (200.0, 400.0)})
        self.assertTrue(any(line[1] != line[3] for line, _ in plot.lines))
        self.assertTrue(all(kwargs["tags"] == "dlc_level_grid" for _, kwargs in plot.lines))
        self.assertTrue(all(first != second for line, _ in plot.lines
                            for first, second in zip(line[::2], line[2::2])))

    def test_counter_levels_are_nonuniform_in_conductance(self):
        from types import SimpleNamespace
        from gui import SignalPlot

        plot = SimpleNamespace(transfer_curve=[(0, 24), (500, 200), (1000, 600),
                                               (1500, 1051)], offset_uv=0)
        sample = Sample(0, 0, 0, p_hz=700000, n_hz=600000, source="dlc",
                        current_ua=.52, sample_hz=10, dlc_log_width=12,
                        dlc_discard_bits=0, dlc_reference_p=True)
        values = [SignalPlot._dlc_level_conductance(plot, sample, level)
                  for level in (100, 200, 300)]
        self.assertTrue(all(value is not None for value in values))
        self.assertGreater(abs((values[0] - values[1]) - (values[1] - values[2])), .1)

    def test_time_overflow_and_stacked_level_overflows_use_axis(self):
        from gui import SignalPlot

        class PlotCapture:
            start_s = 0
            view_seconds = 2

            def __init__(self):
                self.lines = []
                self.line_options = []
                self.circles = []

            def create_line(self, *coordinates, **kwargs):
                self.lines.append(coordinates)
                self.line_options.append(kwargs)

            def create_oval(self, *coordinates, **_kwargs):
                self.circles.append(coordinates)

        packets = [Sample(1, 0, 0, source="dlc", dlc_byte=0,
                          dlc_kind="time_overflow")]
        plot = PlotCapture()
        SignalPlot._draw_dlc_level_arrows(plot, packets, 0, 0, 200, 100)
        self.assertEqual(len(plot.circles), 1)
        self.assertEqual(len(plot.lines), 0)

        packets = [Sample(1, 0, 0, source="dlc", dlc_byte=7,
                          dlc_kind="amplitude_up", dlc_level_change=7)
                   for _ in range(2)]
        packets.append(Sample(1, 0, 0, source="dlc", dlc_byte=1,
                              dlc_level_change=1))
        plot = PlotCapture()
        SignalPlot._draw_dlc_level_arrows(plot, packets, 0, 0, 200, 100)
        self.assertEqual(len(plot.lines), 3)
        self.assertEqual(len({line[1] for line in plot.lines}), 3)
        self.assertEqual({line[0] for line in plot.lines}, {100.0})
        self.assertEqual(plot.line_options[0]["arrowshape"], (10, 8, 4))
        self.assertTrue(all(options["width"] == 2 for options in plot.line_options))
        self.assertEqual(len(plot.circles), 0)
        self.assertTrue(all(line[1] < line[3] for line in plot.lines))

        plot = PlotCapture()
        packets = [Sample(1, 0, 0, source="dlc", dlc_byte=7,
                          dlc_kind="amplitude_up", dlc_level_change=7)
                   for _ in range(16)]
        SignalPlot._draw_dlc_level_arrows(plot, packets, 0, 0, 200, 100)
        self.assertEqual(len(plot.lines), 16)
        self.assertEqual(len({line[1] for line in plot.lines}), 16)
        self.assertTrue(all(0 <= coordinate <= 100 for line in plot.lines
                            for coordinate in (line[1], line[3])))


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
    def test_session_uart_sidecar_restores_buffer_loss_after_csv_flush(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.csv"
            path.write_text(
                "time_s,vco_p_uV,vco_n_uV,source,dlc_level,buffer_loss\n"
                "0,730000,630000,dlc,10,0\n"
                "1,730000,630000,dlc,10,0\n")
            recording_uart_path(path).write_text(
                "A,1\nB,1\nR,0\nX,100,16\nA,1\nB,1\nR,1\n")
            samples = read_recording(path)
            self.assertEqual([sample.buffer_loss for sample in samples], [True, False])
            self.assertFalse(samples[0].analysis_valid)

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

    def test_idac_code_mismatch_invalidates_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "code.csv"
            path.write_text("time_s,vco_p_uV,vco_n_uV,injected_current_uA,idac_code\n0,730000,630000,0.2,4\n")
            sample = read_recording(path)[0]
            self.assertFalse(sample.valid)
            self.assertEqual(sample.current_code, 4)

    def test_dlc_quantizer_metadata_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dlc.csv"
            path.write_text(
                "time_s,vco_p_uV,vco_n_uV,source,dlc_level,dlc_level_change,dlc_log_width,dlc_discard_bits,dlc_reference_p\n"
                "0,730000,630000,dlc,21,-3,4,2,1\n")
            sample = read_recording(path)[0]
            self.assertEqual((sample.dlc_level, sample.dlc_level_change, sample.dlc_log_width,
                              sample.dlc_discard_bits, sample.dlc_reference_p), (21, -3, 4, 2, True))

    def test_legacy_uart_recovers_width_changes_and_same_time_packets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy_dlc.csv"
            path.write_text(
                "time_s,vco_p_uV,vco_n_uV,vco_p_Hz,vco_n_Hz,source,stored_bytes,dlc_byte\n"
                "0,700000,600000,700000,600000,dlc,0,\n"
                "1,700000,600000,700000,600000,dlc,1,33\n"
                "1,700000,600000,700000,600000,dlc,1,1\n"
                "2,700000,600000,700000,600000,dlc,0,\n")
            recording_uart_path(path).write_text(
                "B,10000000,10000,4,3,0,10,700000,640,16,12345,1,10000\n"
                "R,0,12345,640,16,700000,600000,0,0,0,0,0,10,1\n"
                "D,0,22345,640,16,700000,600000,2101\n"
                "B,10000000,10000,5,3,0,20,700000,640,16,32345,1,10000\n"
                "R,1,32345,640,16,700000,600000,0,0,0,0,0,10,1\n")
            samples = read_recording(path)
            self.assertEqual([sample.dlc_level for sample in samples], [10, 11, 12, 20])
            self.assertEqual([sample.dlc_level_change for sample in samples], [0, 1, 1, 0])
            self.assertEqual([sample.dlc_log_width for sample in samples], [4, 4, 4, 5])
            self.assertEqual([sample.time_s for sample in samples], [0, 1, 1, 2])

    def test_malformed_csv_rejected(self):
        for text in ("bad,columns\n1,2\n", "time_s,vco_p_uV,vco_n_uV,injected_current_uA\n0,nan,600000,0.2\n",
                     "time_s,vco_p_uV,vco_n_uV,injected_current_uA\n1,700000,600000,0.2\n0,700000,600000,0.2\n"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.csv"
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_recording(path)

    def test_configuration_roundtrip_and_validation(self):
        values = {"current_code": 13, "motion_enabled": True, "compare_dlc": True,
                  "filter_window": 50, "voltage_offset_mv": 0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "configuration.json"
            write_configuration(path, values)
            self.assertEqual(validate_configuration(read_configuration(path), {}, 46), values)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())
        for invalid in ({"current_code": 0}, {"motion_window": "nan"}, {"motion_enabled": "false"}, {"dlc_width": 3}, {"version": 2}):
            with self.assertRaises(ValueError):
                validate_configuration(invalid, {}, 46)

    def test_recording_configuration_sidecar_name(self):
        self.assertEqual(recording_configuration_path("trial.csv").name,
                         "trial.csv.configuration.json")
        self.assertEqual(recording_uart_path("trial.csv").name,
                         "trial.csv.uart.log")


if __name__ == "__main__":
    unittest.main()
