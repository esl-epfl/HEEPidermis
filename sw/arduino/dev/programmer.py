"""SPI ELF programmer for HEEPidermis via Raspberry Pi Pico.

Writes loadable ELF segments (including zero-initialized BSS and necessary
32-bit boundary padding) to SRAM, with optional byte-exact SPI readback.

Compatible with rpi_pico_SPI_programmer_auto_reset_with_leds.ino
and rpi_pico_SPI_programmer_auto_reset.ino (with the 'R' readback command).
"""

import argparse
import struct
import time

import serial
from elftools.elf.elffile import ELFFile


RAM_BASE = 0x00000000
RAM_SIZE = 0x00008000

SOC_CTRL_BASE = 0x20000000
BOOT_EXIT_LOOP = SOC_CTRL_BASE + 0x0C
BOOT_ADDRESS = SOC_CTRL_BASE + 0x10

PICO_MAX_DATA = 512
DEFAULT_CHUNK_SIZE = 512

SERIAL_READ_TIMEOUT_S = 0.25
SERIAL_WRITE_TIMEOUT_S = 2.0
REPLY_TIMEOUT_S = 3.0
READY_BANNER = b"HEEPidermis programmer ready"


def align_down(value, alignment):
    return value & ~(alignment - 1)


def align_up(value, alignment): 
    return (value + alignment - 1) & ~(alignment - 1)


def format_range(start, end):
    if start == end:
        return f"0x{start:08X}"
    return f"0x{start:08X}-0x{end:08X}"


