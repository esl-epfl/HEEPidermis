"""Single-owner acquisition worker. No Tk calls or network I/O on the UI thread."""

from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import queue
import struct
import threading
import time

from .acquire import AcquisitionError, LiveBlock, MP160, TESTED_FIRMWARE, TESTED_FIRMWARES, discover
from .recording import Segment, SessionWriter, Settings


class DemoMP160:
    """Explicit opt-in simulator; never used as a fallback for hardware errors."""

    trace = None

    def information(self):
        return {"firmware": TESTED_FIRMWARE, "buffer_capacity_bytes": 12_843_008, "status": 0x80}

    def start_live(self, channel, rate, capacity):
        self.rate, self.sent = rate, 0
        self.live_started_monotonic = time.monotonic()

    def read_live(self):
        end = max(self.sent, int((time.monotonic() - self.live_started_monotonic) * self.rate))
        values = []
        for index in range(self.sent, end):
            t = index / self.rate
            phase = t % 9
            response = 0.6 * phase * math.exp(-phase / 0.9)
            voltage = 1.5 + 0.12 * math.sin(t * 0.13) + response + 0.003 * math.sin(t * 31)
            values.append(round(voltage * 65535 / 20))
        block = LiveBlock(self.sent, struct.pack(f">{len(values)}h", *values))
        self.sent = end
        return block

    def finish_live(self):
        return self.read_live()

    def close(self):
        pass


class AcquisitionWorker(threading.Thread):
    """Own the socket, autosave files and mutable segment counters in one thread.

    Events are (kind, payload) tuples. Segment events contain copies, while sample
    blocks are immutable. The GUI must not access worker-owned recording objects.
    """

    def __init__(self, *, demo=False, client_factory=MP160, discovery=discover,
                 writer_factory=SessionWriter, clock=time.monotonic, poll_interval=0.1):
        super().__init__(name="biopac-acquisition", daemon=False)
        self.commands, self.events = queue.Queue(), queue.Queue()
        self.demo, self.client_factory, self.discovery = demo, client_factory, discovery
        self.writer_factory, self.clock, self.poll_interval = writer_factory, clock, poll_interval
        self.client, self.writer, self.segment = None, None, None
        self.stop_requested = False
        self.device, self.settings = {}, Settings()
        self.output_dir = Path(__file__).parent / "outs"
        self.origin, self.number = None, 0
        self.last_end = 0.0

    def submit(self, command, **values):
        self.commands.put((command, values))

    def emit(self, kind, payload=None):
        self.events.put((kind, payload))

    def state(self, state):
        self.emit("state", state)

    def _connect(self, interface="", ip="", output_dir=None):
        if self.client is not None:
            raise AcquisitionError("Disconnect before selecting a different device")
        self.state("connecting")
        if output_dir is not None:
            self.output_dir = Path(output_dir).expanduser()
        if self.demo:
            self.client = DemoMP160()
            device = {"serial": "SIMULATED — NO HARDWARE", "ip": "demo"}
        else:
            if not interface:
                raise ValueError("Select the dedicated MP160 Ethernet interface")
            devices = self.discovery(interface, ip or "169.254.255.255")
            if len(devices) != 1:
                raise AcquisitionError(f"Found {len(devices)} MP160 units; check the Ethernet link and IP address")
            device = devices[0]
            self.client = self.client_factory(interface, device["ip"])
        info = self.client.information()
        if info["firmware"] not in TESTED_FIRMWARES:
            raise AcquisitionError(f"Firmware {info['firmware']} is not validated; expected one of {TESTED_FIRMWARES}")
        if info["status"] & 1:
            raise AcquisitionError("The MP160 is already recording. Stop the other recorder first; it was not interrupted.")
        self.device = {**device, **info, "interface": interface, "simulated": self.demo}
        self.emit("device", self.device.copy())
        self.state("connected")

    def _new_session(self):
        if self.segment is not None:
            raise AcquisitionError("Pause before starting a new session")
        self._close_writer()
        self.origin, self.number, self.last_end = None, 0, 0.0
        self.emit("reset")
        self.state("connected" if self.client is not None else "disconnected")

    def _begin(self, settings, reason="record"):
        if self.client is None or self.segment is not None:
            raise AcquisitionError("Connect and pause before starting an acquisition segment")
        settings = Settings.parse(settings.mapping() if isinstance(settings, Settings) else settings)
        if self.writer is None:
            self.writer = self.writer_factory(self.output_dir, self.device, simulated=self.demo)
            self.client.trace = self.writer.trace
            self.origin, self.number, self.last_end = None, 0, 0.0
            self.emit("session", str(self.writer.path))
        self.client.start_live(settings.channel, settings.rate, self.device["buffer_capacity_bytes"])
        anchor = self.client.live_started_monotonic
        if self.origin is None:
            self.origin = anchor
        self.segment = Segment(self.number, max(anchor - self.origin, self.last_end), settings, reason=reason)
        self.number += 1
        self.settings = settings
        # Publish before any file write that could fail, so failures can mark the
        # corresponding GUI segment invalid even when the disk has filled up.
        self.emit("segment", replace(self.segment))
        self.writer.begin(self.segment)
        self.emit("applied", settings)
        self.state("recording")

    def _deliver(self, block):
        if block.count:
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
            raise AcquisitionError(error)
        suffix = "simulated samples saved" if self.demo else "samples matched the frozen MP160 buffer"
        self.emit("log", f"Segment {segment.number + 1}: {segment.samples:,} {suffix}.")

    def _poll(self):
        if self.segment is None:
            return  # Nothing to poll between segments; commands keep the loop alive.
        self._deliver(self.client.read_live())
        segment = self.segment
        # Leave ten seconds of headroom and also respect the one-hour setup
        # horizon. A host-time check protects against a slow/stalled reader.
        limit = min(3590.0, self.device["buffer_capacity_bytes"] / (2 * segment.settings.rate) - 10)
        elapsed = self.clock() - self.client.live_started_monotonic
        if elapsed >= max(0.1, limit) or segment.samples / segment.settings.rate >= max(0.1, limit):
            self.emit("log", "Buffer rollover: stopping and verifying before a new segment (gap retained).")
            self._finish()
            self._begin(self.settings, "buffer rollover")

    def run_once(self):
        """Run one queued command or one poll, so a real thread and tests share code."""
        try:
            # A bounded wait keeps the thread responsive even between segments.
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
        if self.client is not None:
            self.client.trace = None
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
                    client.close()
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
            self._begin(values["settings"], "record" if self.number == 0 else "resume")
        elif command == "pause":
            self._finish()
            self.state("paused")
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
                self.emit("error", f"Close could not fully verify the recording: {error}. Check the MP160 has stopped.")
            self.emit("closed")
