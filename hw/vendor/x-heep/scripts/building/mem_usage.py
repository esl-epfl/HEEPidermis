# Copyright EPFL contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# Author: Juan Sapriza <juan.sapriza@epfl.ch>
#
# Report linker regions from main.map and physical banks from the MCU package.
# Allocated ELF sections determine actual occupancy: C for executable code,
# d for constants/runtime data/reserved heap and stack, and i for interleaved data.
# Interleaved banks assume an even distribution across the bank group.

import subprocess
import re


def is_readelf_available():
    try:
        subprocess.run(["readelf", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except FileNotFoundError:
        return False


def get_banks_and_sizes(mcu_pkg_size):
    """
    Parses the core_v_mini_mcu_pkg.sv file to extract the count of memory banks and their sizes. 
    It looks for the definitions:
    localparam int unsigned NUM_BANKS = 5;
    localparam int unsigned NUM_BANKS_IL = 2;

    To obtain the total and IL count. 

    Later looks for 
    localparam logic [31:0] RAM0_SIZE = 32'h00008000;
    To extract the size of each. 
    They are all assumed to be contiguous. 

    Parameters:
    mcu_pkg_size - path of the .sv file, relative to the location from which this script is called (e.g. the Makefile)

    Returns: 
    num_banks       - Total count of memory banks
    num_il_banks    - How many of those banks are IL
    sizes_B         - Size in bytes of each bank
    """
    num_banks = 0
    num_il_banks = 0
    sizes_B = []
    try:
        with open(mcu_pkg_size, 'r') as file:
            for line in file:
                if "NUM_BANKS =" in line:
                    num_banks = int(line.split('=')[1].strip().strip(';'))
                elif "NUM_BANKS_IL =" in line:
                    num_il_banks = int(line.split('=')[1].strip().strip(';'))
                else: 
                    match = re.search(r"RAM(\d+)_SIZE = 32'h([0-9A-Fa-f]+);", line)
                    if match:
                        size_B      = int(match.group(2), 16)
                        sizes_B.append(size_B)               
    except FileNotFoundError:
        print("File not found. Please check the path and try again.")
    return num_banks, num_il_banks, sizes_B

def get_memory_sections(map_path):
    """
    Parses the main.map file to obtain the origin and length of each region. 
    These are called ram0 (code), ram1 (data) and ram2 (IL data) - but that does not necessarily 
    correspond with an index of memory banks. 

    The origin and size of each are defined in the configs/*.hjson files.

    Parameters:
    map_path - path of the .map file, relative to the location from which this script is called (e.g. the Makefile)

    Returns: 
    sections - Dictionary with the sections found
    """
    sections = {}
    try:
        index = 0
        with open(map_path, 'r') as file:
            collect = False
            for line in file:
                if "Name" in line and "Origin" in line and "Length" in line:
                    collect = True
                    continue
                if collect:
                    if line.strip() == '':
                        collect = False  # Stop collecting when a blank line is encountered
                        continue
                    parts = line.split()
                    if len(parts) >= 4:
                        name = parts[0]
                        if name == 'FLASH': continue
                        origin = int(parts[1], 16)
                        length = int(parts[2], 16)
                        attributes = parts[3]
                        sections[name] = {'origin': origin, 'length': length, 'attributes': attributes}
                        index += 1
    except FileNotFoundError:
        print("File not found. Please check the path and try again.")
    return sections

def get_regions(readelf_output):
    """Classify allocated ELF sections individually, even in mixed LOAD segments."""
    section_line = re.compile(
        r"^\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-fA-F]+)"
        r"\s+[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[0-9a-fA-F]+"
        r"\s+([A-Za-z]*)\s+\d+\s+\d+\s+\d+\s*$"
    )
    regions = []
    for line in readelf_output.splitlines():
        match = section_line.match(line)
        if not match:
            continue
        name, address, size, flags = match.groups()
        size = int(size, 16)
        if "A" not in flags or not size:
            continue  # Debug metadata and empty sections do not occupy target SRAM.
        address = int(address, 16)
        symbol = "C" if "X" in flags else "d"
        if name == ".data_interleaved" or name.startswith(".data_interleaved."):
            symbol = "i"
        regions.append({
            "name": name,
            "symbol": symbol,
            "start_add": address,
            "size_B": size,
            "end_add": address + size,
        })
    return regions


def overlap(region, start, end):
    """Number of allocated bytes in this address interval."""
    return max(0, min(end, region["end_add"]) - max(start, region["start_add"]))


def interval_usage(regions, start, end):
    """Return the dominant section type and exact usage for one display cell."""
    totals = {"C": 0, "d": 0, "i": 0}
    for region in regions:
        totals[region["symbol"]] += overlap(region, start, end)
    used = sum(totals.values())
    return (max(totals, key=totals.get) if used else "-"), used


def print_region_summary(sections, regions):
    # These are linker regions, which can each hold both code and data.
    print("Region \t Start \tEnd\tSz(kB)\tUsd(kB)\tReq(kB)\tUtilz(%)")
    for name, section in sections.items():
        if not re.fullmatch(r"ram\d+", name):
            continue
        start = section["origin"]
        size = section["length"]
        end = start + size
        occupants = [region for region in regions if overlap(region, start, end)]
        used = sum(overlap(region, start, end) for region in occupants)
        required = max((min(region["end_add"], end) - start for region in occupants), default=0)
        utilization = 100 * required / size if size else 0
        print(f"{name}:  \t{start/1024:5.1f}\t{end/1024:5.1f}\t{size/1024:5.1f}"
              f"\t{used/1024:0.1f}\t{required/1024:5.1f}\t{utilization:0.1f}")


def print_banks(regions, bank_sizes, num_il_banks):
    # Each character covers 1 KiB; mixed cells use their dominant section type.
    # The percentage uses actual allocated bytes, including partial cells.
    granularity = 1024
    num_continuous = len(bank_sizes) - num_il_banks
    il_start = sum(bank_sizes[:num_continuous])
    bank_start = 0
    print()
    for bank_idx, bank_size in enumerate(bank_sizes):
        interleaved = bank_idx >= num_continuous
        symbols = []
        used = 0
        for offset in range(0, bank_size, granularity):
            cell_size = min(granularity, bank_size - offset)
            if interleaved:
                # Interleaved address space is distributed evenly across its banks.
                start = il_start + offset * num_il_banks
                end = start + cell_size * num_il_banks
                symbol, cell_used = interval_usage(regions, start, end)
                cell_used /= num_il_banks
            else:
                start = bank_start + offset
                symbol, cell_used = interval_usage(regions, start, start + cell_size)
            symbols.append(symbol)
            used += cell_used
        kind = "IntL" if interleaved else "Cont"
        print(kind, bank_idx, "".join(symbols), f"\t{100 * used / bank_size:0.1f}%")
        bank_start += bank_size


def main():
    if not is_readelf_available():
        print("readelf not available. Will not print the memory utilization report.")
        return
    result = subprocess.run(
        ["readelf", "-SW", "sw/build/main.elf"],
        capture_output=True, text=True, check=True,
    )
    regions = get_regions(result.stdout)
    num_banks, num_il_banks, bank_sizes = get_banks_and_sizes(
        "hw/core-v-mini-mcu/include/core_v_mini_mcu_pkg.sv"
    )
    num_continuous = num_banks - num_il_banks
    print(f"Total space: {sum(bank_sizes)/1024:0.1f} kB = Continuous:",
          [int(size/1024) for size in bank_sizes[:num_continuous]],
          "kB + Interleaved:",
          [int(size/1024) for size in bank_sizes[num_continuous:]] if num_il_banks else [0],
          "kB")
    sections = get_memory_sections("sw/build/main.map")
    if "ram0" not in sections or "ram1" not in sections:
        print("Memory distribution analysis not available for LINKER=flash_exec")
        return
    print_region_summary(sections, regions)
    print_banks(regions, bank_sizes, num_il_banks)


if __name__ == "__main__":
    main()
