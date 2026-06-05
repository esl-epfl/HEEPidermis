import re
import argparse
from pathlib import Path


# ============================================================
# USER SETTINGS — CHANGE THESE ONLY
# ============================================================

_SCRIPT_DIR = Path(__file__).parent

INPUT_TXT_FILE = _SCRIPT_DIR / "conductance.txt"

OUTPUT_FILE = _SCRIPT_DIR / "input_signal.h"

START_INDEX = 499999        # inclusive (0-indexed; matches line_start=500000 in resistor.sv)
END_INDEX   = 600000         # exclusive (0-indexed; matches line_end=600000 in resistor.sv)

# Divide raw values by this factor before writing.
# conductance.txt is in pS; dividing by 1000 converts to nS,
# which keeps intermediate Q14 products within int32 range.
DIVISOR = 1000

# Optional pre-FE downsampling. Use this for GT conductance when the simulation
# FE runs slower than conductance.txt. For example, 200 Hz GT vs 20 Hz sim uses
# DOWNSAMPLE_FACTOR = 10.
DOWNSAMPLE_FACTOR = 2
DOWNSAMPLE_MODE = "pick"   # "pick" or "mean"
DOWNSAMPLE_OFFSET = 0      # source samples to skip before downsampling

# Optional pre-FE oversampling. Applied after optional downsampling. For
# example, factor 10 inserts 9 held/interpolated samples between each pair of
# input samples and records sample_step = DOWNSAMPLE_FACTOR / 10.
OVERSAMPLE_FACTOR = 1
OVERSAMPLE_MODE = "hold"   # "hold"/"zoh" or "linear"

ARRAY_NAME = "signal"

C_TYPE = "static int"
# For embedded memory saving, you can use:
# C_TYPE = "const int"

LENGTH_NAME = "signal_length"

VALUES_PER_LINE = 10

ROUND_DECIMALS_TO_INT = True

INCLUDE_HEADER_GUARD = True
HEADER_GUARD_NAME = "INPUT_SIGNAL_H"

# ============================================================
# END USER SETTINGS
# ============================================================


def read_numbers(txt_path):
    lines = Path(txt_path).read_text().splitlines()

    # Prefer one numeric sample per line so UART banners or metadata do not
    # accidentally become signal samples. Fall back to regex extraction for
    # legacy files that contain multiple numbers on one line.
    values = [
        line.strip()
        for line in lines
        if re.fullmatch(r"[-+]?\d*\.?\d+", line.strip())
    ]
    if not values:
        text = "\n".join(lines)
        values = re.findall(r"[-+]?\d*\.?\d+", text)

    if ROUND_DECIMALS_TO_INT:
        return [int(round(float(v))) for v in values]

    return [float(v) for v in values]


def format_number(value):
    if isinstance(value, int):
        return str(value)

    value = float(value)
    if value.is_integer():
        return str(int(value))

    return f"{value:.12g}"


def format_c_array(values, array_name=ARRAY_NAME, length_name=LENGTH_NAME,
                   line_start=None, line_end=None, sample_step=1):
    signal_length = len(values)

    lines = []

    if INCLUDE_HEADER_GUARD:
        lines.append(f"#ifndef {HEADER_GUARD_NAME}")
        lines.append(f"#define {HEADER_GUARD_NAME}")
        lines.append("")

    lines.append(f"#define {length_name} {signal_length}")
    if line_start is not None:
        lines.append(f"#define signal_line_start {format_number(line_start)}")
    if line_end is not None:
        lines.append(f"#define signal_line_end {format_number(line_end)}")
    lines.append(f"#define signal_sample_step {format_number(sample_step)}")
    lines.append("")

    lines.append(f"{C_TYPE} {array_name}[{length_name}] =")
    lines.append("{")

    for i in range(0, signal_length, VALUES_PER_LINE):
        chunk = values[i:i + VALUES_PER_LINE]
        line = ", ".join(str(v) for v in chunk)

        if i + VALUES_PER_LINE < signal_length:
            line += ","

        lines.append("    " + line)

    lines.append("};")

    if INCLUDE_HEADER_GUARD:
        lines.append("")
        lines.append(f"#endif /* {HEADER_GUARD_NAME} */")

    lines.append("")
    return "\n".join(lines)


