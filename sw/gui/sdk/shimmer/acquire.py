#!/usr/bin/env python3
"""Shimmer3 GSR+ driver: LogAndStream protocol over Bluetooth SPP or dock UART.

The transport is a plain serial stream (pyserial).  Command framing, the
stream state machine and packet decoding are delegated to ``pyshimmer``, the
reference open-source implementation of the Shimmer Bluetooth API.

GSR decoding follows the Shimmer GSR+ user guide: the top two bits of the
16-bit value are the auto-selected range (feedback resistor) and the low
twelve bits are the 12-bit ADC conversion.  Skin resistance is

    Rs = Rf / (ADC · (3.0 V / 4095) / 0.5 V − 1)

and skin conductance in microsiemens is 1e6 / Rs.  An open electrode input
settles at ADC ≈ 682 (the 0.5 V bias point), which the decoder reports as an
open circuit instead of a bogus conductance.
"""

from __future__ import annotations

from collections import deque
import math
import time

import serial

try:
    from pyshimmer import ShimmerBluetooth, DataPacket
    from pyshimmer.dev.channels import EChannelType, ESensorGroup
    from pyshimmer.dev.base import DEV_CLOCK_RATE, dr2sr, sr2dr
except ImportError as error:  # pragma: no cover - dependency guard
    raise SystemExit("Shimmer SDK dependencies missing. Run: python3 -m pip install -r sw/gui/requirements.txt\n"
                     f"{error}") from error

from . import connection


# Feedback resistor in ohms for GSR range 0..3 (Shimmer GSR+ user guide, table 3-2).
RF_OHMS = (40_200.0, 287_000.0, 1_000_000.0, 3_300_000.0)
# Above this skin resistance the electrode input is effectively open (the
# GSR+ resolves up to 4.7 MΩ; an unworn electrode settles at the 0.5 V bias
# point, ADC ≈ 682, i.e. infinite resistance).
OPEN_RESISTANCE_OHMS = 10_000_000.0
OPEN_RATIO_LIMIT = 1e-3
# Device-supported sample rates: 32768 Hz clock divided by an integer.
RATES = (8.0, 10.24, 16.0, 25.6, 32.0, 51.2, 64.0, 102.4, 128.0, 204.8, 256.0, 512.0)
DEFAULT_RATE = 51.2
TESTED_FIRMWARE = "LogAndStream"


class AcquisitionError(RuntimeError):
    pass


def decode_gsr(raw16):
    """(range, adc) from a 16-bit Shimmer GSR word."""
    return raw16 >> 14, raw16 & 0x0FFF


def adc_to_resistance(gsr_range, adc):
    """Skin resistance in ohms, or None when the input is open/invalid."""
    if gsr_range > 3:
        return None
    ratio = adc * (3.0 / 4095.0) / 0.5 - 1.0
    if ratio <= OPEN_RATIO_LIMIT:
        return None
    resistance = RF_OHMS[gsr_range] / ratio
    return None if resistance > OPEN_RESISTANCE_OHMS else resistance


def adc_to_conductance(raw16):
    """Skin conductance in µS from a raw 16-bit word, or None when open."""
    gsr_range, adc = decode_gsr(raw16)
    resistance = adc_to_resistance(gsr_range, adc)
    return None if resistance is None else 1e6 / resistance


class LiveBlock:
    """Immutable batch of decoded GSR samples handed from worker to GUI."""

    __slots__ = ("first_sample", "samples")

    def __init__(self, first_sample, samples):
        self.first_sample = first_sample
        self.samples = samples  # [(device_ticks or None, raw16)]


