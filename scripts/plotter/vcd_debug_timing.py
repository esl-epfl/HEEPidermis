#!/usr/bin/env python3
"""Measure software timing intervals from x-heep debug markers in VCD/FST.

The tool decodes words written to the debug section as:

    tag     = debug_word[31:24]
    payload = debug_word[23:0]

Use a group tag to record the current experiment parameter, for example N, and
start/end tags to measure a processing interval.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_DEBUG_SIGNAL = (
    "TOP.tb_system.u_cheep_top.u_core_v_mini_mcu."
    "memory_subsystem_i.ram1_i.tc_ram_i.debug_section"
)

TIME_SCALE_TO_NS = {
    "s": 1e9,
    "ms": 1e6,
    "us": 1e3,
    "ns": 1.0,
    "ps": 1e-3,
    "fs": 1e-6,
}


@dataclass
class SignalTrace:
    path: str
    values: list[tuple[int, object]]


@dataclass
class DebugEvent:
    tick: int
    time_ns: float
    word: int
    tag: int
    payload: int


@dataclass
class TimingInterval:
    group: int | None
    start_time_ns: float
    end_time_ns: float
    duration_ns: float
    duration_us_real: float
    start_payload: int
    end_payload: int


def waveform_lines(wave_path: Path) -> Iterable[str]:
    if wave_path.suffix == ".fst":
        proc = subprocess.Popen(
            ["fst2vcd", str(wave_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                yield line
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            else:
                ret = proc.wait()
                if ret != 0:
                    stderr = proc.stderr.read() if proc.stderr is not None else ""
                    raise RuntimeError(
                        "fst2vcd failed. Make sure GTKWave/fst2vcd is installed.\n"
                        + stderr.strip()
                    )
    else:
        with wave_path.open("r", encoding="utf-8", errors="replace") as f:
            yield from f


def parse_value_change(line: str) -> tuple[str, object]:
    prefix = line[0]
    if prefix in "01xXzZ":
        return line[1:].strip(), prefix
    if prefix in "bB":
        value_bits, code = line[1:].split(None, 1)
        if any(bit in value_bits for bit in "xXzZ"):
            value = None
        else:
            value = int(value_bits, 2)
        return code.strip(), value
    if prefix in "rR":
        value_str, code = line[1:].split(None, 1)
        return code.strip(), float(value_str)
    raise ValueError(f"Unsupported VCD value change: {line}")


def _timescale_ns(tokens: list[str]) -> float:
    # Handles both "$timescale 1 ns $end" and split multi-token variants.
    numeric = None
    unit = None
    for token in tokens:
        if token in {"$timescale", "$end"}:
            continue
        compact = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([a-z]+)", token)
        if compact and compact.group(2) in TIME_SCALE_TO_NS:
            numeric = float(compact.group(1))
            unit = compact.group(2)
            continue
        try:
            numeric = float(token)
            continue
        except ValueError:
            pass
        if token in TIME_SCALE_TO_NS:
            unit = token
    if numeric is None or unit is None:
        raise ValueError(f"Could not parse VCD timescale from: {' '.join(tokens)}")
    return numeric * TIME_SCALE_TO_NS[unit]


def parse_waveform(
    wave_path: Path,
    wanted_paths: list[str],
    parse_until_ns: float | None = None,
) -> tuple[float, dict[str, SignalTrace]]:
    wanted = set(wanted_paths)
    wanted_leaf = {path.split(".")[-1]: path for path in wanted_paths}
    id_to_path: dict[str, str] = {}
    scope: list[str] = []
    traces = {path: SignalTrace(path, []) for path in wanted}
    timescale_ns = 1.0
    current_tick = 0
    in_header = True
    timescale_tokens: list[str] | None = None

    for raw_line in waveform_lines(wave_path):
        line = raw_line.strip()
        if not line:
            continue

        if in_header:
            tokens = line.split()
            if timescale_tokens is not None:
                timescale_tokens.extend(tokens)
                if "$end" in tokens:
                    timescale_ns = _timescale_ns(timescale_tokens)
                    timescale_tokens = None
            elif line.startswith("$timescale"):
                timescale_tokens = tokens[:]
                if "$end" in tokens:
                    timescale_ns = _timescale_ns(timescale_tokens)
                    timescale_tokens = None
            elif line.startswith("$scope"):
                # $scope module TOP $end
                if len(tokens) >= 3:
                    scope.append(tokens[2])
            elif line.startswith("$upscope"):
                if scope:
                    scope.pop()
            elif line.startswith("$var"):
                # $var wire 32 <id> debug_section $end
                if len(tokens) >= 5:
                    code = tokens[3]
                    name = tokens[4]
                    path = ".".join(scope + [name])
                    if path in wanted:
                        id_to_path[code] = path
                    elif name in wanted_leaf:
                        id_to_path[code] = wanted_leaf[name]
            elif line.startswith("$enddefinitions"):
                in_header = False
            continue

        if line.startswith("#"):
            current_tick = int(line[1:])
            if parse_until_ns is not None and current_tick * timescale_ns > parse_until_ns:
                break
            continue

        if line.startswith("$"):
            continue

        code, value = parse_value_change(line)
        path = id_to_path.get(code)
        if path is not None:
            traces[path].values.append((current_tick, value))

    return timescale_ns, traces


def decode_debug_word(value: object) -> tuple[int, int, int] | None:
    if value is None:
        return None
    word = int(value) & 0xFFFFFFFF
    tag = (word >> 24) & 0xFF
    payload = word & 0x00FFFFFF
    return word, tag, payload


def collect_debug_events(
    trace: SignalTrace,
    timescale_ns: float,
    start_ns: float | None = None,
    stop_ns: float | None = None,
) -> list[DebugEvent]:
    events: list[DebugEvent] = []
    last_word: int | None = None
    for tick, value in trace.values:
        time_ns = tick * timescale_ns
        if start_ns is not None and time_ns < start_ns:
            continue
        if stop_ns is not None and time_ns > stop_ns:
            break
        decoded = decode_debug_word(value)
        if decoded is None:
            continue
        word, tag, payload = decoded
        if word == last_word:
            continue
        last_word = word
        events.append(DebugEvent(tick, time_ns, word, tag, payload))
    return events


def parse_tag(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value, 0)


def measure_intervals(
    events: list[DebugEvent],
    start_tag: int,
    end_tag: int,
    group_tag: int | None,
    sys_fclk_hz: float,
) -> list[TimingInterval]:
    intervals: list[TimingInterval] = []
    current_group: int | None = None
    pending_start: DebugEvent | None = None

    for event in events:
        if group_tag is not None and event.tag == group_tag:
            current_group = event.payload
            continue
        if event.tag == start_tag:
            pending_start = event
            continue
        if event.tag == end_tag and pending_start is not None:
            duration_ns = event.time_ns - pending_start.time_ns
            intervals.append(
                TimingInterval(
                    group=current_group,
                    start_time_ns=pending_start.time_ns,
                    end_time_ns=event.time_ns,
                    duration_ns=duration_ns,
                    duration_us_real=duration_ns * (sys_fclk_hz / 1e9) * 10,
                    start_payload=pending_start.payload,
                    end_payload=event.payload,
                )
            )
            pending_start = None

    return intervals


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "count": 0,
            "mean": math.nan,
            "min": math.nan,
            "max": math.nan,
            "stdev": math.nan,
        }
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    return {
        "count": len(values),
        "mean": mean,
        "min": min(values),
        "max": max(values),
        "stdev": math.sqrt(var),
    }


def write_intervals_csv(path: Path, intervals: list[TimingInterval]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "group",
                "start_time_ns",
                "end_time_ns",
                "duration_ns",
                "duration_us_real",
                "start_payload",
                "end_payload",
            ]
        )
        for interval in intervals:
            writer.writerow(
                [
                    "" if interval.group is None else interval.group,
                    f"{interval.start_time_ns:.3f}",
                    f"{interval.end_time_ns:.3f}",
                    f"{interval.duration_ns:.3f}",
                    f"{interval.duration_us_real:.3f}",
                    interval.start_payload,
                    interval.end_payload,
                ]
            )


def grouped_interval_stats(
    intervals: list[TimingInterval],
) -> list[tuple[int | None, dict[str, float], dict[str, float]]]:
    grouped: dict[int | None, list[TimingInterval]] = {}
    for interval in intervals:
        grouped.setdefault(interval.group, []).append(interval)

    rows = []
    for group, group_intervals in sorted(
        grouped.items(), key=lambda item: (-1 if item[0] is None else item[0])
    ):
        us_real = [interval.duration_us_real for interval in group_intervals]
        ns = [interval.duration_ns for interval in group_intervals]
        rows.append((group, summarize(us_real), summarize(ns)))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure timing between x-heep debug markers in VCD/FST."
    )
    parser.add_argument("waveform", type=Path, help="Input .vcd or .fst waveform")
    parser.add_argument("--debug-signal", default=DEFAULT_DEBUG_SIGNAL)
    parser.add_argument("--start-tag", type=parse_tag, help="Start marker tag, e.g. 0xA1")
    parser.add_argument("--end-tag", type=parse_tag, help="End marker tag, e.g. 0xA2")
    parser.add_argument("--group-tag", type=parse_tag, help="Marker tag whose payload groups intervals, e.g. N")
    parser.add_argument("--sys-fclk-hz", type=float, default=10_000_000.0)
    parser.add_argument("--start-ms", type=float, default=None)
    parser.add_argument("--stop-ms", type=float, default=None)
    parser.add_argument("--max-events", type=int, default=80)
    parser.add_argument("--list-events", action="store_true")
    parser.add_argument("--csv", type=Path, help="Optional raw interval CSV output")
    args = parser.parse_args()

    start_ns = None if args.start_ms is None else args.start_ms * 1e6
    stop_ns = None if args.stop_ms is None else args.stop_ms * 1e6

    timescale_ns, traces = parse_waveform(
        args.waveform,
        [args.debug_signal],
        parse_until_ns=stop_ns,
    )
    trace = traces[args.debug_signal]
    if not trace.values:
        print(f"No values found for debug signal:\n  {args.debug_signal}")
        return 1

    events = collect_debug_events(trace, timescale_ns, start_ns=start_ns, stop_ns=stop_ns)
    print(f"debug_events={len(events)} timescale_ns={timescale_ns:g}")

    if args.list_events or args.start_tag is None or args.end_tag is None:
        print("time_ns,tag,payload,word")
        for event in events[: args.max_events]:
            print(
                f"{event.time_ns:.3f},0x{event.tag:02X},{event.payload},0x{event.word:08X}"
            )
        if len(events) > args.max_events:
            print(f"... {len(events) - args.max_events} more events")

    if args.start_tag is None or args.end_tag is None:
        return 0

    intervals = measure_intervals(
        events,
        start_tag=args.start_tag,
        end_tag=args.end_tag,
        group_tag=args.group_tag,
        sys_fclk_hz=args.sys_fclk_hz,
    )

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        write_intervals_csv(args.csv, intervals)
        print(f"wrote {args.csv}")

    print("group,count,max_us_real,min_us_real,mean_us_real,stdev_us_real,max_ns_sim")
    for group, stats, ns_stats in grouped_interval_stats(intervals):
        group_label = "" if group is None else str(group)
        print(
            f"{group_label},{stats['count']},"
            f"{stats['max']:.3f},{stats['min']:.3f},"
            f"{stats['mean']:.3f},{stats['stdev']:.3f},"
            f"{ns_stats['max']:.3f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
