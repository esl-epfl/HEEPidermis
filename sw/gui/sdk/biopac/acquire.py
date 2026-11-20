#!/usr/bin/env python3
"""Experimental, input-only MP160 recorder for Linux; Python standard library only."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import ipaddress
import json
import math
from pathlib import Path
import socket
import struct
import sys
import threading
import time

if __package__:
    from .profiles import DEFAULT_PROFILE, PROFILES, get_profile
else:  # Support running this file directly without GUI/scientific dependencies.
    from profiles import DEFAULT_PROFILE, PROFILES, get_profile


CONTROL_PORT = 16005
DATA_PORT = 16004
RATES = (100, 200, 500, 1000)
TESTED_FIRMWARE = "2.1.06"
TESTED_FIRMWARES = (TESTED_FIRMWARE, "2.0.00")  # Both exercised with the existing input/buffer protocol.
READ_BYTES = 1400  # Response plus UDP/IP headers fits in a 1500-byte Ethernet MTU.
VOLTS_PER_COUNT = 20.0 / 65535.0


class AcquisitionError(RuntimeError):
    pass


def frame(command, payload=b""):
    body = bytes((1, command)) + payload
    return body + bytes((-sum(body) & 255,))


def decode_reply(packet, expected):
    if len(packet) != expected + 2:
        raise AcquisitionError(f"Expected {expected + 2} reply bytes, received {len(packet)}")
    if packet[0] != 1 or sum(packet) & 255:
        raise AcquisitionError("Invalid MP160 reply header or checksum")
    return packet[1:-1]


def identification(packet, address):
    # Linux delivers our own broadcast back to a wildcard-bound socket too.
    if len(packet) != 42 or packet[:7] != b"iDnt\x00\x90\x3d":
        return None
    try:
        serial = packet[10:].split(b"\0", 1)[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not serial or address[1] != CONTROL_PORT:
        return None
    return {"ip": address[0], "mac": packet[4:10].hex(":"), "serial": serial}


_SIOCGIFADDR = 0x8915
_SIOCGIFNETMASK = 0x891B
LINK_LOCAL_BROADCAST = "169.254.255.255"
GLOBAL_BROADCAST = "255.255.255.255"


def interface_address(name):
    """(IPv4 address, netmask) configured on an interface, else None."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        packed = struct.pack("256s", name.encode()[:15])
        try:
            ip = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, packed)[20:24])
            mask = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), _SIOCGIFNETMASK, packed)[20:24])
        except OSError:
            return None
    return ip, mask


def interface_addresses():
    """All non-loopback interfaces that have an IPv4 address: {name: (ip, mask)}."""
    addresses = {}
    for _index, name in socket.if_nameindex():
        if name.startswith("lo"):
            continue
        address = interface_address(name)
        if address is not None:
            addresses[name] = address
    return addresses


def broadcast_address(ip, netmask):
    """The directed broadcast address of an interface's IPv4 subnet."""
    network = struct.unpack(">I", socket.inet_aton(ip))[0]
    bits = struct.unpack(">I", socket.inet_aton(netmask))[0]
    return socket.inet_ntoa(struct.pack(">I", network | (~bits & 0xFFFFFFFF)))


def probe_interface(name, target=None, deadline=0.0):
    """Broadcast 'iDnt' on one interface until *deadline*; return the units.

    SO_BINDTODEVICE scopes the probe to the link when the process is
    privileged; otherwise the socket is bound to the interface's own address,
    which scopes directed broadcast just as well for unprivileged users.
    """
    address = interface_address(name)
    if address is None:
        return []
    ip, netmask = address
    if target:
        targets = [target]
    else:
        targets = [broadcast_address(ip, netmask), LINK_LOCAL_BROADCAST, GLOBAL_BROADCAST]
    devices = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, name.encode() + b"\0")
        except OSError:
            pass  # Unprivileged: the bound source address scopes packets instead.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind((ip, CONTROL_PORT))
        except OSError:
            return []  # Control port busy on this address; another socket owns it.
        while time.monotonic() < deadline:
            for one in targets:
                sock.sendto(b"iDnt", (one, CONTROL_PORT))
            until = min(deadline, time.monotonic() + 0.5)
            while time.monotonic() < until:
                sock.settimeout(max(0.001, until - time.monotonic()))
                try:
                    packet, from_address = sock.recvfrom(8192)
                except socket.timeout:
                    break
                except OSError:
                    return list(devices.values())
                device = identification(packet, from_address)
                if device is not None:
                    devices.setdefault(device["mac"], {**device, "interface": name})
            if devices:
                break  # Answered; no need to keep broadcasting on this link.
    return list(devices.values())


