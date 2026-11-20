"""Display stalls must not stall capture, discard samples, or stop future updates."""

import csv
from dataclasses import replace
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui import app
from gui.common.background import compact_series
from gui.sdk.biopac.configuration import Configuration
from gui.sdk.biopac.controller import AcquisitionWorker
from gui.sdk.biopac.panel import BiopacGUI
from gui.sdk.biopac.recording import load_recording
from gui.sdk.heepidermis import panel as he


class SerialPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.patch = patch.object(he, "OUT_DIR", self.directory)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.panel = SimpleNamespace(
            _configuration_snapshot=lambda: dict(motion_enabled=False, motion_window=1,
                                                 motion_threshold_mv=5, motion_correlation=0.9),
            events=queue.Queue(), transfer_curve=he.load_transfer_curve(), first_sample_clock=10,
            offset_uv=0, applied_mode=0, applied_dlc=False, applied_sampling_hz=10,
            applied_supply_hz=10, applied_current_ua=.52, applied_dlc_buffer_events=16,
            dlc_clock_hz=10000000)
        self.capture = he.SerialCapture(self.panel)
        self.capture.open()
        self.addCleanup(self.capture.close)

    def rows(self, path=None):
        with (path or self.capture.path).open() as stream:
            return list(csv.DictReader(stream))

    def test_uart_is_flushed_without_any_tk_event_processing(self):
        self.capture.feed("A,0,10000000,10000,10000,520,13,0,0", 10)
        self.capture.feed("R,0,1000000,520,13,700000,600000,70,60,0,0,620000,10,1", 10.1)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["idac_code"], "13")
        self.assertEqual(self.capture.path.name, "last_output.csv")
        self.assertTrue(he.recording_uart_path(self.capture.path).read_text().endswith(
            "R,0,1000000,520,13,700000,600000,70,60,0,0,620000,10,1\n"))
        # dLC metadata and a crossing use the same decoder as the display.
        self.capture.feed("B,10000000,10000,0,6,0,0,700000,520,13,1000000,0,10000,16", 10.1)
        self.capture.feed("D,0,2000000,520,13,700000,600000,05", 10.2)
        self.assertEqual(self.rows()[-1]["source"], "dlc")

    def test_rename_keeps_open_writer_and_new_recording_replaces_only_last_output(self):
        self.capture.feed("0,700000,600000,520", 10.1)
        last = self.capture.path
        inode = last.stat().st_ino
        saved = self.directory / "saved.csv"
        self.capture.rename(saved)
        self.capture.feed("1,700000,600000,520", 10.2)
        self.assertEqual(saved.stat().st_ino, inode)
        self.assertFalse(last.exists())
        self.assertEqual(len(self.rows(saved)), 2)
        self.assertTrue(he.recording_configuration_path(saved).exists())
        self.assertTrue(he.recording_uart_path(saved).exists())
        self.capture.close()
        for count in (3, 1):
            capture = he.SerialCapture(self.panel)
            try:
                capture.open()
                for i in range(count):
                    capture.feed(f"{i},700000,600000,520", 10.1 + i / 10)
                self.assertEqual(len(self.rows(last)), count)
            finally:
                capture.close()
        self.assertEqual(len(self.rows(saved)), 2)

    def test_failed_rename_preserves_active_recording(self):
        target = self.directory / "existing.csv"
        target.write_text("keep me")
        with self.assertRaises(FileExistsError):
            self.capture.rename(target)
        self.capture.feed("0,700000,600000,520", 10.1)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(target.read_text(), "keep me")