class ShimmerClient:
    """One connected Shimmer3 GSR+; owns the serial transport and stream."""

    def __init__(self, path, address="", name=""):
        self.path, self.address, self.name = path, address, name
        self._serial = None
        self._device = None
        self._packets = deque()
        self._sent = 0
        self._original_sensors = None
        self._streaming = False
        self.live_started_monotonic = None

    # -- connection ---------------------------------------------------------

    def connect(self):
        try:
            self._serial = serial.Serial(self.path, connection.BAUDRATE, timeout=None)
        except (OSError, serial.SerialException) as error:
            raise AcquisitionError(f"Could not open {self.path}: {error}") from error
        try:
            device = ShimmerBluetooth(self._serial)
            device.initialize()
        except Exception as error:
            self.close()
            raise AcquisitionError(f"The Shimmer did not answer on {self.path}: {error}") from error
        self._device = device
        self.name = self._safe(device.get_device_name, self.name)
        return self

    def _safe(self, call, default=None):
        try:
            return call()
        except Exception:
            return default

    def information(self):
        firmware = self._safe(lambda: self._device.get_firmware_version())
        rate = self._safe(self._device.get_sampling_rate)
        battery = self._safe(lambda: self._device.get_battery_state(in_percent=True))
        channels = []
        inquiry = self._safe(self._device.get_inquiry)
        if inquiry:
            rate = rate or inquiry[0]
            channels = [channel.name for channel in inquiry[2]]
        kind = (firmware[0].name if firmware else "?")
        version = f"{firmware[1].major}.{firmware[1].minor}.{firmware[1].rel}" if firmware else "?"
        return {"name": self.name, "path": self.path, "address": self.address,
                "transport": "dock" if "SHIMMER_DOCK" in self.path else "bluetooth",
                "firmware": kind, "firmware_version": version,
                "firmware_tested": kind == TESTED_FIRMWARE,
                "rate": rate, "battery_percent": battery,
                "channels": channels}

    # -- configuration ------------------------------------------------------

    @staticmethod
    def _groups_for_channels(channels):
        """Sensor groups to restore after streaming, derived from channels."""
        groups = set()
        for channel in channels:
            name = channel.name
            if name.startswith("ACCEL_LN"):
                groups.add(ESensorGroup.ACCEL_LN)
            elif name.startswith("ACCEL_WR"):
                groups.add(ESensorGroup.ACCEL_WR)
            elif name.startswith("ACCEL_MPU"):
                groups.add(ESensorGroup.ACCEL_MPU)
            elif name.startswith("GYRO"):
                groups.add(ESensorGroup.GYRO)
            elif name.startswith("MAG"):
                groups.add(ESensorGroup.MAG)
            elif name == "VBATT":
                groups.add(ESensorGroup.BATTERY)
            elif name == "GSR_RAW":
                groups.add(ESensorGroup.GSR)
        return sorted(groups, key=lambda group: group.value)

    def configure(self, rate=DEFAULT_RATE):
        """Enable GSR-only streaming at *rate*; remembers the previous config."""
        inquiry = self._safe(self._device.get_inquiry)
        if inquiry and self._original_sensors is None:
            self._original_sensors = self._groups_for_channels(inquiry[2]) or \
                [ESensorGroup.ACCEL_LN, ESensorGroup.BATTERY, ESensorGroup.GYRO, ESensorGroup.MAG]
        if rate not in RATES:
            raise AcquisitionError(f"Rate {rate} Hz is not supported; choose one of {RATES}")
        self._safe(lambda: self._device.set_sampling_rate(rate))
        self._device.set_sensors([ESensorGroup.GSR])
        return self._safe(self._device.get_sampling_rate) or rate

    # -- streaming ----------------------------------------------------------

    def _on_packet(self, packet: DataPacket):
        ticks = None
        try:
            ticks = packet[EChannelType.TIMESTAMP]
        except KeyError:
            pass
        try:
            raw = packet[EChannelType.GSR_RAW]
        except KeyError:
            return
        self._packets.append((ticks, raw))

    def start_live(self):
        self._packets.clear()
        self._sent = 0
        self._device.add_stream_callback(self._on_packet)
        self._device.start_streaming()
        self._streaming = True
        self.live_started_monotonic = time.monotonic()

    def read_live(self):
        samples = []
        while self._packets:
            samples.append(self._packets.popleft())
        block = LiveBlock(self._sent, samples)
        self._sent += len(samples)
        return block

    def finish_live(self):
        if self._streaming:
            try:
                self._device.stop_streaming()
            finally:
                self._streaming = False
        return self.read_live()

    # -- teardown -----------------------------------------------------------

    def close(self):
        try:
            if self._device is not None:
                if self._streaming:
                    self._safe(self._device.stop_streaming)
                if self._original_sensors:
                    self._safe(lambda: self._device.set_sensors(self._original_sensors))
                self._safe(self._device.shutdown)
        finally:
            self._device = None
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None


