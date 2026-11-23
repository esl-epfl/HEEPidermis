"""Host-side SDK for the HEEPidermis Raspberry Pi Pico programmer.

Firmware protocol (USB CDC, 115200 baud):
  X                    CPU-domain reset to Boot ROM -> OK / ERR_RESET
  W + <u32 LE><u16 LE> + bytes  -> OK / ERR...
  R + <u32 LE><u16 LE>          -> DATA\r\n + raw bytes / ERR...
  C1                   clock on               -> OK CLK ON / ERR...
  C0                   clock off              -> OK CLK OFF
  C?                   clock status           -> CLK ON|OFF <Hz> Hz
  CF + <u32 LE>        configure clock        -> OK CLK SET <Hz> Hz / ERR...
  V                    enable VCOp, clock off -> OK VCO <hex> / ERR_VCO <hex>

Frequency changes while clock is on are rejected by firmware unless its
ALLOW_LIVE_CLOCK_FREQUENCY_CHANGE compile-time flag is enabled. The SDK never
turns on the Pico clock implicitly; call clock_on() explicitly before SPI
access if the ASIC depends on the Pico clock.
"""

from __future__ import annotations

import re
import struct
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

RAM_BASE = 0x00000000
RAM_SIZE = 0x00008000
SOC_CTRL_BASE = 0x20000000
BOOT_EXIT_LOOP = SOC_CTRL_BASE + 0x0C
BOOT_ADDRESS = SOC_CTRL_BASE + 0x10
# config/cheep_configs.hjson: AO external peripherals + VCO decoder + ENABLE.
VCO_ENABLE = 0x20070000 + 0x1000 + 0x0C
VCOP_ENABLE_MASK = 1 << 0

MAX_TRANSFER = 512
DEFAULT_CHUNK_SIZE = 512
READY_BANNER = b"HEEPidermis programmer ready"
REPLY_TIMEOUT_S = 3.0
SERIAL_READ_TIMEOUT_S = 0.25
SERIAL_WRITE_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class ClockStatus:
    enabled: bool
    frequency_hz: int  # Actual output frequency if ON, requested frequency if OFF.


@dataclass(frozen=True)
class ProgramResult:
    entry_point: int
    bytes_written: int
    regions_written: int
    verified: bool
    started: bool


class PicoProtocolError(RuntimeError):
    """The Pico returned ERR... or a malformed response."""


