"""Tk GUI lifecycle with a scripted worker; skipped without a display."""

import queue
import sys
import tempfile
import tkinter as tk
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biopac.acquire import LiveBlock
from biopac.gui import BiopacGUI, main
from biopac.configuration import Configuration, write_configuration
from biopac.profiles import get_profile
from biopac.recording import Segment, Settings, load_recording

try:
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
except tk.TclError as error:  # headless CI or a missing python3-tk
    raise unittest.SkipTest(f"Tk display unavailable: {error}") from error


class FakeWorker:
    """Records commands; tests deliver worker events directly."""

    def __init__(self):
        self.commands = []
        self.events = queue.Queue()

    def submit(self, command, **values):
        self.commands.append((command, values))

    def start(self):
        pass

    def join(self, timeout=None):
        pass


class GUITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.worker = FakeWorker()
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.gui = BiopacGUI(self.root, replace(Configuration(), output_dir=str(self.directory)),
                             config_path=self.directory / "config.json", demo=True,
                             worker=self.worker)
        self.addCleanup(self._drain_jobs)

    def _drain_jobs(self):
        for job in self.root.tk.call("after", "info"):
            self.root.after_cancel(job)
        self.gui.executor.shutdown(wait=True)
        self.root.update_idletasks()

    def deliver(self, kind, payload=None):
        if self.gui.pump_job is not None:
            self.root.after_cancel(self.gui.pump_job)
        self.worker.events.put((kind, payload))
        self.gui._pump()

    def block(self, first, *values):
        import struct
        return LiveBlock(first, struct.pack(f">{len(values)}h", *values))

    def test_controls_match_the_acquisition_state(self):
        for state, enabled in (("connecting", False), ("connected", True), ("recording", False)):
            with self.subTest(state=state):
                self.deliver("state", state)
                self.assertEqual(self.gui.start_button.instate(["disabled"]), not enabled)
        self.deliver("state", "recording")
        self.assertFalse(self.gui.pause_button.instate(["disabled"]))
        self.assertIn("provisional", self.gui.status.get())
        self.deliver("state", "paused")
        self.assertEqual(self.gui.start_button.cget("text"), "Resume recording")

    def test_pending_and_applied_settings_are_shown_separately(self):
        self.assertIn("Not applied", self.gui.settings_status.get())
        self.deliver("applied", Settings(rate=100))
        self.assertIn("Applied", self.gui.settings_status.get())
        self.gui.hardware_variables["rate"].set("200")
        self.assertIn("PENDING", self.gui.settings_status.get())
        self.gui.hardware_variables["rate"].set("100")
        self.assertIn("Applied", self.gui.settings_status.get())
        self.assertNotIn("PENDING", self.gui.settings_status.get())

    def select_profile(self, key):
        self.gui.profile_var.set(get_profile(key).name)
        self.gui._profile_selected()

    def test_profile_selection_loads_defaults_without_touching_device_or_history(self):
        self.deliver("segment", Segment(0, 0, Settings()))
        self.deliver("samples", (0, self.block(0, 6554)))
        self.deliver("applied", Settings())
        self.deliver("state", "recording")
        original = self.gui.recording.samples[0].conductance
        self.select_profile("bionomadix-ppged-r")
        self.assertEqual(self.gui._settings(), Settings.from_profile("bionomadix-ppged-r"))
        self.assertEqual(self.gui.recording.samples[0].conductance, original)
        self.assertEqual(self.gui.applied, Settings())
        self.assertIn("PENDING", self.gui.settings_status.get())
        self.assertIn("pairing", self.gui.profile_hint.get())
        self.assertEqual(self.gui.filter_labels[0].cget("text"), "Bandwidth")
        self.assertEqual(self.worker.commands, [])
        self.select_profile("eda100c")
        self.assertEqual(self.gui._settings(), Settings())
        self.assertEqual(self.gui.filter_labels[0].cget("text"), "Filter 1")

    def test_bionomadix_start_and_apply_forward_channel9_and_calibration(self):
        self.select_profile("bionomadix-ppged-r")
        self.deliver("state", "connected")
        self.gui.start()
        self.assertEqual(self.worker.commands[-1], ("start", {"settings": Settings.from_profile("bionomadix-ppged-r")}))
        self.deliver("state", "recording")
        self.gui.hardware_variables["zero_voltage"].set("0.01")
        with patch("biopac.gui.messagebox.askokcancel", return_value=True):
            self.gui.apply_settings()
        self.assertEqual(self.worker.commands[-1], ("apply", {"settings": replace(Settings.from_profile("bionomadix-ppged-r"), zero_voltage=0.01)}))

    def test_saved_profile_overrides_survive_gui_config_load(self):
        self.select_profile("bionomadix-ppged-r")
        self.gui.hardware_variables["sensitivity"].set("4.95")
        self.gui.hardware_variables["channel"].set("10")
        self.gui.hardware_variables["zero_voltage"].set("0.02")
        path = self.directory / "custom.json"
        self.gui.save_config(str(path))
        settings = self.gui._settings()
        self.select_profile("eda100c")
        self.gui.load_config(str(path))
        self.assertEqual(self.gui._settings(), settings)
        self.assertEqual(self.gui.profile_var.get(), get_profile("bionomadix-ppged-r").name)

    def test_gui_profile_argument_overrides_saved_eda100c_settings(self):
        path = self.directory / "last.json"
        write_configuration(path, replace(Configuration(), acquisition=replace(Settings(), zero_voltage=0.2)))
        # Verify the real command-line/config wiring without starting another Tk
        # interpreter, altering process signal handlers or contacting hardware.
        with patch("biopac.gui.tk.Tk", return_value=Mock()), patch("biopac.gui.BiopacGUI") as factory, patch("biopac.gui.signal.signal"):
            self.assertEqual(main(["--config", str(path), "--profile", "bionomadix-ppged-r", "--demo"]), 0)
        self.assertEqual(factory.call_args.args[1].acquisition, Settings.from_profile("bionomadix-ppged-r"))

    def test_invalid_pending_settings_do_not_raise_or_start(self):
        self.gui.hardware_variables["sensitivity"].set("-2")
        self.gui.start()
        self.gui.apply_settings()
        self.assertIn("invalid", self.gui.settings_status.get())
        self.assertEqual(self.worker.commands, [])

    def test_samples_render_readout_and_history(self):
        self.deliver("state", "recording")
        self.deliver("segment", Segment(0, 0, Settings()))
        self.deliver("samples", (0, self.block(0, 100, 200, 300)))
        self.gui._render()
        self.assertIn("0.091554", self.gui.readout.get())
        self.assertIn("3 provisional", self.gui.history_status.get())
        self.assertEqual(len(self.gui.recording.samples), 3)

    def test_finished_samples_are_reported_as_verified(self):
        self.deliver("state", "recording")
        self.deliver("segment", Segment(0, 0, Settings()))
        self.deliver("samples", (0, self.block(0, 100)))
        self.deliver("finished", (0, None))
        self.gui._render()
        self.assertIn("1 verified", self.gui.history_status.get())

    def test_failed_segments_disable_export_until_stopped(self):
        self.deliver("state", "recording")
        self.deliver("segment", Segment(0, 0, Settings()))
        self.deliver("samples", (0, self.block(0, 100)))
        self.deliver("finished", (0, "mismatch"))
        self.gui._render()
        self.assertTrue(self.gui.export_button.instate(["disabled"]))
        self.deliver("state", "paused")
        self.gui._render()
        self.assertFalse(self.gui.export_button.instate(["disabled"]))

    def test_commands_forward_settings_and_configuration(self):
        self.gui.interface.set("enx")
        self.gui.connect()
        self.assertEqual(self.worker.commands[-1][0], "connect")
        self.deliver("state", "connected")  # The worker reply re-enables commands.
        self.gui.start()
        command, values = self.worker.commands[-1]
        self.assertEqual(command, "start")
        self.assertEqual(values["settings"].rate, 100)
        self.deliver("state", "recording")
        self.gui.pause_button.invoke()
        self.assertEqual(self.worker.commands[-1][0], "pause")
        self.deliver("state", "paused")
        self.gui.apply_settings()
        self.assertEqual(self.worker.commands[-1][0], "apply")
        self.assertEqual(self.worker.commands[-1][1]["settings"].sensitivity, 2)

    def test_pause_needs_no_arguments_and_close_flushes_configuration(self):
        self.deliver("state", "recording")
        self.gui.close()
        self.assertTrue(self.gui.closing)
        self.assertEqual(self.worker.commands[-1][0], "close")
        saved = (self.directory / "config.json").read_text()
        self.assertIn('"schema_version": 1', saved)

    def test_close_during_pending_is_ignored_until_closed(self):
        self.gui.close()
        self.gui.close()
        self.assertEqual([command for command, _ in self.worker.commands].count("close"), 1)

    def test_load_and_export_roundtrip_through_the_gui(self):
        source = self.directory / "session.csv"
        from biopac.recording import SessionWriter
        writer = SessionWriter(self.directory, {"serial": "t"}, simulated=True)
        segment = Segment(0, 0, Settings())
        writer.begin(segment)
        writer.append(segment, self.block(0, 100, 200))
        writer.finish(segment)
        writer.close()
        self.gui.load_csv(str(writer.path))
        self.assertEqual(len(self.gui.recording.samples), 2)
        export = self.directory / "export.csv"
        self.gui.export_csv(str(export))
        loaded, metadata = load_recording(export)
        self.assertEqual([s.adc_count for s in loaded.samples], [100, 200])
        self.assertTrue(metadata["simulated"])

    def test_load_failure_is_reported_without_touching_the_history(self):
        bad = self.directory / "bad.csv"
        bad.write_text("time_s,adc_count\n0,2\nnotanumber,3\n")
        self.gui.load_csv(str(bad))
        self.assertIn("Load failed", self.gui.notice.get())
        self.assertEqual(self.gui.recording.samples, [])

    def test_configuration_roundtrip_and_defaults(self):
        self.gui.interface.set("enx")
        self.gui.hardware_variables["rate"].set("500")
        configuration = self.gui._configuration()
        self.assertEqual(configuration.interface, "enx")
        self.assertEqual(configuration.acquisition.rate, 500)
        self.gui._set_configuration(replace(configuration, interface="eth0"))
        self.assertEqual(self.gui.interface.get(), "eth0")
        self.assertEqual(self.gui.hardware_variables["rate"].get(), "500")

    def test_processing_changes_are_debounced_and_validate(self):
        self.gui.processing_variables["average"].set("50")
        self.gui.root.update()
        self.gui._apply_processing()
        self.assertEqual(self.gui.processing.average, 50)
        self.gui.processing_variables["average"].set("0")
        self.gui._apply_processing()
        self.assertIn("not applied", self.gui.notice.get())
        self.assertEqual(self.gui.processing.average, 50)

    def test_demo_mode_is_labelled_everywhere(self):
        self.assertIn("SIMULATED", self.root.title())
        self.assertIn("SIMULATED DATA ONLY", self.gui.log.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
