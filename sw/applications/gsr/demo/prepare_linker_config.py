#!/usr/bin/env python3
"""Create the GSR demo's linker config without its unused debug memory region."""

import argparse

import hjson


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()

    with open(args.source, encoding="utf-8") as source_file:
        config = hjson.load(source_file)

    config["linker_sections"] = [
        section
        for section in config["linker_sections"]
        if section["name"] != "debug_mem"
    ]
    # Keep the GSR demo's static footprint within SRAM0. This app does not use
    # dynamic allocation; retain a minimal heap and a 1.2 KiB stack.
    config["linker_script"]["heap_size"] = "0x40"
    config["linker_script"]["stack_size"] = "0x4c0"

    with open(args.destination, "w", encoding="utf-8") as destination_file:
        hjson.dump(config, destination_file)


if __name__ == "__main__":
    main()