class HEEPidermis:
    """Reusable USB/SPI control interface to the HEEPidermis ASIC.

    Example::

        with HEEPidermis('/dev/ttyACM0') as heep:
            heep.clock_set_frequency(1_000_000)  # Only while clock OFF.
            heep.clock_on()
            result = heep.program_elf('app.elf', verify=True, run=True)
            heep.clock_off()

    The Pico's X command resets the CPU domain, not the whole ASIC board.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        *,
        serial_instance=None,
    ):
        if not (0 < chunk_size <= MAX_TRANSFER and chunk_size % 4 == 0):
            raise ValueError("chunk_size must be a multiple of 4 in [4, 512]")
        self.chunk_size = chunk_size

        if serial_instance is not None:
            # Supports a simulated serial port for protocol unit tests.
            self.ser = serial_instance
        else:
            try:
                import serial
            except ImportError as exc:
                raise RuntimeError("Install pyserial: pip install pyserial") from exc
            self.ser = serial.Serial(
                port,
                baudrate,
                timeout=SERIAL_READ_TIMEOUT_S,
                write_timeout=SERIAL_WRITE_TIMEOUT_S,
            )
            time.sleep(0.3)  # USB CDC startup as in the existing programmer.
            self.ser.reset_input_buffer()

    def __enter__(self) -> "HEEPidermis":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.ser.close()

    def _send(self, packet: bytes) -> None:
        count = self.ser.write(packet)
        self.ser.flush()
        if count != len(packet):
            raise OSError(f"Short USB write: {count}/{len(packet)} bytes")

    def _read_line(self, deadline: float) -> Optional[bytes]:
        buf = bytearray()
        while time.monotonic() < deadline:
            value = self.ser.read(1)
            if not value:
                continue
            if value == b"\n":
                return bytes(buf).rstrip(b"\r")
            buf.extend(value)
            if len(buf) > 256:
                raise PicoProtocolError("Pico response line exceeds 256 bytes")
        if buf:
            raise TimeoutError(f"Incomplete Pico response: {bytes(buf)!r}")
        return None

    def _response_line(self, context: str) -> bytes:
        deadline = time.monotonic() + REPLY_TIMEOUT_S
        while time.monotonic() < deadline:
            reply = self._read_line(deadline)
            if reply is None:
                break
            if reply == READY_BANNER or not reply:
                continue
            if reply.startswith(b"ERR"):
                raise PicoProtocolError(
                    f"{context}: {reply.decode('ascii', errors='replace')}"
                )
            return reply
        raise TimeoutError(f"Timed out waiting for Pico response ({context})")

    def _expect(self, packet: bytes, expected: bytes, context: str) -> None:
        self._send(packet)
        reply = self._response_line(context)
        if reply != expected:
            raise PicoProtocolError(
                f"{context}: expected {expected!r}, received {reply!r}"
            )

    # -------- Clock control (Pico GPIO21 / GPOUT0) --------

    def clock_on(self) -> None:
        self._expect(b"C1", b"OK CLK ON", "clock_on")

    def clock_off(self) -> None:
        self._expect(b"C0", b"OK CLK OFF", "clock_off")

    def clock_status(self) -> ClockStatus:
        self._send(b"C?")
        reply = self._response_line("clock_status")
        match = re.fullmatch(rb"CLK (ON|OFF) ([0-9]+) Hz", reply)
        if not match:
            raise PicoProtocolError(f"Invalid clock status response: {reply!r}")
        return ClockStatus(
            enabled=(match.group(1) == b"ON"),
            frequency_hz=int(match.group(2)),
        )

    def clock_set_frequency(self, frequency_hz: int) -> int:
        """Set the Pico clock frequency in Hz and return the firmware report.

        Default firmware policy rejects this while the clock is running.
        """
        if not isinstance(frequency_hz, int) or isinstance(frequency_hz, bool):
            raise TypeError("frequency_hz must be an integer")
        if not 1 <= frequency_hz <= 0xFFFFFFFF:
            raise ValueError("frequency_hz must be in [1, 4294967295]")
        self._send(b"CF" + struct.pack("<I", frequency_hz))
        reply = self._response_line("clock_set_frequency")
        match = re.fullmatch(rb"OK CLK SET ([0-9]+) Hz", reply)
        if not match:
            raise PicoProtocolError(f"Invalid clock frequency response: {reply!r}")
        return int(match.group(1))

    # -------- Memory accesses (SPI) --------

    @staticmethod
    def _check_transfer(address: int, length: int) -> None:
        if not isinstance(address, int) or not 0 <= address <= 0xFFFFFFFF:
            raise ValueError(f"Invalid 32-bit address: {address!r}")
        if address % 4:
            raise ValueError(f"Unaligned SPI address: 0x{address:08X}")
        if length < 0 or length > MAX_TRANSFER or length % 4:
            raise ValueError("SPI length must be a multiple of 4, <= 512 bytes")
        if length and address > 0xFFFFFFFF - (length - 1):
            raise ValueError("SPI transfer overflows 32-bit address space")

    def write(self, address: int, data: bytes) -> None:
        data = bytes(data)
        self._check_transfer(address, len(data))
        if data:
            # Firmware serial framing uses little-endian address/length.
            self._expect(
                b"W" + struct.pack("<IH", address, len(data)) + data,
                b"OK", f"write at 0x{address:08X}",
            )
            # OK means the transaction was sent, not verified in SRAM.

    def write_block(self, address: int, data: bytes) -> None:
        data = bytes(data)
        if address % 4 or len(data) % 4:
            raise ValueError("SPI write block address and length must align to 4")
        if not 0 <= address <= 0xFFFFFFFF or (data and address > 0xFFFFFFFF - len(data) + 1):
            raise ValueError("SPI write block outside 32-bit address space")
        for offset in range(0, len(data), self.chunk_size):
            self.write(address + offset, data[offset:offset + self.chunk_size])

    def read(self, address: int, length: int) -> bytes:
        self._check_transfer(address, length)
        if not length:
            return b""
        self._send(b"R" + struct.pack("<IH", address, length))
        header = self._response_line(f"read at 0x{address:08X}")
        if header != b"DATA":
            raise PicoProtocolError(f"Invalid readback header: {header!r}")

        data = bytearray()
        deadline = time.monotonic() + REPLY_TIMEOUT_S
        while len(data) < length and time.monotonic() < deadline:
            received = self.ser.read(length - len(data))
            if received:
                data.extend(received)
        if len(data) != length:
            raise TimeoutError(
                f"Readback timed out at 0x{address:08X}: "
                f"{len(data)}/{length} bytes"
            )
        return bytes(data)

    def read_block(self, address: int, length: int) -> bytes:
        if address % 4 or length < 0 or length % 4:
            raise ValueError("SPI read block address and length must align to 4")
        if not 0 <= address <= 0xFFFFFFFF or (length and address > 0xFFFFFFFF - length + 1):
            raise ValueError("SPI read block outside 32-bit address space")
        return b"".join(
            self.read(address + offset, min(self.chunk_size, length - offset))
            for offset in range(0, length, self.chunk_size)
        )

    def verify_block(self, address: int, expected: bytes) -> None:
        expected = bytes(expected)
        if address % 4 or len(expected) % 4:
            raise ValueError("Verification block must align to 4 bytes")
        for offset in range(0, len(expected), self.chunk_size):
            chunk = expected[offset:offset + self.chunk_size]
            actual = self.read(address + offset, len(chunk))
            if actual != chunk:
                for index, (wanted, found) in enumerate(zip(chunk, actual)):
                    if wanted != found:
                        raise PicoProtocolError(
                            f"Verification failed at 0x{address + offset + index:08X}: "
                            f"expected 0x{wanted:02X}, read 0x{found:02X}"
                        )
                raise PicoProtocolError("Verification failed: unexpected read length")

    def write_u32(self, address: int, value: int) -> None:
        if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("Expected an unsigned 32-bit integer")
        self.write(address, struct.pack("<I", value))

    def read_u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    # -------- Boot control --------

    def reset_cpu_to_bootrom(self) -> None:
        """Reset CPU domain to Boot ROM, not the entire ASIC/board."""
        # Match the existing programmer's reset behavior.
        self.ser.reset_input_buffer()
        self._expect(b"X", b"OK", "reset_cpu_to_bootrom")

    def enable_vcop(self) -> int:
        """Enable VCOp over SPI, verify ENABLE, then stop the Pico clock.

        Requires a working ASIC clock and the CPU waiting in Boot ROM so
        firmware cannot race the read-modify-write. The Pico firmware performs
        the read-modify-write and readback; it turns its clock off only if the
        readback matches (an ERR_VCO reply leaves the clock on). An already
        enabled oscillator is never toggled off. Returns the ENABLE readback.
        """
        self._send(b"V")
        reply = self._response_line("enable_vcop")  # ERR_VCO raises here.
        match = re.fullmatch(rb"OK VCO ([0-9A-Fa-f]+)", reply)
        if not match:
            raise PicoProtocolError(f"Invalid VCO enable response: {reply!r}")
        return int(match.group(1), 16)

    def reset(self) -> None:
        """Alias for reset_cpu_to_bootrom()."""
        self.reset_cpu_to_bootrom()

    def run(self, entry_point: int) -> None:
        if not isinstance(entry_point, int) or not 0 <= entry_point <= 0xFFFFFFFF:
            raise ValueError("entry_point must be a 32-bit address")
        self.write_u32(BOOT_ADDRESS, entry_point)
        self.write_u32(BOOT_EXIT_LOOP, 1)

    def program_elf(
        self,
        path: str | Path,
        *,
        verify: bool = False,
        run: bool = False,
        reset: bool = True,
        strict_partial_words: bool = False,
    ) -> ProgramResult:
        """Program all ELF PT_LOAD sections to SRAM; optionally verify and run.

        This does not enable the clock. Call clock_on() first if required.
        """
        entry, regions = load_elf(path, strict_partial_words=strict_partial_words)
        if run and not RAM_BASE <= entry < RAM_BASE + RAM_SIZE:
            raise ValueError(f"ELF entry point 0x{entry:08X} outside SRAM")

        if reset:
            self.reset()
        for address, payload in regions:
            self.write_block(address, payload)
        if verify:
            for address, payload in regions:
                self.verify_block(address, payload)
        if run:
            self.run(entry)
        return ProgramResult(
            entry_point=entry,
            bytes_written=sum(len(payload) for _, payload in regions),
            regions_written=len(regions),
            verified=verify,
            started=run,
        )


def _align_up(value: int, alignment: int = 4) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def load_elf(path: str | Path, *, strict_partial_words: bool = False):
    """Return (entry, [(address, aligned_bytes), ...]) from a RISC-V ELF.

    As in the existing programmer: includes PT_LOAD file data, BSS zero-fill,
    rejects contradictory overlaps, and pads boundary words with zeros.
    """
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError as exc:
        raise RuntimeError("Install pyelftools: pip install pyelftools") from exc

    image = bytearray(RAM_SIZE)
    defined = bytearray(RAM_SIZE)
    used_words = [False] * (RAM_SIZE // 4)
    with open(path, "rb") as f:
        elf = ELFFile(f)
        if elf.elfclass != 32:
            raise ValueError(f"Expected 32-bit ELF, got ELF{elf.elfclass}")
        if not elf.little_endian:
            raise ValueError("Expected a little-endian ELF")
        if elf.header["e_machine"] != "EM_RISCV":
            raise ValueError(f"Expected RISC-V ELF, got {elf.header['e_machine']}")
        entry = int(elf.header["e_entry"])
        if not 0 <= entry <= 0xFFFFFFFF:
            raise ValueError("ELF entry point is not a 32-bit address")

        for segment in elf.iter_segments():
            if segment["p_type"] != "PT_LOAD":
                continue
            addr = int(segment["p_paddr"])
            vaddr = int(segment["p_vaddr"])
            filesz = int(segment["p_filesz"])
            memsz = int(segment["p_memsz"])
            if memsz == 0:
                continue
            if not 0 <= addr <= 0xFFFFFFFF or filesz > memsz:
                raise ValueError(f"Invalid PT_LOAD at 0x{addr:X}")
            if addr < RAM_BASE or addr + memsz > RAM_BASE + RAM_SIZE:
                raise ValueError(f"PT_LOAD at 0x{addr:08X} exceeds HEEPidermis SRAM")
            if vaddr != addr:
                warnings.warn(
                    f"PT_LOAD p_paddr=0x{addr:08X} differs from "
                    f"p_vaddr=0x{vaddr:08X}; using p_paddr",
                    stacklevel=2,
                )
            data = bytes(segment.data())
            if len(data) != filesz:
                raise ValueError(f"Incorrect PT_LOAD file length at 0x{addr:08X}")
            data += b"\x00" * (memsz - filesz)
            start = addr - RAM_BASE
            end = start + memsz
            for i, value in enumerate(data):
                idx = start + i
                if defined[idx] and image[idx] != value:
                    raise ValueError(f"Conflicting PT_LOAD overlap at 0x{addr + i:08X}")
                image[idx] = value
                defined[idx] = 1
            for word in range((start & ~3) // 4, _align_up(end) // 4):
                used_words[word] = True

    if not any(used_words):
        raise ValueError("ELF has no non-empty PT_LOAD segments in SRAM")

    padding = [
        i
        for w, used in enumerate(used_words) if used
        for i in range(4 * w, 4 * w + 4) if not defined[i]
    ]
    if padding:
        ranges = []
        first = last = padding[0]
        for idx in padding[1:]:
            if idx != last + 1:
                ranges.append((first, last))
                first = idx
            last = idx
        ranges.append((first, last))
        description = ", ".join(
            f"0x{RAM_BASE + lo:08X}-0x{RAM_BASE + hi:08X}"
            for lo, hi in ranges
        )
        message = f"ELF requires zero padding outside its image: {description}"
        if strict_partial_words:
            raise ValueError(message)
        warnings.warn(message, stacklevel=2)

    regions = []
    word = 0
    while word < len(used_words):
        if not used_words[word]:
            word += 1
            continue
        first = word
        while word < len(used_words) and used_words[word]:
            word += 1
        regions.append((RAM_BASE + first * 4, bytes(image[first * 4:word * 4])))
    return entry, regions