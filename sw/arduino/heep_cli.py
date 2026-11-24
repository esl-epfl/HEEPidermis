#!/usr/bin/env python3
"""CLI frontend to the HEEPidermis external SPI programmer SDK.

From repository root:
    python3 sw/arduino/heep_cli.py clock status
    python3 sw/arduino/heep_cli.py clock set 1000000
    python3 sw/arduino/heep_cli.py clock on
    python3 sw/arduino/heep_cli.py reset
    python3 sw/arduino/heep_cli.py program --verify --run

The ASIC clock is *never* enabled implicitly.
"""

import argparse
import os
import sys
from pathlib import Path

from heep_sdk import HEEPidermis

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ELF = REPO_ROOT / "sw" / "build" / "main.elf"


def build_parser():
    parser = argparse.ArgumentParser(description="Control HEEPidermis through an external SPI programmer")
    parser.add_argument(
        "--port", "-p", default=os.environ.get("HEEP_SPI_PORT", os.environ.get("HEEP_PICO_PORT", "/dev/ttyACM0")),
        help="SPI programmer serial port (default HEEP_SPI_PORT or /dev/ttyACM0)",
    )
    parser.add_argument("--chunk", type=int, default=512,
                        help="SPI transfer size (multiple of 4, maximum 512)")
    commands = parser.add_subparsers(dest="command", required=True)

    clock = commands.add_parser("clock", help="Control the Pico GPIO21 ASIC clock")
    clock_actions = clock.add_subparsers(dest="clock_action", required=True)
    clock_actions.add_parser("on", help="Enable clock")
    clock_actions.add_parser("off", help="Disable clock (GPIO21 and GPIO8 high-Z)")
    clock_actions.add_parser("status", help="Show current clock status")
    freq_cmd = clock_actions.add_parser("set", help="Set clock frequency in Hz (normally only when OFF)")
    freq_cmd.add_argument("hz", type=int)

    commands.add_parser("reset", help="Reset ASIC CPU domain to Boot ROM")
    commands.add_parser("vco-enable", help="Enable VCOp over SPI, verify, then stop the Pico clock (CPU must be in Boot ROM)")

    program = commands.add_parser("program", help="Program a RISC-V ELF into SRAM")
    program.add_argument("elf", nargs="?", type=Path, default=DEFAULT_ELF,
                         help="ELF image (default: sw/build/main.elf)")
    program.add_argument("--verify", action="store_true", help="Read back and verify bytes")
    program.add_argument("--run", action="store_true", help="Start the CPU after programming")
    program.add_argument("--no-reset", action="store_true", help="Do not reset CPU first")
    program.add_argument("--strict-partial-words", action="store_true",
                         help="Reject ELF segments requiring word-boundary padding")

    run = commands.add_parser("run", help="Start CPU at a specified address")
    run.add_argument("entry", type=lambda s: int(s, 0), help="Entry address, e.g. 0x180")

    read32 = commands.add_parser("read32", help="Read one 32-bit word")
    read32.add_argument("address", type=lambda s: int(s, 0))

    write32 = commands.add_parser("write32", help="Write one 32-bit word")
    write32.add_argument("address", type=lambda s: int(s, 0))
    write32.add_argument("value", type=lambda s: int(s, 0))
    return parser


def execute(args):
    with HEEPidermis(args.port, chunk_size=args.chunk) as heep:
        if args.command == "clock":
            if args.clock_action == "on":
                heep.clock_on()
                print("ASIC clock enabled")
            elif args.clock_action == "off":
                heep.clock_off()
                print("ASIC clock disabled")
            elif args.clock_action == "status":
                status = heep.clock_status()
                print(f"ASIC clock: {'ON' if status.enabled else 'OFF'}, {status.frequency_hz} Hz")
            elif args.clock_action == "set":
                actual = heep.clock_set_frequency(args.hz)
                print(f"ASIC clock configured to {actual} Hz")
        elif args.command == "vco-enable":
            value = heep.enable_vcop()
            print(f"VCOp enabled; ENABLE readback: 0x{value:08X}; Pico clock off")
        elif args.command == "reset":
            heep.reset()
            print("CPU reset to Boot ROM")
        elif args.command == "program":
            if not args.elf.is_file():
                raise FileNotFoundError(f"ELF not found: {args.elf}")
            result = heep.program_elf(
                args.elf,
                verify=args.verify,
                run=args.run,
                reset=not args.no_reset,
                strict_partial_words=args.strict_partial_words,
            )
            print(f"Programmed {result.bytes_written} bytes in {result.regions_written} SRAM regions")
            if result.verified:
                print("Readback verification successful")
            if result.started:
                print(f"CPU started at 0x{result.entry_point:08X}")
        elif args.command == "run":
            heep.run(args.entry)
            print(f"CPU started at 0x{args.entry:08X}")
        elif args.command == "read32":
            value = heep.read_u32(args.address)
            print(f"0x{args.address:08X}: 0x{value:08X}")
        elif args.command == "write32":
            heep.write_u32(args.address, args.value)
            print(f"0x{args.address:08X} <- 0x{args.value:08X}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        execute(args)
    except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