def interface_for(ip):
    """Name of the interface used to reach *ip* (route lookup via UDP connect)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect((ip, 9))
            source = sock.getsockname()[0]
        except OSError:
            return ""
    for name, (address, _mask) in interface_addresses().items():
        if address == source:
            return name
    return ""


def arp_neighbours():
    """(ip, mac, interface) for neighbours with a completed ARP entry."""
    try:
        lines = Path("/proc/net/arp").read_text(encoding="utf-8", errors="replace").splitlines()[1:]
    except OSError:
        return []
    neighbours = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 6 and fields[2] == "0x2" and fields[3] != "00:00:00:00:00:00":
            neighbours.append((fields[0], fields[3], fields[5]))
    return neighbours


def known_addresses(interface=""):
    """Candidate MP160 IPv4s without any broadcast: neighbours that answered
    ARP (the kernel remembers the receiver once anything touched it) plus the
    device IPs recorded in past session metadata under the recordings folder.
    """
    candidates = []
    for ip, _mac, device in arp_neighbours():
        if interface and device != interface:
            continue
        candidates.append(ip)
    directory = Path(__file__).resolve().parents[2] / "outs"
    try:
        metadata = list(directory.glob("*.json")) + list((directory / "biopac").glob("*.json"))
    except OSError:
        metadata = []
    for path in metadata:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        ip = (data.get("device") or {}).get("ip") if isinstance(data, dict) else None
        if ip and ip not in candidates:
            candidates.append(str(ip))
    return candidates[:64]


def probe_unicast(addresses, deadline):
    """iDnt sent unicast to each candidate; returns identified units.

    Some MP160 units answer unicast but stay silent on broadcast, so a
    remembered or ARP-visible address is probed directly.
    """
    devices = {}
    if not addresses:
        return []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind(("0.0.0.0", CONTROL_PORT))
        except OSError:
            pass  # Port busy: bind ephemeral; the unit replies to the source port.
        for ip in addresses:
            try:
                sock.sendto(b"iDnt", (ip, CONTROL_PORT))
            except OSError:
                continue
        until = min(deadline, time.monotonic() + 1.0)
        while time.monotonic() < until:
            sock.settimeout(max(0.001, until - time.monotonic()))
            try:
                packet, from_address = sock.recvfrom(8192)
            except socket.timeout:
                break
            except OSError:
                break
            device = identification(packet, from_address)
            if device is not None:
                device.setdefault("interface", interface_for(device["ip"]))
                devices.setdefault(device["mac"], device)
    return list(devices.values())


def discover(interface="", target=None, timeout=2.0):
    """Identify MP160 units; no hardware configuration writes.

    An explicit *interface* probes only that link. The default "" scans every
    non-loopback interface that has an IPv4 address in parallel (each unit
    reports the link it was found on), so the receiver is found no matter how
    it is connected. *target* (an explicit device IP) is sent unicast on all
    probed links.
    """
    if interface:
        if socket.if_nametoindex(interface) == 0:
            raise AcquisitionError(f"No such interface: {interface}")
        names = [interface]
    else:
        names = sorted(interface_addresses())
    if not names:
        return []
    deadline = time.monotonic() + timeout
    results = [None] * len(names)
    threads = []
    for index, name in enumerate(names):
        thread = threading.Thread(target=lambda i=index, n=name:
                                  results.__setitem__(i, probe_interface(n, target, deadline)),
                                  daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(max(0.1, deadline - time.monotonic() + 1.0))
    devices = {}
    for found in results:
        for device in found or []:
            devices.setdefault(device["mac"], device)
    if not devices and not target:
        # Some units answer unicast but ignore broadcast: recall their
        # remembered addresses (ARP cache + past recordings) directly.
        candidates = [ip for ip in known_addresses(interface)
                      if ip not in {device["ip"] for device in devices.values()}]
        for device in probe_unicast(candidates, time.monotonic() + min(1.5, timeout)):
            devices.setdefault(device["mac"], device)
    return list(devices.values())


def timer_payload(rate):
    if rate not in RATES:
        raise ValueError(f"Supported input rates: {RATES}")
    # Vendor timer encoding: 0 prescaler, 4 MHz clock, reload = ticks - 2.
    timer = b"\0\0\0" + struct.pack(">H", 4_000_000 // rate - 2)
    # The setup command initializes three timers. It does not enable DAC outputs.
    return timer * 3


@dataclass
class Capture:
    raw: bytes
    channel: int
    rate: int
    requested_seconds: float
    host_seconds: float
    started_utc: str
    interrupted: bool

    @property
    def count(self):
        return len(self.raw) // 2

    def counts(self):
        return (value for (value,) in struct.iter_unpack(">h", self.raw))


@dataclass(frozen=True)
class LiveBlock:
    first_sample: int
    raw: bytes

    @property
    def count(self):
        return len(self.raw) // 2


class MP160:
    """Serialized UDP command channel, using the device's extended acquisition buffer.

    Replies have no command identifier. Do not pipeline or blindly retry commands:
    a late reply could otherwise be attributed to a different request.
    """

    def __init__(self, interface, address, trace=None, timeout=2.0):
        self.trace = trace
        self._live_raw = None
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if interface:
                try:
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                                           interface.encode() + b"\0")
                    self.socket.bind(("0.0.0.0", DATA_PORT))
                except PermissionError:
                    # Unprivileged users cannot scope with SO_BINDTODEVICE;
                    # bind the interface's own address instead, which scopes
                    # the channel to that link just as well.
                    self.socket.close()
                    self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    scoped = interface_address(interface)
                    if scoped is None:
                        raise AcquisitionError(f"Interface {interface} has no IPv4 address")
                    self.socket.bind((scoped[0], DATA_PORT))
            else:
                # No interface known (explicit-IP connect): route normally.
                self.socket.bind(("0.0.0.0", DATA_PORT))
            # No SO_REUSEADDR: another recorder must not share this command port.
            self.socket.connect((address, DATA_PORT))
            self.socket.settimeout(timeout)
        except BaseException:
            self.socket.close()
            raise

    def close(self):
        self.socket.close()

    def _exchange(self, command, payload=b"", expected=1, acknowledge=False):
        packet = frame(command, payload)
        event = {"monotonic_s": time.monotonic(), "command": command, "tx_hex": packet.hex()}
        try:
            self.socket.send(packet)
            response = self.socket.recv(65535)
            event["rx_hex"] = response.hex()
            result = decode_reply(response, expected)
            if acknowledge and result != b"\x06":
                raise AcquisitionError(f"MP160 rejected command {command:#04x}: {result.hex()}")
            return result
        except (OSError, AcquisitionError) as error:
            event["error"] = str(error)
            raise AcquisitionError(f"Command {command:#04x}: {error}") from error
        finally:
            if self.trace is not None:
                self.trace(event)

    def _set(self, command, payload=b""):
        self._exchange(command, payload, acknowledge=True)

    def status(self):
        return self._exchange(0x07)[0]

    def information(self):
        firmware = self._exchange(0xA8, b"\x05", expected=32)
        return {
            "firmware": firmware.split(b"\0", 1)[0].decode("ascii"),
            "buffer_capacity_bytes": struct.unpack(">III", self._exchange(0x88, expected=12))[0],
            "status": self.status(),
        }

    def _position(self):
        return struct.unpack(">II", self._exchange(0x91, expected=8))

    def _read(self, offset, size):
        return self._exchange(0x93, struct.pack(">IH", offset, size), expected=size)

    def _frozen_buffer(self, capacity):
        previous = self._position()
        for _ in range(3):
            position = self._position()
            if position == previous:
                break
            previous = position
        else:
            raise AcquisitionError("Acquisition buffer is still changing after Stop")
        size, wraps = position
        if wraps or size > capacity:
            raise AcquisitionError("Acquisition buffer wrapped; use a shorter recording")
        if not size or size % 2:
            raise AcquisitionError(f"Invalid acquired byte count: {size}")
        raw = b"".join(self._read(offset, min(READ_BYTES, size - offset))
                       for offset in range(0, size, READ_BYTES))
        # Verify a frozen snapshot rather than silently exporting changing memory.
        for offset in {0, max(0, size - 64)}:
            length = min(64, size - offset)
            if self._read(offset, length) != raw[offset:offset + length]:
                raise AcquisitionError("Acquired memory changed during readout")
        return raw

    def _prepare(self, seconds, channel, rate, capacity):
        if not math.isfinite(seconds) or not 0 < seconds <= 3600:
            raise ValueError("Recording duration must be greater than 0 and at most 3600 seconds")
        # Public channel numbers are one-based. The start command selects the
        # corresponding zero-based bit: channel 3 = 0x0004, channel 9 = 0x0100.
        if not 1 <= channel <= 16:
            raise ValueError("Channel must be 1 through 16")
        timers = timer_payload(rate)
        if (math.ceil(seconds * rate) + 2 * rate) * 2 >= capacity:
            raise ValueError("Recording would exceed the device buffer; shorten its duration")
        if self.status() & 1:
            raise AcquisitionError("The MP160 is already acquiring. Stop that recording first.")

        self._set(0xA9, b"\x01\0\0\0\0")  # Extended-buffer acquisition mode.
        self._set(0x9C, b"\0" * 5)  # Free-running, no external trigger.
        self._set(0x85, struct.pack(">I", math.ceil(seconds * rate) + 1))
        self._set(0x06, struct.pack(">I", 0))
        self._set(0x89, timers)

    def start_live(self, channel, rate, capacity):
        """Start one continuous, non-wrapping acquisition segment.

        The caller must poll, stop before capacity, and call finish_live on exit.
        All calls belong to one acquisition thread, never the Tk thread.
        """
        if self._live_raw is not None:
            raise AcquisitionError("This connection already owns a live acquisition")
        self._prepare(min(3600, (capacity // 2 - 4 * rate) / rate), channel, rate, capacity)
        self._live_capacity = capacity
        self._live_raw = bytearray()
        try:
            self.live_started_monotonic = time.monotonic()
            self._set(0x82, b"\x03\0" + struct.pack(">HH", 1 << (channel - 1), 0))
        except BaseException:
            self._live_raw = None
            self._set(0x12)
            raise

    def read_live(self):
        """Read newly completed samples, not the still-being-written tail word."""
        if self._live_raw is None:
            raise AcquisitionError("No live acquisition on this connection")
        size, wraps = self._position()
        first = len(self._live_raw)
        if wraps or size > self._live_capacity or size < first:
            raise AcquisitionError("Live acquisition buffer wrapped or its counter moved backwards")
        size -= size % 2
        size = min(size, first + 8 * READ_BYTES)
        raw = b"".join(self._read(offset, min(READ_BYTES, size - offset))
                       for offset in range(first, size, READ_BYTES))
        self._live_raw.extend(raw)
        return LiveBlock(first // 2, raw)

    def finish_live(self):
        """Stop, verify every live byte against frozen memory, and return the tail.

        Live samples are provisional until this verification completes. A mismatch
        is an error, never a silently accepted or retrospectively relabeled signal.
        """
        if self._live_raw is None:
            return LiveBlock(0, b"")
        previous = self._live_raw
        try:
            self._set(0x12)
            if self.status() & 1:
                raise AcquisitionError("The MP160 did not confirm that ADC acquisition stopped")
            raw = self._frozen_buffer(self._live_capacity)
            if not raw.startswith(previous):
                raise AcquisitionError("Live samples do not match the final frozen acquisition buffer")
            return LiveBlock(len(previous) // 2, raw[len(previous):])
        finally:
            self._live_raw = None

    def capture(self, seconds, channel, rate, capacity):
        self._prepare(seconds, channel, rate, capacity)
        interrupted = False
        started_utc = datetime.now(timezone.utc).isoformat()
        before = time.monotonic()
        try:
            # This command starts input acquisition. Stop even if its ACK is lost.
            self._set(0x82, b"\x03\0" + struct.pack(">HH", 1 << (channel - 1), 0))
            before = time.monotonic()
            try:
                time.sleep(seconds)
            except KeyboardInterrupt:
                interrupted = True
        finally:
            # 0x12 stops ADC acquisition; 0x22 alone does NOT freeze the ADC buffer.
            self._set(0x12)
        elapsed = time.monotonic() - before
        if self.status() & 1:
            raise AcquisitionError("The MP160 did not confirm that ADC acquisition stopped")
        raw = self._frozen_buffer(capacity)
        return Capture(raw, channel, rate, seconds, elapsed, started_utc, interrupted)


def csv_rows(capture, sensitivity, zero_voltage):
    for index, count in enumerate(capture.counts()):
        voltage = count * VOLTS_PER_COUNT
        yield (index, index / capture.rate, capture.channel, count, voltage,
               (voltage - zero_voltage) * sensitivity, int(count in (-32768, 32767)))


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True, help="Dedicated MP160 Ethernet interface")
    parser.add_argument("--ip", type=ipaddress.IPv4Address, help="MP160 address, if already known")
    parser.add_argument("--probe", action="store_true", help="Identify hardware without starting acquisition")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--profile", choices=tuple(PROFILES), default=DEFAULT_PROFILE,
                        help="Manual hardware preset (default: eda100c); explicit options override its defaults")
    parser.add_argument("--rate", type=int, choices=RATES, help="MP160 sampling rate in Hz (default: profile rate)")
    parser.add_argument("--channel", type=int, choices=range(1, 17),
                        help="One-based MP160 analog input, 1–16 (default: profile channel)")
    parser.add_argument("--sensitivity", type=float, help="Nominal/calibrated µS/V (default: profile sensitivity)")
    parser.add_argument("--zero-voltage", type=float, help="Known calibrated zero in V; not a tissue baseline (default: 0)")
    parser.add_argument("--filters", nargs=3, metavar=("FILTER_1", "FILTER_2", "FILTER_3"),
                        help="Manual filter declarations; BioNomadix uses overall bandwidth then N/A N/A")
    parser.add_argument("--output", type=Path, help="New CSV path (never overwrites existing files)")
    args = parser.parse_args(argv)
    profile = get_profile(args.profile)
    for name in ("channel", "rate", "sensitivity", "zero_voltage", "filters"):
        if getattr(args, name) is None:
            setattr(args, name, getattr(profile, name))
    args.filters = tuple(value.strip() for value in args.filters)
    if not sys.platform.startswith("linux"):
        parser.error("This native acquisition backend currently supports Linux only")
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 3600:
        parser.error("--seconds must be greater than 0 and at most 3600")
    if not math.isfinite(args.sensitivity) or args.sensitivity <= 0 or not math.isfinite(args.zero_voltage):
        parser.error("Sensitivity must be finite and positive; zero voltage must be finite")
    if any(not value or len(value) > 40 for value in args.filters):
        parser.error("Enter three nonempty filter declarations (maximum 40 characters each)")
    return args


def main(argv=None):
    args = parse_arguments(argv)
    profile = get_profile(args.profile)

    try:
        devices = discover(args.interface or "", str(args.ip) if args.ip else None)
        if len(devices) != 1:
            raise AcquisitionError(f"Found {len(devices)} BIOPAC units; check the link/IP or specify --ip")
        device = devices[0]
        interface = device.get("interface") or args.interface or ""
        with ExitStack() as stack:
            client = MP160(interface, device["ip"])
            stack.callback(client.close)
            info = client.information()
            if args.probe:
                print(json.dumps({**device, **info}, indent=2))
                return 0
            if info["firmware"] not in TESTED_FIRMWARES:
                raise AcquisitionError(f"Firmware {info['firmware']} is not validated; expected one of {TESTED_FIRMWARES}")

            output = args.output or (Path(__file__).parent / "outs" /
                                     f"{datetime.now():%Y_%m_%d_%H%M%S_%f}_ch{args.channel}.csv")
            paths = [output, Path(str(output) + ".json"), Path(str(output) + ".raw"),
                     Path(str(output) + ".udp.jsonl")]
            if any(path.exists() for path in paths):
                raise AcquisitionError("An output file already exists; choose a new --output path")
            output.parent.mkdir(parents=True, exist_ok=True)
            csv_file = stack.enter_context(output.open("x", newline=""))
            meta_file = stack.enter_context(paths[1].open("x"))
            raw_file = stack.enter_context(paths[2].open("xb"))
            trace_file = stack.enter_context(paths[3].open("x"))

            def trace(event):
                trace_file.write(json.dumps(event) + "\n")
                trace_file.flush()

            client.trace = trace
            metadata = {
                "schema_version": 1, "backend": "experimental_native_mp160_udp",
                "device": {**device, **info}, "interface": args.interface,
                "hardware_profile": args.profile,
                "hardware_profile_defaults": asdict(profile),  # Preset, not readback or measured calibration.
                "channel": args.channel, "requested_seconds": args.seconds,
                "sample_rate_hz_nominal": args.rate, "adc_volts_per_count_nominal": VOLTS_PER_COUNT,
                "sensitivity_us_per_v_manual": args.sensitivity, "calibrated_zero_voltage_v": args.zero_voltage,
                "hardware_filters_manual": list(args.filters),
                "calibration": "nominal conversion; verify against known loads",
                "timebase": "sample index / nominal hardware rate; not host packet arrival time",
                "status": "failed",
            }
            writer = csv.writer(csv_file)
            writer.writerow(("sample_index", "time_s_nominal", "channel", "adc_count",
                             "voltage_v_nominal", "conductance_us_nominal", "adc_clipped"))
            print(f"MP160 {device['serial']} at {device['ip']}, firmware {info['firmware']}", flush=True)
            print(f"Profile: {profile.name}; {args.sensitivity:g} µS/V; filters: {' / '.join(args.filters)}", flush=True)
            print(profile.notes, flush=True)
            print(f"Recording channel {args.channel}, {args.rate} Hz, {args.seconds:g} s. Ctrl+C stops and saves.", flush=True)
            try:
                capture = client.capture(args.seconds, args.channel, args.rate, info["buffer_capacity_bytes"])
                raw_file.write(capture.raw)
                writer.writerows(csv_rows(capture, args.sensitivity, args.zero_voltage))
                observed = capture.count / capture.host_seconds
                counts = list(capture.counts())
                metadata.update({
                    "status": "interrupted" if capture.interrupted else "complete",
                    "samples": capture.count, "host_acquisition_seconds": capture.host_seconds,
                    "started_utc_host": capture.started_utc, "observed_rate_hz_approx": observed,
                    "rate_consistent_with_host": abs(capture.count - args.rate * capture.host_seconds)
                        <= max(3, 0.03 * capture.count),
                    "minimum_adc_count": min(counts), "maximum_adc_count": max(counts),
                    "adc_clipped_samples": sum(value in (-32768, 32767) for value in counts),
                    "frozen_buffer_verified": True,
                })
            except BaseException as error:
                metadata["error"] = str(error) or type(error).__name__
                raise
            finally:
                json.dump(metadata, meta_file, indent=2)
                meta_file.write("\n")
            print(f"Saved {capture.count} samples ({observed:.2f} samples/s observed): {output}")
            if not metadata["rate_consistent_with_host"]:
                print("WARNING: Sample count does not match the nominal hardware rate.", file=sys.stderr)
            if max(abs(value) for value in counts) >= 0.98 * 32767:
                print("WARNING: Signal approaches the ADC rail; check sensitivity and electrode setup.", file=sys.stderr)
            print("Voltage and µS are nominal; no baseline subtraction, rectification or smoothing applied.")
            return 130 if capture.interrupted else 0
    except (OSError, AcquisitionError, ValueError) as error:
        print(f"Acquisition failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted before a valid capture was available.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