class DemoShimmer:
    """Explicit opt-in simulator; never used as a fallback for hardware errors."""

    def __init__(self, path="demo", address="", name="SIMULATED"):
        self.path, self.address, self.name = path, address, name
        self.rate = DEFAULT_RATE
        self.live_started_monotonic = None
        self._sent = 0

    def connect(self):
        return self

    def information(self):
        return {"name": "Shimmer_DEMO", "path": "demo", "address": "", "transport": "simulated",
                "firmware": TESTED_FIRMWARE, "firmware_version": "0.0.0", "firmware_tested": True,
                "rate": self.rate, "battery_percent": 100.0, "channels": ["GSR_RAW"]}

    def configure(self, rate=DEFAULT_RATE):
        self.rate = rate
        return rate

    def start_live(self):
        self._sent = 0
        self.live_started_monotonic = time.monotonic()

    def read_live(self):
        total = int((time.monotonic() - self.live_started_monotonic) * self.rate)
        samples = []
        for index in range(self._sent, total):
            t = index / self.rate
            tonic = 4.0 + 0.8 * math.sin(t * 0.05)
            response = 2.5 * max(0.0, (t % 20) - 12) * math.exp(-((t % 20) - 12) / 2.0)
            conductance = tonic + response
            resistance = 1e6 / conductance
            gsr_range = next((r for r in (3, 2, 1, 0) if resistance <= RF_OHMS[r]), 0)
            ratio = RF_OHMS[gsr_range] / resistance
            adc = round((ratio + 1.0) * 4095.0 / 6.0)
            samples.append((None, (gsr_range << 14) | min(4095, max(0, adc))))
        block = LiveBlock(self._sent, samples)
        self._sent = total
        return block

    def finish_live(self):
        return self.read_live()

    def close(self):
        pass


def connect(device, demo=False, request_privilege=None, log=lambda message: None):
    """Find, possibly pair/bind, open and identify a Shimmer.

    *device* is a dict from ``connection.find`` or {"mode": "auto"}; with
    "auto" the first transport that answers a version probe is used.
    """
    if demo:
        return DemoShimmer().connect().information(), DemoShimmer()
    candidates = [device] if device.get("mode") in ("bluetooth", "dock") else connection.find(
        device.get("preferred", ""), log=log, mode=device.get("connection_mode", "auto"))
    if not candidates:
        raise AcquisitionError("No Shimmer found. Check that the device is on and, for Bluetooth, "
                               "advertising (blinking blue LED); or dock it and try again.")
    failures = []
    for index, candidate in enumerate(candidates):
        try:
            log(f"Trying {candidate.get('name', candidate.get('address', candidate.get('port', '?')))}…")
            path, transport = connection.open_link(candidate, request_privilege, log)
            if transport == "dock" and not connection.probe(path):
                failures.append(f"{path}: no LogAndStream answer on the dock UART")
                continue
            client = ShimmerClient(path, candidate.get("address", ""), candidate.get("name", "")).connect()
            return client.information(), client
        except (AcquisitionError, connection.LinkError) as error:
            failures.append(str(error))
            if index + 1 >= len(candidates):
                break
    raise AcquisitionError("No Shimmer could be opened: " + "; ".join(failures))