class DisplayTests(unittest.TestCase):
    def test_he_windowed_snapshot_computes_tonic_without_tk(self):
        from itertools import accumulate

        samples = [he.Sample(i / 10, 70000, 50000) for i in range(2000)]
        variable = lambda value: SimpleNamespace(get=lambda: value)
        panel = SimpleNamespace(samples=samples, times=[s.time_s for s in samples],
            prefix_p=[0] + list(accumulate(s.p_uv for s in samples)),
            prefix_n=[0] + list(accumulate(s.n_uv for s in samples)),
            prefix_delta=[0] + list(accumulate(s.delta_uv for s in samples)),
            prefix_current=[0] + list(accumulate(s.current_ua for s in samples)),
            offset_uv=0, filter_size=10, SERIES_NAMES=he.VCOGui.SERIES_NAMES,
            algorithm=variable(he.ALGORITHMS[1]), smoothing=variable(1.),
            peak_prominence=variable(.01), dlc_interpolation=variable("ZOH"), analysis_generation=0)
        snapshot = he.VCOGui.plot_snapshot(panel, {"tonic_uS"})
        series = snapshot.get_series(25, 50)
        self.assertLess(len(series["tonic_uS"]["times"]), 300)
        self.assertEqual(snapshot.latest, 199.9)
        for raw, tonic in zip(series["conductance_uS"]["values"], series["tonic_uS"]["values"]):
            self.assertAlmostEqual(raw, 26.)
            self.assertAlmostEqual(tonic, 26.)

    def test_large_window_frame_is_computed_without_tk_and_reduced(self):
        from gui.common.plots import TimeViewport

        times = [i / 100 for i in range(180000)]
        data = dict(times=times, values=[float(i % 1000) for i in range(len(times))], segments=None)
        renderer = app.Workspace.__new__(app.Workspace)
        renderer.viewport = TimeViewport(span=1800)
        renderer.signal_rows = [app.SignalRow(device="biopac", signal="conductance_uS", color="#ff0000")]
        renderer.panels = {"biopac": SimpleNamespace(
            get_series=lambda *args: {"conductance_uS": data}, invalid_spans=lambda *args: [],
            signals=lambda: {"conductance_uS": dict(unit="µS", label="Skin conductance")})}
        renderer.feature_cache, renderer.error_seen = {}, set()
        built, invalid = renderer._compute_frame()
        self.assertEqual(len(built), 1)
        self.assertLessEqual(len(built[0]["times"]), 2400)
        self.assertEqual(max(built[0]["values"]), 999.)
        self.assertEqual(invalid, [])

    def test_display_reduction_keeps_extrema_and_breaks(self):
        values = [0.] * 20000
        values[1234], values[15678] = 80., -30.
        reduced = compact_series(dict(times=list(range(20000)), values=values,
                                      segments=[0] * 10001 + [1] * 9999))
        self.assertLessEqual(len(reduced["times"]), 2400)
        self.assertIn(80., reduced["values"])
        self.assertIn(-30., reduced["values"])
        self.assertTrue(any(value != value for value in reduced["values"]))

    def test_slow_and_failed_render_does_not_block_tk_or_csv(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        errors, beats = [], []
        root.report_callback_exception = lambda *args: errors.append(args)
        gate, entered = threading.Event(), threading.Event()
        worker = AcquisitionWorker(demo=True, poll_interval=.01)
        workspace = None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            configuration = replace(Configuration(), output_dir=directory)
            factory = lambda root, **kwargs: BiopacGUI(root, configuration,
                config_path=path / "biopac.json", worker=worker, demo=True, **kwargs)
            devices = tuple({**device, "panel": factory} if device["key"] == "biopac" else device
                            for device in app.DEVICES)
            try:
                with patch.object(app, "WORKSPACE_CONFIG", path / "workspace.json"), patch.object(app, "DEVICES", devices):
                    workspace = app.Workspace(root)
                    root.geometry("1600x900+0+0")
                    root.deiconify()
                    workspace.device_frames["biopac"]["variable"].set(True)
                    panel = workspace.panels["biopac"]
                    panel.connect()

                    def pump_until(predicate, timeout=5):
                        end = time.monotonic() + timeout
                        while not predicate() and time.monotonic() < end:
                            root.update()
                            time.sleep(.003)
                        self.assertTrue(predicate(), f"event-loop condition timed out: {panel.state}, "
                                        f"pending={panel.pending}, {panel.notice.get()}")

                    pump_until(lambda: panel.state == "connected")
                    workspace.add_signal_row("biopac", "conductance_uS")
                    workspace._transport("start")
                    pump_until(lambda: len(panel.recording.samples) >= 10)
                    workspace.add_signal_row("features", "Phasic peak detection",
                                             source_a="biopac:conductance_uS")

                    def heartbeat():
                        beats.append(time.monotonic())
                        if not workspace.closing:
                            root.after(10, heartbeat)

                    def slow_peaks(*args, **kwargs):
                        entered.set()
                        gate.wait(5)
                        return [], []

                    heartbeat()
                    with patch.object(app.features, "detect_peaks", slow_peaks):
                        pump_until(entered.is_set)
                        before = len(load_recording(path / "last_output.csv")[0].samples)
                        # The display job is blocked; repeated zoom and transport
                        # still run, and samples reach disk without GUI exports.
                        for _ in range(4):
                            workspace._zoom(2)
                            start = time.monotonic()
                            pump_until(lambda: time.monotonic() - start > .1)
                        self.assertIn("Loading", workspace.plot_status.get())
                        self.assertGreater(len(load_recording(path / "last_output.csv")[0].samples), before)
                        workspace._transport("pause")
                        pump_until(lambda: panel.state == "paused")
                        self.assertGreater(len(beats), 20)
                        self.assertLess(max(b - a for a, b in zip(beats, beats[1:])), .25)
                        saved = path / "named.csv"
                        inode = (path / "last_output.csv").stat().st_ino
                        panel.export_csv(saved)
                        pump_until(lambda: panel.session_path == saved)
                        self.assertEqual(saved.stat().st_ino, inode)
                        count = len(load_recording(saved)[0].samples)
                        workspace._transport("start")
                        pump_until(lambda: panel.state == "recording" and len(load_recording(saved)[0].samples) > count)
                        workspace._transport("stop")
                        pump_until(lambda: panel.state == "paused")
                        workspace._transport("start")
                        pump_until(lambda: panel.session_path == path / "last_output.csv" and len(panel.recording.samples) > 0)
                        self.assertTrue(saved.exists())
                        gate.set()
                        pump_until(lambda: workspace.plot_status.get() == "Visualization up to date")
                    # An exception must become a visible error, then recover.
                    with patch.object(app.Workspace, "_compute_frame", side_effect=ValueError("injected display failure")):
                        workspace._zoom(.5)
                        pump_until(lambda: "injected display failure" in workspace.plot_status.get())
                    workspace._zoom(.5)
                    pump_until(lambda: workspace.plot_status.get() == "Visualization up to date")
                    self.assertEqual(errors, [])
                    workspace.close()
                    pump_until(lambda: panel.closed)
            finally:
                gate.set()
                if workspace is not None and not workspace.closing:
                    workspace.close()
                worker.submit("close")
                worker.join(timeout=5)
                root.destroy()


if __name__ == "__main__":
    unittest.main()
