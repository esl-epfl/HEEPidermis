#!/usr/bin/env python3
# Copyright EPFL contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

from vendor import Desc
import sys
import argparse


parser = argparse.ArgumentParser(prog="check_vendor", description='Compares that two vendor files points to the same revision commit')

parser.add_argument('--file_1',
                        '-f1',
                        dest="desc_file1",
                        type=argparse.FileType('r', encoding='UTF-8'),
                        help='first vendoring description file (*.vendor.hjson)')
    

parser.add_argument('--file_2',
                        '-f2',
                        dest="desc_file2",
                        type=argparse.FileType('r', encoding='UTF-8'),
                        help='second vendoring description file (*.vendor.hjson)')

args = parser.parse_args()

desc1 = Desc(args.desc_file1, [])
desc2 = Desc(args.desc_file2, [])

if (desc1.upstream.rev != desc2.upstream.rev):
    print("The two vendors have a different revision number")
    print("File1: " + str(args.desc_file1.name) + " : " + str(desc1.upstream.rev))
    print("File2: " + str(args.desc_file2.name) + " : " + str(desc2.upstream.rev))
    exit(-1)
else:
    exit(0)