class HeepProgrammer:
    def __init__(self, port, baudrate=115200, chunk_size=DEFAULT_CHUNK_SIZE):
        if chunk_size <= 0:
            raise ValueError("Chunk size must be > 0")
        if chunk_size > PICO_MAX_DATA:
            raise ValueError(
                f"Chunk size {chunk_size} exceeds Pico buffer size "
                f"({PICO_MAX_DATA} bytes)"
            )
        if chunk_size % 4:
            raise ValueError("Chunk size must be a multiple of 4 bytes")

        self.chunk_size = chunk_size
        self.ser = serial.Serial(
            port,
            baudrate,
            timeout=SERIAL_READ_TIMEOUT_S,
            write_timeout=SERIAL_WRITE_TIMEOUT_S,
        )
        # The Pico may emit its startup banner either before or after this.
        time.sleep(0.3)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    def _read_line(self, deadline):
        """Read a complete CR/LF-terminated line across USB read timeouts."""
        buf = bytearray()
        while time.monotonic() < deadline:
            byte = self.ser.read(1)
            if not byte:
                continue
            if byte == b"\n":
                return bytes(buf).rstrip(b"\r")
            buf.extend(byte)

        if buf:
            raise TimeoutError(f"Timed out with partial Pico response: {bytes(buf)!r}")
        return None

    def _expect_ok(self, address):
        deadline = time.monotonic() + REPLY_TIMEOUT_S
        while time.monotonic() < deadline:
            reply = self._read_line(deadline)
            if reply is None:
                break
            if reply == READY_BANNER:
                continue
            if reply == b"OK":
                return
            if reply.startswith(b"ERR"):
                raise RuntimeError(
                    f"Pico error at 0x{address:08X}: "
                    f"{reply.decode(errors='replace')}"
                )
            raise RuntimeError(
                f"Unexpected Pico response at 0x{address:08X}: {reply!r}"
            )
        raise TimeoutError(f"Timed out waiting for Pico response at 0x{address:08X}")

    def _expect_reset_ok(self):
        deadline = time.monotonic() + REPLY_TIMEOUT_S
        while time.monotonic() < deadline:
            reply = self._read_line(deadline)
            if reply is None:
                break
            if reply == READY_BANNER:
                continue
            if reply == b"OK":
                return
            if reply.startswith(b"ERR"):
                raise RuntimeError(
                    "Pico CPU reset failed: " + reply.decode(errors="replace")
                )
            # Preserve intermediate RESET_DEBUG lines, if enabled.
            print("Pico: " + reply.decode(errors="replace"))
        raise TimeoutError("Timed out waiting for Pico CPU reset response")

    def _send_packet(self, packet):
        sent = self.ser.write(packet)
        self.ser.flush()
        if sent != len(packet):
            raise IOError(f"Short serial write: sent {sent}/{len(packet)} bytes")

    @staticmethod
    def _check_transfer(address, length):
        if address < 0 or address > 0xFFFFFFFF:
            raise ValueError(f"Invalid 32-bit address: 0x{address:X}")
        if address & 3:
            raise ValueError(f"Transfer address 0x{address:08X} is not 4-byte aligned")
        if length % 4:
            raise ValueError(f"Transfer length {length} is not a multiple of 4 bytes")
        if length > PICO_MAX_DATA:
            raise ValueError(
                f"Transfer too large ({length} bytes, max {PICO_MAX_DATA})"
            )
        if length and address > 0xFFFFFFFF - (length - 1):
            raise ValueError(
                f"Transfer range starting at 0x{address:08X} "
                "overflows 32-bit address space"
            )

    def write(self, address, data):
        data = bytes(data)
        self._check_transfer(address, len(data))
        if not data:
            return
        # PC -> Pico: little-endian address, length, and memory bytes.
        # Pico converts target-memory words to MSB-first SPI wire order.
        packet = b"W" + struct.pack("<IH", address, len(data)) + data
        self._send_packet(packet)
        # 'OK' confirms SPI transmission, not SRAM correctness.
        self._expect_ok(address)

    def write_block(self, address, data):
        data = bytes(data)
        if address < 0 or address > 0xFFFFFFFF:
            raise ValueError(f"Invalid 32-bit address: 0x{address:X}")
        if address & 3:
            raise ValueError(f"Block address 0x{address:08X} is not 4-byte aligned")
        if len(data) % 4:
            raise ValueError(f"Block length {len(data)} is not a multiple of 4 bytes")
        if data and address > 0xFFFFFFFF - (len(data) - 1):
            raise ValueError(
                f"Block range starting at 0x{address:08X} "
                "overflows 32-bit address space"
            )
        for offset in range(0, len(data), self.chunk_size):
            self.write(address + offset, data[offset:offset + self.chunk_size])

    def read(self, address, length):
        """Read exactly length bytes from HEEPidermis SRAM via the Pico 'R' command.

        Response: ASCII 'DATA\\r\\n' followed by *exactly* length raw bytes.
        Do not call readline() on the raw payload: it may contain any byte.
        """
        self._check_transfer(address, length)
        if not length:
            return b""

        self._send_packet(b"R" + struct.pack("<IH", address, length))

        deadline = time.monotonic() + REPLY_TIMEOUT_S
        while True:
            reply = self._read_line(deadline)
            if reply is None:
                raise TimeoutError(
                    f"Timed out waiting for Pico read header at 0x{address:08X}"
                )
            if reply == READY_BANNER:
                continue
            if reply.startswith(b"ERR"):
                raise RuntimeError(
                    f"Pico read error at 0x{address:08X}: "
                    f"{reply.decode(errors='replace')}"
                )
            if reply != b"DATA":
                raise RuntimeError(
                    f"Unexpected Pico read header at 0x{address:08X}: {reply!r}"
                )
            break

        # Read *exactly* length bytes. A serial.read() call can return fewer
        # bytes than requested, even without a protocol error.
        result = bytearray()
        deadline = time.monotonic() + REPLY_TIMEOUT_S
        while len(result) < length:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"SPI readback timed out at 0x{address:08X}: "
                    f"received {len(result)}/{length} bytes"
                )
            part = self.ser.read(length - len(result))
            if part:
                result.extend(part)

        return bytes(result)

    def verify_block(self, address, expected):
        """Check the entire programmed block; fail on the first byte mismatch."""
        expected = bytes(expected)
        if len(expected) % 4:
            raise ValueError("Verification block length must be a multiple of 4")
        for offset in range(0, len(expected), self.chunk_size):
            chunk = expected[offset:offset + self.chunk_size]
            actual = self.read(address + offset, len(chunk))
            if actual != chunk:
                for i, (wanted, found) in enumerate(zip(chunk, actual)):
                    if wanted != found:
                        bad_address = address + offset + i
                        raise RuntimeError(
                            f"Verification failed at 0x{bad_address:08X}:\n"
                            f"  expected: 0x{wanted:02X}\n"
                            f"  read:     0x{found:02X}"
                        )
                raise RuntimeError(
                    f"Verification failed: unexpected read length at "
                    f"0x{address + offset:08X}"
                )

    def write_u32(self, address, value):
        if value < 0 or value > 0xFFFFFFFF:
            raise ValueError(f"Invalid 32-bit value: 0x{value:X}")
        self.write(address, struct.pack("<I", value))

    def reset_cpu_to_bootrom(self):
        """Ask Pico ('X') to reset the CPU domain and wait in Boot ROM."""
        self.ser.reset_input_buffer()
        self._send_packet(b"X")
        self._expect_reset_ok()

    def run(self, entry):
        print(f"Setting boot address to 0x{entry:08X}")
        self.write_u32(BOOT_ADDRESS, entry)
        print("Releasing CPU")
        self.write_u32(BOOT_EXIT_LOOP, 1)


