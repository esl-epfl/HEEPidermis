"""Worker threading and lifecycle, with a scripted fake client and no hardware."""

from dataclasses import replace
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gui.sdk.biopac.acquire import AcquisitionError, LiveBlock
from gui.sdk.biopac.controller import AcquisitionWorker
from gui.sdk.biopac.recording import SessionWriter, Settings, load_recording


class FakeClient:
    """Same live API and reply cadence as MP160, including stop behaviour."""

    def __init__(self):
        self.capacity = 40000
        self.calls = []
        self.rate, self.sent, self.finished = 0, 0, 0
        self.start_error = self.read_error = self.finish_error = None
        self.information = lambda: {"firmware": "2.1.06", "buffer_capacity_bytes": self.capacity, "status": 0x80}

    def start_live(self, channel, rate, capacity):
        if self.start_error:
            raise self.start_error
        self.channel, self.rate, self.sent = channel, rate, 0
        self.live_started_monotonic = 0.0

    def read_live(self):
        if self.read_error:
            raise self.read_error
        count = 5
        block = LiveBlock(self.sent, struct.pack(f">{count}h", *range(self.sent, self.sent + count)))
        self.sent += block.count
        return block

    def finish_live(self):
        self.finished += 1
        if self.finish_error:
            raise self.finish_error
        return LiveBlock(self.sent, b"")

    def close(self):
        self.calls.append("close")


def slow_discovery(*_args):
    raise AcquisitionError("no network")


class SessionWriterSpy(SessionWriter):
    def __init__(self, directory, device, simulated=False):
        super().__init__(directory, device, simulated)
        self.closed = False

    def close(self):
        self.closed = True
        super().close()


