#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path


# ============================================================
# USER SETTINGS - CHANGE THESE ONLY
# ============================================================

_SCRIPT_DIR = Path(__file__).parent

INPUT_CSV_FILE = _SCRIPT_DIR / "reconstructed_dlc.csv"

OUTPUT_FILE = _SCRIPT_DIR / "input_signal.h"

START_INDEX = 0        # inclusive, 0-indexed data row in reconstructed_dlc.csv
END_INDEX   = 1000     # exclusive, 0-indexed data row in reconstructed_dlc.csv

# reconstructed_dlc.csv contains time_s and conductance_nS. FE consumes the
# conductance values only.
CSV_COLUMN = "conductance_nS"

# Divide raw values by this factor before writing.
DIVISOR = 1

# Optional pre-FE downsampling.
DOWNSAMPLE_FACTOR = 1
DOWNSAMPLE_MODE = "pick"   # "pick" or "mean"
DOWNSAMPLE_OFFSET = 0      # source samples to skip before downsampling

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


def _is_number(value):
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _column_index(header, column):
    if column in header:
        return header.index(column)

    try:
        index = int(column)
    except ValueError as exc:
        raise ValueError(
            f"Column '{column}' not found. Available columns: {', '.join(header)}"
        ) from exc

    if index < 0 or index >= len(header):
        raise ValueError(
            f"Column index {index} out of range for {len(header)} CSV columns"
        )

    return index


def read_numbers(csv_path, column=CSV_COLUMN):
    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))

    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"No rows found in {csv_path}")

    first_row = [cell.strip() for cell in rows[0]]
    has_header = not all(_is_number(cell) for cell in first_row)

    if has_header:
        column_idx = _column_index(first_row, column)
        data_rows = rows[1:]
    else:
        try:
            column_idx = int(column)
        except ValueError as exc:
            raise ValueError(
                "CSV has no header, so --column must be a zero-based column index"
            ) from exc
        data_rows = rows

    values = []
    for data_row_num, row in enumerate(data_rows, start=1):
        if column_idx >= len(row):
            raise ValueError(
                f"Row {data_row_num} has {len(row)} column(s), expected "
                f"column index {column_idx}"
            )

        text = row[column_idx].strip()
        if not text:
            continue
        if not _is_number(text):
            raise ValueError(
                f"Non-numeric value in row {data_row_num}, column {column_idx}: {text}"
            )

        value = float(text)
        values.append(int(round(value)) if ROUND_DECIMALS_TO_INT else value)

    if not values:
        raise ValueError(f"No numeric samples found in column '{column}'")

    return values


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
        lines.append(f"#define signal_line_start {line_start}")
    if line_end is not None:
        lines.append(f"#define signal_line_end {line_end}")
    lines.append(f"#define signal_sample_step {sample_step}")
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
        description="Convert reconstructed dLC conductance CSV into input_signal.h for FE."
    )
    parser.add_argument("--input", type=Path, default=INPUT_CSV_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument("--column", default=CSV_COLUMN)
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


def main():
    args = parse_args()
    numbers = read_numbers(args.input, args.column)

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

    line_start = args.start + first_source_offset + 1
    line_end = args.start + last_source_offset + 1

    c_code = format_c_array(
        selected,
        args.array_name,
        args.length_name,
        line_start=line_start,
        line_end=line_end,
        sample_step=args.downsample_factor,
    )

    Path(args.output).write_text(c_code)

    print("Done.")
    print(f"Input file: {args.input}")
    print(f"Output file: {args.output}")
    print(f"CSV column: {args.column}")
    print(f"Start index: {args.start}")
    print(f"End index: {args.end}")
    print(f"Divisor: {args.divisor}")
    print(f"Downsample factor: {args.downsample_factor}")
    print(f"Downsample mode: {args.downsample_mode}")
    print(f"Downsample offset: {args.downsample_offset}")
    print(f"Line start: {line_start}")
    print(f"Line end: {line_end}")
    print(f"Number of samples written: {len(selected)}")


if __name__ == "__main__":
    main()