def load_elf(filename, strict_partial_words=False):
    """Build 32-bit-aligned SRAM write regions from loadable ELF segments.

    Each region includes PT_LOAD file contents, BSS zero-fill, and any
    necessary zero-padded bytes in partial boundary words. Conflicting
    overlapping segments are rejected.
    """
    image = bytearray(RAM_SIZE)
    defined = bytearray(RAM_SIZE)
    used_words = [False] * (RAM_SIZE // 4)

    with open(filename, "rb") as f:
        elf = ELFFile(f)
        if elf.elfclass != 32:
            raise RuntimeError(f"Expected a 32-bit ELF, got ELF{elf.elfclass}")
        if not elf.little_endian:
            raise RuntimeError("Expected a little-endian HEEPidermis ELF")
        if elf.header["e_machine"] != "EM_RISCV":
            raise RuntimeError(
                f"Expected a RISC-V ELF, got {elf.header['e_machine']}"
            )

        entry = int(elf.header["e_entry"])
        if not (0 <= entry <= 0xFFFFFFFF):
            raise RuntimeError(f"ELF entry point 0x{entry:X} is not a 32-bit address")

        for segment in elf.iter_segments():
            if segment["p_type"] != "PT_LOAD":
                continue

            address = int(segment["p_paddr"])
            vaddr = int(segment["p_vaddr"])
            filesz = int(segment["p_filesz"])
            memsz = int(segment["p_memsz"])
            if memsz == 0:
                continue
            if not (0 <= address <= 0xFFFFFFFF):
                raise RuntimeError(f"Invalid PT_LOAD physical address 0x{address:X}")
            if filesz > memsz:
                raise RuntimeError(
                    f"Malformed PT_LOAD at 0x{address:08X}: "
                    f"p_filesz ({filesz}) > p_memsz ({memsz})"
                )

            segment_end = address + memsz
            if segment_end > 0x100000000:
                raise RuntimeError(
                    f"PT_LOAD at 0x{address:08X} overflows 32-bit address space"
                )
            if address < RAM_BASE or segment_end > RAM_BASE + RAM_SIZE:
                raise RuntimeError(
                    f"Segment 0x{address:08X}-0x{segment_end - 1:08X} "
                    "is outside SRAM"
                )
            if address != vaddr:
                print(
                    f"Warning: PT_LOAD p_paddr=0x{address:08X} differs from "
                    f"p_vaddr=0x{vaddr:08X}; using p_paddr"
                )

            file_data = bytes(segment.data())
            if len(file_data) != filesz:
                raise RuntimeError(
                    f"PT_LOAD at 0x{address:08X}: expected {filesz} "
                    f"file bytes, got {len(file_data)}"
                )
            mem_data = file_data + b"\x00" * (memsz - filesz)
            start = address - RAM_BASE
            end = start + memsz

            for i, value in enumerate(mem_data):
                idx = start + i
                if defined[idx] and image[idx] != value:
                    raise RuntimeError(
                        "Conflicting PT_LOAD overlap at SRAM address "
                        f"0x{RAM_BASE + idx:08X}"
                    )
                image[idx] = value
                defined[idx] = 1

            first_word = align_down(start, 4) // 4
            last_word = align_up(end, 4) // 4
            for word in range(first_word, last_word):
                used_words[word] = True

    if not any(used_words):
        raise RuntimeError("ELF contains no non-empty PT_LOAD segments in SRAM")

    padding_indices = []
    for word, used in enumerate(used_words):
        if used:
            base = word * 4
            for idx in range(base, base + 4):
                if not defined[idx]:
                    padding_indices.append(idx)

    if padding_indices:
        ranges = []
        range_start = previous = padding_indices[0]
        for idx in padding_indices[1:]:
            if idx != previous + 1:
                ranges.append((range_start, previous))
                range_start = idx
            previous = idx
        ranges.append((range_start, previous))
        description = ", ".join(
            format_range(RAM_BASE + start, RAM_BASE + end)
            for start, end in ranges
        )
        message = (
            "ELF requires zero-padding byte(s) outside its defined memory image "
            "because the SPI slave writes full 32-bit words: " + description
        )
        if strict_partial_words:
            raise RuntimeError(message)
        print(f"Warning: {message}")

    regions = []
    word = 0
    while word < len(used_words):
        if not used_words[word]:
            word += 1
            continue
        first = word
        while word < len(used_words) and used_words[word]:
            word += 1
        regions.append((
            RAM_BASE + first * 4,
            bytes(image[first * 4:word * 4]),
        ))

    return entry, regions


def main():
    parser = argparse.ArgumentParser(
        description="Program HEEPidermis SRAM through Raspberry Pi Pico SPI"
    )
    parser.add_argument("elf", help="HEEPidermis ELF file")
    parser.add_argument(
        "--port", required=True,
        help="Pico serial port, e.g. COM6 or /dev/ttyACM0",
    )
    parser.add_argument(
        "--chunk", type=int, default=DEFAULT_CHUNK_SIZE,
        help=f"USB/SPI block size: multiple of 4, <= {PICO_MAX_DATA} "
             f"(default: {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--run", action="store_true",
        help="Start the CPU after programming (and verification, if requested)",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Read back all programmed SRAM regions and compare byte-for-byte",
    )
    parser.add_argument(
        "--strict-partial-words", action="store_true",
        help="Reject ELF segments requiring zero-padded boundary bytes",
    )
    parser.add_argument(
        "--no-reset", action="store_true",
        help="Do not reset the CPU to Boot ROM before programming",
    )
    args = parser.parse_args()

    entry, regions = load_elf(
        args.elf, strict_partial_words=args.strict_partial_words
    )
    if args.run and not (RAM_BASE <= entry < RAM_BASE + RAM_SIZE):
        raise RuntimeError(f"ELF entry point 0x{entry:08X} is outside SRAM")

    total = sum(len(data) for _, data in regions)
    print(f"ELF entry point: 0x{entry:08X}")
    print(f"Bytes to write:  {total}")
    for address, data in regions:
        print(
            f"  0x{address:08X} - 0x{address + len(data) - 1:08X} "
            f"({len(data)} bytes)"
        )

    programmer = HeepProgrammer(args.port, chunk_size=args.chunk)
    try:
        if not args.no_reset:
            print("Resetting CPU to Boot ROM ...")
            programmer.reset_cpu_to_bootrom()
            print("CPU is waiting in Boot ROM")

        written = 0
        for address, data in regions:
            print(f"Writing 0x{address:08X} ...")
            programmer.write_block(address, data)
            written += len(data)
            print(f"  {written}/{total} bytes")
        print("Programming complete")

        # Verify *after all writes*, *before --run*. The CPU therefore
        # cannot modify SRAM between programming and comparison (unless
        # --no-reset is used, in which case the user controls that risk).
        if args.verify:
            print("Verifying memory ...")
            verified = 0
            for address, data in regions:
                programmer.verify_block(address, data)
                verified += len(data)
                print(f"  {verified}/{total} bytes")
            print("Verification successful")

        if args.run:
            programmer.run(entry)
            print("CPU started")
    finally:
        programmer.close()


if __name__ == "__main__":
    main()