class WorkerHarness(unittest.TestCase):
    """Synchronous worker: each drain runs exactly one queued command or poll."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.client = FakeClient()
        self.worker = AcquisitionWorker(demo=False, client_factory=lambda *args: self.client,
                                        poll_interval=0.001)
        self.worker.output_dir = self.directory
        self.now = 0.0
        self.worker.clock = lambda: self.now
        self.worker.discovery = lambda interface, ip: [
            {"ip": ip or "169.254.164.181", "mac": "00:90:3d:00:24:b5", "serial": "2107A-00024B5",
             "interface": interface or "enx"}]
        self.emitted, self.spies = [], []
        original_emit = self.worker.emit

        def emit(kind, payload=None):
            self.emitted.append((kind, payload))
            original_emit(kind, payload)

        def writer_factory(directory, device, simulated=False):
            spy = SessionWriterSpy(directory, device, simulated)
            self.spies.append(spy)
            return spy

        self.worker.emit = emit
        self.worker.writer_factory = writer_factory
        self.addCleanup(self.worker._close_writer)

    def drain(self, runs=1):
        for _ in range(runs):
            self.worker.run_once()

    def connect(self):
        self.worker.submit("connect", interface="enx", ip="169.254.164.181")
        self.drain()

    def start(self, settings=None):
        self.worker.submit("start", settings=settings or Settings())
        self.drain()

    def events(self, kind):
        return [payload for name, payload in self.emitted if name == kind]


class LifecycleTests(WorkerHarness):

    def test_connect_emits_device_and_connected_state(self):
        self.connect()
        self.assertEqual(self.events("state"), ["connecting", "connected"])
        self.assertEqual(self.events("device")[0]["serial"], "2107A-00024B5")
        self.assertEqual(self.client.calls, [])  # socket stays open until Disconnect

    def test_newly_exercised_firmware_is_accepted(self):
        self.client.information = lambda: {"firmware": "2.0.00", "buffer_capacity_bytes": 40000, "status": 0x80}
        self.connect()
        self.assertEqual(self.events("state")[-1], "connected")
        self.assertEqual(self.events("error"), [])

    def test_profile_change_restarts_channel_and_keeps_previous_metadata(self):
        self.connect()
        self.start(Settings.from_profile("eda100c"))
        self.drain()
        self.assertEqual(self.client.channel, 3)
        self.worker.submit("apply", settings=Settings.from_profile("bionomadix-ppged-r"))
        self.drain()
        self.assertEqual((self.client.channel, self.client.rate), (9, 100))
        self.assertEqual(self.client.finished, 1)
        self.drain()
        self.worker.submit("pause")
        self.drain()
        history, _ = load_recording(self.events("session")[0])
        self.assertEqual(history.segments[0].settings, Settings.from_profile("eda100c"))
        self.assertEqual(history.segments[1].settings, Settings.from_profile("bionomadix-ppged-r"))
        self.assertTrue(all(s.verified for s in history.segments.values()))

    def test_firmware_and_busy_status_are_not_bypassed(self):
        self.client.information = lambda: {"firmware": "9.9", "buffer_capacity_bytes": 100, "status": 0x80}
        self.worker.submit("connect", interface="enx", ip="")
        self.drain()
        self.assertIn("not validated", self.events("error")[-1])
        self.client.information = lambda: {"firmware": "2.1.06", "buffer_capacity_bytes": 100, "status": 0x81}
        self.worker.submit("connect", interface="enx", ip="")
        self.drain()
        self.assertIn("already recording", self.events("error")[-1])
        self.assertEqual(self.events("state")[-1], "disconnected")

    def test_discovery_failure_never_touches_the_socket(self):
        self.worker.discovery = slow_discovery
        self.worker.submit("connect", interface="enx", ip="")
        self.drain()
        self.assertIn("no network", self.events("error")[-1])
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.events("state")[-1], "disconnected")

    def test_blank_interface_scans_every_interface(self):
        calls = []
        self.worker.discovery = lambda interface, ip: (calls.append((interface, ip)) or
                                                       [{"ip": "169.254.164.181", "mac": "m",
                                                         "serial": "s", "interface": "enx7"}])
        self.worker.submit("connect", interface="", ip="")
        self.drain()
        self.assertEqual(calls, [("", None)])
        self.assertEqual(self.events("state")[-1], "connected")
        self.assertIn("enx7", self.events("device")[0]["interface"])

    def test_empty_selected_interface_falls_back_to_a_full_scan(self):
        calls = []

        def discovery(interface, ip):
            calls.append(interface)
            return ([{"ip": "169.254.164.181", "mac": "m", "serial": "s", "interface": "eth9"}]
                    if interface == "" else [])

        self.worker.discovery = discovery
        self.worker.submit("connect", interface="enx", ip="")
        self.drain()
        self.assertEqual(calls, ["enx", ""])
        self.assertEqual(self.events("state")[-1], "connected")

    def test_silent_scan_reports_no_answer(self):
        self.worker.discovery = lambda interface, ip: []
        self.worker.submit("connect", interface="", ip="")
        self.drain()
        self.assertIn("No MP160 answered", self.events("error")[-1])
        self.assertEqual(self.events("state")[-1], "disconnected")

    def test_several_units_are_reported_for_disambiguation(self):
        self.worker.discovery = lambda interface, ip: [
            {"ip": f"169.254.164.{index}", "mac": f"m{index}", "serial": f"s{index}",
             "interface": "enx"} for index in (1, 2)]
        self.worker.submit("connect", interface="", ip="")
        self.drain()
        self.assertIn("Found 2 MP160 units", self.events("error")[-1])

    def test_start_pause_and_disconnect_verify_in_order(self):
        self.connect()
        self.start()
        self.assertEqual(self.events("state"), ["connecting", "connected", "recording"])
        self.assertEqual(self.events("session"), [str(self.worker.writer.path)])
        self.drain()  # First poll delivers the streamed prefix.
        self.assertEqual([block.first_sample for _, block in self.events("samples")], [0])
        self.worker.submit("pause")
        self.drain()
        self.assertEqual(self.events("state")[-1], "paused")
        self.drain()
        self.assertEqual(self.client.finished, 1)
        self.assertFalse(self.spies[0].closed)  # The session stays open for resume.
        self.worker.submit("disconnect")
        self.drain()
        self.assertTrue(self.spies[0].closed)
        self.assertEqual(self.client.calls, ["close"])
        history, metadata = load_recording(self.events("session")[0])
        self.assertEqual(len(history.samples), 5)
        self.assertTrue(history.segments[0].verified)
        self.assertFalse(metadata["simulated"])

    def test_apply_during_recording_restarts_with_a_gap_and_keeps_calibration(self):
        self.connect()
        self.start()
        self.now = 3.0
        self.drain()
        self.worker.submit("apply", settings=replace(Settings(), rate=500, sensitivity=5,
                                                     zero_voltage=0.1, filters=("1 Hz", "Low", "High")))
        self.drain()
        self.assertEqual([segment.reason for segment in self.events("segment")], ["record", "settings changed"])
        self.drain()  # Poll the restarted segment so it also holds samples.
        first, second = self.events("segment")
        self.assertEqual(second.number, 1)
        self.assertGreaterEqual(second.start_s, first.start_s + first.samples / first.settings.rate)
        applied = self.events("applied")
        self.assertEqual(applied[0].rate, 100)
        self.assertEqual(applied[-1].sensitivity, 5)
        self.worker.submit("pause")
        self.drain(2)
        history = load_recording(self.events("session")[0])[0]
        self.assertEqual([segment.settings.rate for segment in history.segments.values()], [100, 500])
        self.assertEqual(history.segments[0].settings.zero_voltage, 0.0)
        self.assertEqual(history.segments[1].settings.filters, ("1 Hz", "Low", "High"))

    def test_apply_while_paused_does_not_restart_the_recording(self):
        self.connect()
        self.start()
        self.worker.submit("pause")
        self.drain(2)
        self.client.finished = 0
        self.worker.submit("apply", settings=replace(Settings(), rate=200))
        self.drain()
        self.assertEqual(self.client.finished, 0)
        self.assertEqual(self.events("state")[-1], "paused")
        self.assertEqual(len(self.events("segment")), 1)

    def test_new_session_replaces_last_output(self):
        self.connect()
        self.start()
        self.drain()
        self.worker.submit("pause")
        self.drain(2)
        first = self.events("session")[0]
        old_samples = len(load_recording(first)[0].samples)
        self.worker.submit("new")
        self.drain()
        self.assertEqual(self.events("reset"), [None])
        self.start()
        second = self.events("session")[-1]
        self.assertEqual(first, second)
        self.assertEqual(Path(second).name, "last_output.csv")
        self.assertLess(self.worker.writer.count, old_samples)
        self.assertTrue(Path(first).exists())

    def test_new_session_without_connection_only_resets_the_display(self):
        self.worker.submit("new")
        self.drain()
        self.assertEqual(self.events("reset"), [None])
        self.assertEqual(self.events("state")[-1], "disconnected")
        self.assertEqual(self.events("error"), [])

    def test_failed_save_does_not_stop_capture(self):
        self.connect()
        self.start()
        self.drain()
        path = self.directory / "existing.csv"
        path.write_text("keep")
        self.worker.submit("save", path=path)
        self.drain()
        self.assertTrue(self.events("save_error"))
        self.assertEqual(self.events("error"), [])
        count = self.worker.writer.count
        self.drain()
        self.assertGreater(self.worker.writer.count, count)
        self.assertEqual(path.read_text(), "keep")

    def test_rollover_verifies_then_starts_a_new_segment_with_a_gap(self):
        self.client.capacity = 40000  # 20,000 samples; worker plans rollover near 190 s.
        self.connect()
        self.start()
        self.now = 190.5  # Host clock says the segment may reach the buffer edge.
        self.drain(1)
        segments = self.events("segment")
        self.assertEqual([segment.reason for segment in segments], ["record", "buffer rollover"])
        self.assertEqual(self.client.finished, 1)
        first, second = segments
        self.assertGreaterEqual(second.start_s, first.start_s + first.samples / first.settings.rate)
        self.worker.submit("pause")
        self.drain(2)
        history = load_recording(self.events("session")[0])[0]
        self.assertTrue(all(segment.verified for segment in history.segments.values()))

    def test_read_error_fails_verifies_disconnects_and_rejects_more_work(self):
        self.connect()
        self.start()
        self.client.read_error = AcquisitionError("UDP timeout")
        self.drain()
        self.assertEqual(self.client.finished, 1)
        self.assertEqual(self.client.calls, ["close"])
        self.assertEqual(self.events("state")[-1], "disconnected")
        self.assertIn("UDP timeout", self.events("error")[-1])
        self.assertEqual(self.events("finished")[0][1], "UDP timeout")
        self.worker.submit("start", settings=Settings())
        self.drain()
        self.assertIn("Connect and pause", self.events("error")[-1])

    def test_finish_failure_is_recorded_and_disconnected(self):
        self.connect()
        self.start()
        self.drain()  # Some samples exist before the failing stop.
        self.client.finish_error = AcquisitionError("mismatch")
        self.worker.submit("pause")
        self.drain()
        self.assertEqual(self.events("state")[-1], "disconnected")
        self.assertIn("mismatch", self.events("finished")[0][1])
        history = load_recording(self.events("session")[0])[0]
        self.assertFalse(history.segments[0].verified)

    def test_close_flushes_files_and_disconnects(self):
        self.connect()
        self.start()
        self.worker.submit("pause")
        self.drain(2)
        self.worker.submit("close")
        self.worker.run()  # The thread's finally block performs the cleanup.
        self.assertTrue(self.spies[0].closed)
        self.assertEqual(self.client.calls, ["close"])
        self.assertEqual(self.events("state")[-1], "disconnected")


class ThreadTests(WorkerHarness):
    def test_close_command_ends_the_thread_and_flushes(self):
        self.connect()
        self.start()
        self.worker.submit("pause")
        time.sleep(0.05)
        self.worker.start()  # A real thread finishes the remaining lifecycle.
        self.worker.submit("close")
        self.worker.join(timeout=5)
        self.assertFalse(self.worker.is_alive())
        self.assertEqual(self.client.calls, ["close"])
        kinds = [name for name, _payload in self.emitted]
        self.assertEqual(kinds[-1], "closed")
        self.assertTrue(self.spies[0].closed)


if __name__ == "__main__":
    unittest.main()
