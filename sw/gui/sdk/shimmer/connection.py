"""Shimmer link layer: Bluetooth SPP and dock UART without terminal commands.

The Shimmer3 GSR+ with LogAndStream firmware speaks a single command byte
protocol over Bluetooth SPP and over the dock UART.  This module finds
candidate transports and, for Bluetooth, performs discovery, pairing (legacy
PIN 1234) and RFCOMM binding.  Binding an RFCOMM node requires root, so the
sequence of attempts is:

1. bind directly (works when rfcomm is setuid, or the node already exists);
2. ``pkexec rfcomm bind …`` — a desktop polkit password popup, no terminal;
3. hand the exact command to the GUI, which may show it to the user and let
   them run it manually before pressing Accept (see the ``request_privilege``
   callback, called from the acquisition worker thread).
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import time

import serial


BAUDRATE = 115200
PIN = "1234"  # Legacy Shimmer pairing PIN.
SHIMMER_NAME_PREFIXES = ("shimmer",)
DOCK_GLOB = "/dev/serial/by-id/*SHIMMER_DOCK*"


class LinkError(RuntimeError):
    """Raised when no transport to a Shimmer could be established."""


def dock_ports():
    """Serial paths for a plugged Shimmer dock (UART channel first)."""
    paths = sorted(Path("/dev/serial").glob(DOCK_GLOB.split("/", 3)[-1]))
    # by-id names sort as …if00-port0 (Shimmer UART) then …if01-port0 (BSL).
    return [str(path) for path in paths]


def bluetooth_scan(timeout=12.0):
    """Scan for advertising Shimmer devices; return [(mac, name)], best first."""
    try:
        process = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, bufsize=1)
    except OSError as error:
        raise LinkError(f"bluetoothctl is not available: {error}") from error
    devices, pattern = {}, re.compile(r"Device ([0-9A-F:]{17})\s*(?:Name|Alias):?\s*(.*)")
    deadline = time.monotonic() + timeout
    try:
        process.stdin.write("scan on\n")
        process.stdin.flush()
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            match = pattern.search(line)
            if match and match.group(2).strip().lower().startswith(SHIMMER_NAME_PREFIXES):
                devices[match.group(1)] = match.group(2).strip()
    finally:
        try:
            process.stdin.write("quit\n")
            process.stdin.flush()
        except OSError:
            pass
        process.terminate()
        process.wait(timeout=5)
    ordered = sorted(devices.items(), key=lambda item: (len(item[1]), item[1]))
    return [(mac, name) for mac, name in ordered]


def paired_devices():
    """Bluetooth devices this host has already paired with."""
    listing = ""
    for command in (["bluetoothctl", "paired-devices"], ["bluetoothctl", "devices", "Paired"]):
        try:
            listing = subprocess.run(command, capture_output=True, text=True, timeout=10).stdout
            if listing.strip():
                break
        except (OSError, subprocess.SubprocessError):
            continue
    devices = {}
    for mac, name in re.findall(r"Device ([0-9A-F:]{17})\s*(.*)", listing):
        if name.strip().lower().startswith(SHIMMER_NAME_PREFIXES):
            devices[mac] = name.strip()
    return devices


def pair(mac, log=lambda message: None, timeout=45.0):
    """Pair and trust a Shimmer over Bluetooth; returns True when paired.

    Drives one interactive bluetoothctl session: scans until the device shows
    up, pairs, answers the legacy PIN prompt, then trusts the device so future
    connections never ask again.
    """
    if mac in paired_devices():
        return True
    log(f"Pairing with {mac} (PIN {PIN})…")
    try:
        process = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
    except OSError as error:
        raise LinkError(f"bluetoothctl is not available: {error}") from error
    deadline, seen, paired = time.monotonic() + timeout, False, False
    try:
        for step in ("agent KeyboardDisplay\n", "default-agent\n", "scan on\n"):
            process.stdin.write(step)
            process.stdin.flush()
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            text = line.strip()
            if mac in text and ("NEW" in line or "CHG" in line or "Device" in text):
                seen = True
            if seen and "Attempting to pair" not in text and not paired:
                if not text.startswith("["):
                    continue
                process.stdin.write(f"pair {mac}\n")
                process.stdin.flush()
                paired = True  # A pair attempt is now in flight.
            if "Request PIN code" in text:
                process.stdin.write(PIN + "\n")
                process.stdin.flush()
            if "Pairing successful" in text or "AlreadyExists" in text:
                process.stdin.write(f"trust {mac}\n")
                process.stdin.flush()
                log("Paired and trusted.")
                return True
            if "Failed to pair" in text or "br-connection" in text:
                raise LinkError(f"Pairing with {mac} failed: {text}")
    finally:
        try:
            process.stdin.write("quit\n")
            process.stdin.flush()
        except OSError:
            pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    return mac in paired_devices()


def rfcomm_entries():
    """Existing RFCOMM bindings as {mac: device_path}."""
    try:
        listing = subprocess.run(["rfcomm"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return {match.group(2): f"/dev/{match.group(1)}" for match in
            (re.match(r"(rfcomm\d+): ([0-9A-F:]{17}) channel \d+", line) for line in listing.splitlines())
            if match}


def bind_rfcomm(mac, request_privilege=None, log=lambda message: None):
    """Bind an RFCOMM node for *mac*, escalating privileges as needed.

    ``request_privilege(command)`` is called only as a last resort: the GUI may
    show the command and let the user run it, then return True to retry.
    """
    entries = rfcomm_entries()
    if mac in entries:
        return entries[mac]
    index = next((i for i in range(10) if f"rfcomm{i}" not in
                  {Path(path).name for path in entries.values()}), 0)
    command = ["rfcomm", "bind", str(index), mac, "1"]
    log(f"Binding {Path('/dev') / f'rfcomm{index}'}…")
    attempts = [command]
    binary = shutil.which("rfcomm")
    if binary:
        attempts.append(["pkexec", binary, *command[1:]])
    for attempt in attempts:
        try:
            result = subprocess.run(attempt, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            log(f"Bind attempt failed: {error}")
            continue
        if result.returncode == 0:
            return f"/dev/rfcomm{index}"
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        log(f"Bind attempt failed: {detail[0] if detail else result.returncode}")
    if request_privilege is not None and request_privilege(" ".join(attempts[0])):
        result = subprocess.run(attempts[0], capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return f"/dev/rfcomm{index}"
        raise LinkError(f"rfcomm bind still failed: {(result.stderr or result.stdout).strip()}")
    raise LinkError("Binding the Bluetooth serial port requires administrator rights.")


def probe(path, timeout=3.0):
    """True when a LogAndStream Shimmer answers a version query on *path*."""
    try:
        with serial.Serial(path, BAUDRATE, timeout=timeout) as port:
            port.reset_input_buffer()
            port.write(bytes((0x3F,)))  # GET_SHIMMER_VERSION
            time.sleep(0.4)
            reply = port.read(16)
    except (OSError, serial.SerialException):
        return False
    return bytes((0xFF, 0x25)) in reply or (len(reply) >= 2 and reply[0] == 0xFF and reply[1] == 0x25)


def open_link(device, request_privilege=None, log=lambda message: None):
    """Return (serial_path, transport) for a device entry from ``find``."""
    mode = device.get("mode", "auto")
    if mode == "dock":
        return device["port"], "dock"
    if mode == "bluetooth":
        if not pair(device["address"], log):
            raise LinkError(f"Could not pair with {device.get('name', device['address'])}. "
                            "Make sure the Shimmer is advertising (blinking Bluetooth LED).")
        path = bind_rfcomm(device["address"], request_privilege, log)
        return path, "bluetooth"
    raise LinkError(f"Unknown Shimmer transport: {mode}")


def find(preferred="", timeout=12.0, log=lambda message: None, mode="auto"):
    """Locate candidate Shimmer devices on the requested transports.

    *mode* selects the transport: "dock" (USB UART only), "bluetooth" only, or
    "auto" (docks first, then Bluetooth).  Bluetooth candidates combine
    devices caught advertising during a scan with devices this host has
    already paired with — a paired Shimmer accepts outgoing connections even
    while it is not advertising.  When *preferred* (a Bluetooth address or
    dock path) matches, it is placed first.
    """
    if mode not in ("auto", "dock", "bluetooth"):
        raise LinkError(f"Unknown connection mode: {mode}")
    candidates, preferred = [], preferred.strip()
    if mode in ("auto", "dock"):
        for port in dock_ports():
            candidates.append({"mode": "dock", "port": port, "name": Path(port).name})
    if mode in ("auto", "bluetooth"):
        scanned = dict(bluetooth_scan(timeout))
        for mac, name in {**paired_devices(), **scanned}.items():
            candidates.append({"mode": "bluetooth", "address": mac,
                               "name": name, "advertising": mac in scanned})
        # Devices seen advertising right now are the most likely to connect.
        candidates.sort(key=lambda device: not device.get("advertising", device.get("port")))
    if preferred:
        matches = [device for device in candidates
                   if preferred in (device.get("address"), device.get("port"))]
        candidates = matches + [device for device in candidates if device not in matches]
    return candidates