def parse_end(value):
    if value is None:
        return None
    if str(value).lower() == "none":
        return None
    return int(value)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert a text signal into input_signal.h for gsr_eval."
    )
    parser.add_argument("--input", type=Path, default=INPUT_TXT_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument("--start", type=int, default=START_INDEX)
    parser.add_argument("--end", type=parse_end, default=END_INDEX)
    parser.add_argument("--divisor", type=int, default=DIVISOR)
    parser.add_argument("--array-name", default=ARRAY_NAME)
    parser.add_argument("--length-name", default=LENGTH_NAME)
    parser.add_argument(
        "--downsample-factor",
        type=int,
        default=DOWNSAMPLE_FACTOR,
        help="Keep one FE input sample per N selected source samples.",
    )
    parser.add_argument(
        "--downsample-mode",
        choices=("pick", "mean"),
        default=DOWNSAMPLE_MODE,
        help="Use every Nth sample, or average each N-sample block.",
    )
    parser.add_argument(
        "--downsample-offset",
        type=int,
        default=DOWNSAMPLE_OFFSET,
        help="Skip this many selected source samples before downsampling.",
    )
    parser.add_argument(
        "--oversample-factor",
        type=int,
        default=OVERSAMPLE_FACTOR,
        help="Insert N-1 FE input samples between adjacent selected samples.",
    )
    parser.add_argument(
        "--oversample-mode",
        choices=("hold", "zoh", "linear"),
        default=OVERSAMPLE_MODE,
        help="Use zero-order hold or linear interpolation for inserted samples.",
    )
    return parser.parse_args()


def downsample_values(values, factor, mode, offset):
    if factor < 1:
        raise ValueError("DOWNSAMPLE_FACTOR must be >= 1")
    if offset < 0:
        raise ValueError("DOWNSAMPLE_OFFSET must be >= 0")
    if offset >= factor:
        raise ValueError("DOWNSAMPLE_OFFSET must be smaller than DOWNSAMPLE_FACTOR")
    if offset >= len(values):
        raise ValueError("DOWNSAMPLE_OFFSET skips all selected samples")

    sampled = []
    last_source_offset = offset

    for start in range(offset, len(values), factor):
        if mode == "pick":
            sampled.append(values[start])
            last_source_offset = start
        elif mode == "mean":
            block = values[start:start + factor]
            avg = sum(block) / len(block)
            sampled.append(int(round(avg)) if ROUND_DECIMALS_TO_INT else avg)
            last_source_offset = start + len(block) - 1
        else:
            raise ValueError(f"Unsupported downsample mode: {mode}")

    return sampled, offset, last_source_offset


def rounded_sample(value):
    return int(round(value)) if ROUND_DECIMALS_TO_INT else value


def oversample_values(values, factor, mode):
    if factor < 1:
        raise ValueError("OVERSAMPLE_FACTOR must be >= 1")
    if not values:
        raise ValueError("No samples to oversample")
    if factor == 1 or len(values) == 1:
        return values

    sampled = []
    use_hold = mode in ("hold", "zoh")

    for idx in range(len(values) - 1):
        current = values[idx]
        nxt = values[idx + 1]
        for sub in range(factor):
            if use_hold:
                value = current
            elif mode == "linear":
                alpha = sub / factor
                value = current + (nxt - current) * alpha
            else:
                raise ValueError(f"Unsupported oversample mode: {mode}")

            sampled.append(rounded_sample(value))

    sampled.append(values[-1])
    return sampled


def main():
    args = parse_args()
    numbers = read_numbers(args.input)

    if args.start < 0:
        raise ValueError("START_INDEX must be >= 0")

    if args.end is not None and args.end < args.start:
        raise ValueError("END_INDEX must be greater than or equal to START_INDEX")

    selected = numbers[args.start:args.end]

    if not selected:
        raise ValueError("No samples selected. Check START_INDEX and END_INDEX.")

    if args.divisor == 0:
        raise ValueError("DIVISOR must be non-zero")

    if args.divisor != 1:
        selected = [v // args.divisor for v in selected]

    selected, first_source_offset, last_source_offset = downsample_values(
        selected,
        args.downsample_factor,
        args.downsample_mode,
        args.downsample_offset,
    )
    selected = oversample_values(
        selected,
        args.oversample_factor,
        args.oversample_mode,
    )

    line_start = args.start + first_source_offset + 1
    line_end = args.start + last_source_offset + 1
    sample_step = args.downsample_factor / args.oversample_factor

    c_code = format_c_array(
        selected,
        args.array_name,
        args.length_name,
        line_start=line_start,
        line_end=line_end,
        sample_step=sample_step,
    )

    Path(args.output).write_text(c_code)

    print("Done.")
    print(f"Input file: {args.input}")
    print(f"Output file: {args.output}")
    print(f"Start index: {args.start}")
    print(f"End index: {args.end}")
    print(f"Divisor: {args.divisor}")
    print(f"Downsample factor: {args.downsample_factor}")
    print(f"Downsample mode: {args.downsample_mode}")
    print(f"Downsample offset: {args.downsample_offset}")
    print(f"Oversample factor: {args.oversample_factor}")
    print(f"Oversample mode: {args.oversample_mode}")
    print(f"Effective sample step: {format_number(sample_step)}")
    print(f"Line start: {line_start}")
    print(f"Line end: {line_end}")
    print(f"Number of samples written: {len(selected)}")


if __name__ == "__main__":
    main()
