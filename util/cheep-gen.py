#!/usr/bin/env python3

# Copyright 2022 EPFL and Politecnico di Torino.
# Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# File: cheep-gen.py
# Author: Michele Caon, Luigi Giuffrida
# Date: 30/04/2024
# Description: Generate cheep HDL files based on configuration.

import argparse
import pathlib
import re
import sys
import logging
import math

import hjson
from jsonref import JsonRef
from mako.template import Template

# Compile a regex to trim trailing whitespaces on lines
re_trailws = re.compile(r"[ \t\r]+$", re.MULTILINE)


def int2hexstr(n, nbits) -> str:
    """
    Converts an integer to a hexadecimal string representation.

    Args:
        n (int): The integer to be converted.
        nbits (int): The number of bits to represent the hexadecimal string.

    Returns:
        str: The hexadecimal string representation of the integer.

    """
    return hex(n)[2:].zfill(nbits // 4).upper()


def write_template(tpl_path, outdir, **kwargs):
    if tpl_path is not None:
        tpl_path = pathlib.Path(tpl_path).absolute()
        if tpl_path.exists():
            tpl = Template(filename=str(tpl_path))
            with open(
                outdir / tpl_path.with_suffix("").name, "w", encoding="utf-8"
            ) as f:
                code = tpl.render_unicode(**kwargs)
                code = re_trailws.sub("", code)
                f.write(code)
        else:
            raise FileNotFoundError(f"Template file {tpl_path} not found")


def main():
    # Parser for command line arguments
    parser = argparse.ArgumentParser(
        prog="cheep-gen.py",
        description="Generate cheep HDL files based on the provided configuration.",
    )
    parser.add_argument(
        "--cfg",
        "-c",
        metavar="FILE",
        type=argparse.FileType("r"),
        required=True,
        help="Configuration file in HJSON format",
    )
    parser.add_argument(
        "--outdir",
        "-o",
        metavar="DIR",
        type=pathlib.Path,
        required=True,
        help="Output directory",
    )
    parser.add_argument(
        "--tpl-sv", "-s", type=str, metavar="SV", help="SystemVerilog template filename"
    )
    parser.add_argument(
        "--tpl-c", "-C", type=str, metavar="C_SOURCE", help="C template filename"
    )
    parser.add_argument(
        "--corev_pulp", nargs="?", type=bool, help="CORE-V PULP extension"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Increase verbosity"
    )
    args = parser.parse_args()

    # Set verbosity level
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # Read HJSON configuration file
    with args.cfg as f:
        try:
            cfg = hjson.load(f, use_decimal=True)
            cfg = JsonRef.replace_refs(cfg)
        except ValueError as exc:
            raise SystemExit(sys.exc_info()[1]) from exc

    # Check if the output directory is valid
    if not args.outdir.is_dir():
        exit(f"Output directory {args.outdir} is not a valid path")

    # Create output directory
    args.outdir.mkdir(parents=True, exist_ok=True)

    # Get configuration parameters
    # ----------------------------
    # CORE-V-MINI-MCU configuration
    cpu_features = cfg["cpu_features"]
    if args.corev_pulp != None:
        cpu_features["corev_pulp"] = args.corev_pulp

    # Bus configuration
    xbar_nmasters = int(cfg["ext_xbar_masters"])
    xbar_nslaves = 7
    periph_nslaves = len(cfg["ext_periph"])

    # Peripherals map
    iDAC_ctrl_start_address = int(cfg["ext_periph"]["iDAC_ctrl"]["offset"], 16)
    iDAC_ctrl_start_address_hex = int2hexstr(iDAC_ctrl_start_address, 32)
    iDAC_ctrl_size = int(cfg["ext_periph"]["iDAC_ctrl"]["length"], 16)
    iDAC_ctrl_size_hex = int2hexstr(iDAC_ctrl_size, 32)

    VCO_decoder_start_address = int(cfg["ext_periph"]["VCO_decoder"]["offset"], 16)
    VCO_decoder_start_address_hex = int2hexstr(VCO_decoder_start_address, 32)
    VCO_decoder_size = int(cfg["ext_periph"]["VCO_decoder"]["length"], 16)
    VCO_decoder_size_hex = int2hexstr(VCO_decoder_size, 32)

    SES_filter_start_address = int(cfg["ext_periph"]["SES_filter"]["offset"], 16)
    SES_filter_start_address_hex = int2hexstr(SES_filter_start_address, 32)
    SES_filter_size = int(cfg["ext_periph"]["SES_filter"]["length"], 16)
    SES_filter_size_hex = int2hexstr(SES_filter_size, 32)

    aMUX_ctrl_start_address = int(cfg["ext_periph"]["aMUX_ctrl"]["offset"], 16)
    aMUX_ctrl_start_address_hex = int2hexstr(aMUX_ctrl_start_address, 32)
    aMUX_ctrl_size = int(cfg["ext_periph"]["aMUX_ctrl"]["length"], 16)
    aMUX_ctrl_size_hex = int2hexstr(aMUX_ctrl_size, 32)

    REFs_ctrl_start_address = int(cfg["ext_periph"]["REFs_ctrl"]["offset"], 16)
    REFs_ctrl_start_address_hex = int2hexstr(REFs_ctrl_start_address, 32)
    REFs_ctrl_size = int(cfg["ext_periph"]["REFs_ctrl"]["length"], 16)
    REFs_ctrl_size_hex = int2hexstr(REFs_ctrl_size, 32)

    dLC_start_address = int(cfg["ext_periph"]["dLC"]["offset"], 16)
    dLC_start_address_hex = int2hexstr(dLC_start_address, 32)
    dLC_size = int(cfg["ext_periph"]["dLC"]["length"], 16)
    dLC_size_hex = int2hexstr(dLC_size, 32)

    CIC_start_address = int(cfg["ext_periph"]["CIC"]["offset"], 16)
    CIC_start_address_hex = int2hexstr(CIC_start_address, 32)
    CIC_size = int(cfg["ext_periph"]["CIC"]["length"], 16)
    CIC_size_hex = int2hexstr(CIC_size, 32)

    # Explicit arguments
    kwargs = {
        "cpu_corev_pulp": int(cpu_features["corev_pulp"]),
        "cpu_corev_xif": int(cpu_features["corev_xif"]),
        "cpu_fpu": int(cpu_features["fpu"]),
        "cpu_riscv_zfinx": int(cpu_features["riscv_zfinx"]),
        "xbar_nmasters": xbar_nmasters,
        "xbar_nslaves": xbar_nslaves,
        "periph_nslaves": periph_nslaves,
        "iDAC_ctrl_start_address": iDAC_ctrl_start_address_hex,
        "iDAC_ctrl_size": iDAC_ctrl_size_hex,
        "VCO_decoder_start_address": VCO_decoder_start_address_hex,
        "VCO_decoder_size": VCO_decoder_size_hex,
        "SES_filter_start_address": SES_filter_start_address_hex,
        "SES_filter_size": SES_filter_size_hex,
        "aMUX_ctrl_start_address": aMUX_ctrl_start_address_hex,
        "aMUX_ctrl_size": aMUX_ctrl_size_hex,
        "REFs_ctrl_start_address": REFs_ctrl_start_address_hex,
        "REFs_ctrl_size": REFs_ctrl_size_hex,
        "dLC_start_address": dLC_start_address_hex,
        "dLC_size": dLC_size_hex,
        "CIC_start_address": CIC_start_address_hex,
        "CIC_size": CIC_size_hex,
    }

    # Generate SystemVerilog package
    if args.tpl_sv is not None:
        write_template(args.tpl_sv, args.outdir, **kwargs)

    # Generate C header
    if args.tpl_c is not None:
        write_template(args.tpl_c, args.outdir, **kwargs)


if __name__ == "__main__":
    main()
