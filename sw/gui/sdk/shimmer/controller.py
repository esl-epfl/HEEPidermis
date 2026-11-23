"""Single-owner Shimmer acquisition worker. No Tk calls on this thread.

Commands and events mirror the BIOPAC worker.  Two extra event kinds exist for
the interactive parts of Bluetooth linking, both answered through a
``threading.Event`` carried in the payload so the worker can block on the GUI:

- ``choose``     — several Shimmers answered discovery; the GUI shows a picker.
- ``privilege``  — binding the RFCOMM node needs administrator rights and the
                   automated attempts (direct, pkexec popup) failed; the GUI
                   may show the exact command and let the user run it manually.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import queue
import threading
import time

from . import acquire, connection
from .recording import Segment, SessionWriter, Settings


class AcquisitionWorker(threading.Thread):
    """Own the serial link, autosave files and segment counters in one thread."""

    def __init__(self, *, demo=False, client_factory=None, writer_factory=SessionWriter,
                 clock=time.monotonic, poll_interval=0.1):
        super().__init__(name="shimmer-acquisition", daemon=False)
        self.commands, self.events = queue.Queue(), queue.Queue()
        self.demo = demo
        self.writer_factory = writer_factory
        self.clock, self.poll_interval = clock, poll_interval
        self.client, self.writer, self.segment = None, None, None
        self.stop_requested = False
        self.device, self.settings = {}, Settings()
        self.output_dir = Path(__file__).resolve().parents[2] / "outs" / "shimmer"
        self.origin, self.number, self.last_end = None, 0, 0.0
        self.new_recording = False

    def submit(self, command, **values):
        self.commands.put((command, values))

    def emit(self, kind, payload=None):
        self.events.put((kind, payload))

    def state(self, state):
        self.emit("state", state)

    def _choose_device(self, candidates):
        """Ask the GUI to pick one device when discovery is ambiguous."""
        chooser, done = threading.Event(), {}
        self.emit("choose", {"candidates": candidates, "done": done, "event": chooser})
        if not chooser.wait(timeout=300):
            raise acquire.AcquisitionError("No Shimmer was selected from the discovery list.")
        return done.get("choice")

    def _request_privilege(self, command):
        """Last-resort privilege escalation: show the command in the GUI."""
        answer, done = threading.Event(), {}
        self.emit("privilege", {"command": command, "done": done, "event": answer})
        answer.wait(timeout=600)
        return bool(done.get("accepted"))

    def _connect(self, address="", name="", output_dir=None, connection_mode="auto"):
        if self.client is not None:
            raise acquire.AcquisitionError("Disconnect before selecting a different device")
        if connection_mode not in ("auto", "dock", "bluetooth"):
            raise acquire.AcquisitionError(f"Unknown connection mode: {connection_mode}")
        self.state("connecting")
        if output_dir:
            self.output_dir = Path(output_dir).expanduser()
        if self.demo:
            info, self.client = acquire.connect({"mode": "auto"}, demo=True)
        else:
            device = {"mode": "auto", "preferred": address}
            if address and address.startswith("/dev/"):
                device = {"mode": "dock", "port": address, "name": name}
            elif address:
                device = {"mode": "bluetooth", "address": address, "name": name}
            if device["mode"] == "auto":
                where = {"auto": "dock UART, then Bluetooth scan",
                         "dock": "USB dock UART",
                         "bluetooth": "Bluetooth scan"}[connection_mode]
                self.emit("log", f"Searching for Shimmers ({where})…")
                candidates = connection.find(address, log=lambda text: self.emit("log", text),
                                             mode=connection_mode)
                if not candidates:
                    raise acquire.AcquisitionError(
                        "No Shimmer found on the " + ("USB dock. Check that the dock is plugged "
                         "in and the Shimmer is seated on it." if connection_mode == "dock" else
                         "Bluetooth scan. Check the device is powered on and advertising "
                         "(blinking blue LED) — toggle its power switch if needed."))
                if len(candidates) > 1 and not address:
                    picked = self._choose_device(candidates)
                    if picked is None:
                        raise acquire.AcquisitionError("No Shimmer was selected.")
                    candidates = [picked]
                device = candidates[0]
            info, self.client = acquire.connect(
                device, request_privilege=self._request_privilege,
                log=lambda text: self.emit("log", text))
            if not info.get("firmware_tested", True):
                self.emit("log", f"Firmware {info['firmware']} {info.get('firmware_version', '')} "
                                 "was not validated with this driver; continuing.")
        self.device = {**info, "simulated": self.demo}
        self.emit("device", self.device.copy())
        self.state("connected")

    def _new_session(self):
        if self.segment is not None:
            raise acquire.AcquisitionError("Pause before starting a new session")
        self._close_writer()
        self.origin, self.number, self.last_end = None, 0, 0.0
        self.emit("reset")
        self.state("connected" if self.client is not None else "disconnected")

    def _begin(self, settings, reason="record"):
        if self.client is None or self.segment is not None:
            raise acquire.AcquisitionError("Connect and pause before starting an acquisition segment")
        settings = Settings.parse(settings.mapping() if isinstance(settings, Settings) else settings)
        if self.writer is None:
            self.writer = self.writer_factory(self.output_dir, self.device, simulated=self.demo)
            self.origin, self.number, self.last_end = None, 0, 0.0
            self.emit("session", str(self.writer.path))
        rate = self.client.configure(settings.rate)
        self.client.start_live()
        anchor = self.client.live_started_monotonic
        if self.origin is None:
            self.origin = anchor
        self.emit("timebase", self.origin)
        self.writer.metadata["clock_origin_monotonic_s"] = self.origin
        self.segment = Segment(self.number, max(anchor - self.origin, self.last_end),
                               replace(settings, rate=rate), reason=reason)
        self.number += 1
        self.settings = settings
        self.emit("segment", replace(self.segment))
        self.writer.begin(self.segment)
        self.emit("applied", settings)
        self.state("recording")

    def _deliver(self, block):
        if block.samples:
            self.writer.append(self.segment, block)
            self.emit("samples", (self.segment.number, block))

    def _finish(self, prior_error=None):
        if self.segment is None:
            return
        self.state("verifying")
        segment = self.segment
        error = prior_error
        try:
            self._deliver(self.client.finish_live())
        except Exception as failure:
            error = "; ".join(filter(None, (error, str(failure))))
        finally:
            self.last_end = segment.start_s + segment.samples / segment.settings.rate
            try:
                self.writer.finish(segment, error)
            except Exception as failure:
                error = "; ".join(filter(None, (error, f"Metadata save failed: {failure}")))
            self.emit("finished", (segment.number, error))
            self.segment = None
        if error:
            raise acquire.AcquisitionError(error)
        self.emit("log", f"Segment {segment.number + 1}: {segment.samples:,} samples saved.")

    def _poll(self):
        if self.segment is None:
            return
        self._deliver(self.client.read_live())

    def run_once(self):
        """Run one queued command or one poll, so a real thread and tests share code."""
        try:
            command, values = self.commands.get(timeout=self.poll_interval)
        except queue.Empty:
            try:
                self._poll()
            except Exception as error:
                self._fail(error)
            return
        if command == "close":
            self.stop_requested = True
            return
        try:
            self._command(command, values)
        except Exception as error:
            self._fail(error)

    def _close_writer(self):
        if self.writer is not None:
            writer, self.writer = self.writer, None
            writer.close()

    def _disconnect(self):
        try:
            self._finish()
        finally:
            try:
                self._close_writer()
            finally:
                if self.client is not None:
                    client, self.client = self.client, None
                    try:
                        client.close()
                    except Exception as error:
                        self.emit("log", f"Device close warning: {error}")
        self.state("disconnected")

    def _fail(self, error):
        message = str(error) or type(error).__name__
        try:
            self._finish(message)
        except Exception as failure:
            message = str(failure)
        try:
            self._disconnect()
        except Exception as failure:
            message += f"; cleanup: {failure}"
        self.emit("error", message)
        self.state("disconnected")

    def _command(self, command, values):
        if command == "connect":
            self._connect(**values)
        elif command == "start":
            if self.new_recording:
                self._new_session()
                self.new_recording = False
            self._begin(values["settings"], "record" if self.number == 0 else "resume")
        elif command == "pause":
            self._finish()
            self.state("paused")
        elif command == "stop":
            self._finish()
            self.new_recording = True
            self.state("paused")
        elif command == "save":
            if self.writer is None:
                raise ValueError("No live recording to rename")
            # A filename error must not stop acquisition.
            try:
                path = self.writer.rename(values["path"])
                self.emit("saved", str(path))
            except OSError as error:
                self.emit("save_error", str(error))
        elif command == "apply":
            settings = Settings.parse(values["settings"].mapping())
            running = self.segment is not None
            if running:
                self._finish()
            self.settings = settings
            self.emit("applied", settings)
            if running:
                self._begin(settings, "settings changed")
            else:
                self.state("paused" if self.writer is not None else "connected")
        elif command == "new":
            self._new_session()
            self.new_recording = False
        elif command == "disconnect":
            self._disconnect()
        else:
            raise ValueError(f"Unknown acquisition command: {command}")

    def run(self):
        try:
            while not self.stop_requested:
                self.run_once()
        finally:
            try:
                self._disconnect()
            except Exception as error:
                self.emit("error", f"Close could not fully verify the recording: {error}. "
                                   "Check that the Shimmer stopped streaming.")
            self.emit("closed")
