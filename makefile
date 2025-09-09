# Copyright 2022 EPFL and Politecnico di Torino.
# Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# File: makefile
# Author: Michele Caon, Luigi Giuffrida
# Date: 29/04/2024
# Description: Top-level makefile for cheep

# ----- CONFIGURATION ----- #

# Global configuration
ROOT_DIR			:= $(realpath .)
BUILD_DIR 			:= build

# FUSESOC and Python values (default)
ifndef CONDA_DEFAULT_ENV
$(info USING VENV)
FUSESOC = ./.venv/bin/fusesoc
PYTHON  = ./.venv/bin/python
else
$(info USING MINICONDA $(CONDA_DEFAULT_ENV))
FUSESOC := $(shell which fusesoc)
PYTHON  := $(shell which python)
endif

# Implementation specific variables
# TARGET options are 'asic' (default) and 'pynq-z2'
TARGET ?= asic

# X-HEEP configuration
XHEEP_DIR			:= $(ROOT_DIR)/hw/vendor/x-heep
CHEEP_CFG  			?= $(ROOT_DIR)/config/cheep_configs.hjson
PAD_CFG				?= $(ROOT_DIR)/config/cheep_pads.hjson

EXTERNAL_DOMAINS	:= 0

MCU_GEN_OPTS		:= \
	--memorybanks $(MEMORY_BANKS) \
	--memorybanks_il $(MEMORY_BANKS_IL) \
	--bus $(BUS) \
	--config $(CHEEP_CFG) \
	--cfg_peripherals $(CHEEP_CFG) \
	--pads_cfg $(PAD_CFG) \
	--external_domains $(EXTERNAL_DOMAINS)
CHEEP_TOP_TPL		:= $(ROOT_DIR)/hw/ip/cheep_top.sv.tpl
PAD_RING_TPL		:= $(ROOT_DIR)/hw/ip/pad-ring/pad_ring.sv.tpl

# cheep configuration
CHEEP_GEN_OPTS	:= \
	--cfg $(CHEEP_CFG)
CHEEP_GEN_TPL  := \
	hw/ip/packages/cheep_pkg.sv.tpl \
	sw/external/lib/runtime/cheep.h.tpl

# Simulation DPI libraries
DPI_LIBS			:= $(BUILD_DIR)/sw/sim/uartdpi.so
DPI_CINC			:= -I$(dir $(shell which verilator))../share/verilator/include/vltstd

# Simulation configuration
LOG_LEVEL			?= LOG_FULL
BOOT_MODE			?= force # jtag: wait for JTAG (DPI module), flash: boot from flash, force(default): load firmware into SRAM
FIRMWARE			?= $(ROOT_DIR)/build/sw/app/main.hex
LINKER 				?= flash_load
ifeq ($(BOOT_MODE), jtag)
FIRMWARE			= $(ROOT_DIR)/build/sw/app/main.hex.srec
LINKER 				= on_chip
else ifeq ($(BOOT_MODE), force)
FIRMWARE			= $(ROOT_DIR)/build/sw/app/main.hex
LINKER 				= on_chip
else
FIRMWARE			= $(ROOT_DIR)/build/sw/app/main.hex
LINKER 				= flash_load
endif
VCD_MODE			?= 0 # QuestaSim-only - 0: no dump, 1: dump always active, 2: dump triggered by GPIO 0
MAX_CYCLES			?= 1200000
FUSESOC_FLAGS		?=
FUSESOC_ARGS		?=

# Flash file
FLASHWRITE_FILE		?= $(FIRMWARE)

# QuestaSim
FUSESOC_BUILD_DIR			= $(shell find $(BUILD_DIR) -type d -name 'epfl_cheep_cheep_*' 2>/dev/null | sort | head -n 1)
QUESTA_SIM_DIR				= $(FUSESOC_BUILD_DIR)/sim-modelsim
QUESTA_SIM_POSTSYNTH_DIR 	= $(FUSESOC_BUILD_DIR)/sim_postsynthesis-modelsim
QUESTA_SIM_POSTLAYOUT_DIR 	= $(FUSESOC_BUILD_DIR)/sim_postlayout-modelsim

# Waves
SIM_VCD 			?= $(BUILD_DIR)/sim-common/questa-waves.fst

# Application data generation
# NOTE: the application makefile may accept additional parameters, e.g.:
# 	KERNEL_PARAMS="--row_a 8 --col_a 8 --col_b 256"
APP_MAKE 			:= $(wildcard sw/applications/$(PROJECT)/*akefile)

TOOLCHAIN ?= GCC
RISCV     ?= /shares/eslfiler1/apps/linux/Development/rv32imc

# Custom preprocessor definitions
CDEFS				?=

# Software build configuration
SW_DIR		:= sw
LINK_FOLDER := $(shell dirname "$(realpath $(firstword $(MAKEFILE_LIST)))")/sw/linker

# Testing flags
# Optional TEST_FLAGS options are '--compile-only'
TEST_FLAGS=

# Dummy target to force software rebuild
PARAMS = $(PROJECT)

# ----- BUILD RULES ----- #

# Get the path of this Makefile to pass to the Makefile help generator
MKFILE_PATH = $(shell dirname "$(realpath $(firstword $(MAKEFILE_LIST)))")
export FILE_FOR_HELP = $(MKFILE_PATH)/makefile
export XHEEP_DIR


## Print the Makefile help
## @param WHICH=xheep,all,<none> Which Makefile help to print. Leaving blank (<none>) prints only CHEEP's.
help:
ifndef WHICH
	${XHEEP_DIR}/util/MakefileHelp
else ifeq ($(filter $(WHICH),xheep x-heep),)
	${XHEEP_DIR}/util/MakefileHelp
	$(MAKE) -C $(XHEEP_DIR) help
else
	$(MAKE) -C $(XHEEP_DIR) help
endif

## @section Conda
.PHONY: conda
conda: environment.yml
	conda env create -f environment.yml

# Regenerate environment.yml from python-requirements.txt
.PHONY: environment.yml
environment.yml: python-requirements.txt
	util/python-requirements2conda.sh


# Default alias
# -------------
.PHONY: all
all: cheep-gen

## @section RTL & SW generation

## X-HEEP MCU system
.PHONY: mcu-gen
mcu-gen:
ifeq ($(TARGET), asic)
	@echo "### Building X-HEEP MCU..."
	$(MAKE) -f $(XHEEP_MAKE) mcu-gen LINK_FOLDER=$(LINK_FOLDER) X_HEEP_CFG=$(CHEEP_CFG) MCU_CFG_PERIPHERALS=$(CHEEP_CFG)
	@echo "### DONE! X-HEEP MCU generated successfully"
else ifeq ($(TARGET), pynq-z2)
	@echo "### Building X-HEEP MCU for PYNQ-Z2..."
	$(MAKE) -f $(XHEEP_MAKE) mcu-gen LINK_FOLDER=$(LINK_FOLDER) X_HEEP_CFG=$(CHEEP_CFG) MCU_CFG_PERIPHERALS=$(CHEEP_CFG)
	@echo "### DONE! X-HEEP MCU generated successfully"
else ifeq ($(TARGET), zcu104)
	@echo "### Building X-HEEP MCU for zcu104..."
	$(MAKE) -f $(XHEEP_MAKE) mcu-gen LINK_FOLDER=$(LINK_FOLDER) X_HEEP_CFG=$(CHEEP_CFG) MCU_CFG_PERIPHERALS=$(CHEEP_CFG)
	@echo "### DONE! X-HEEP MCU generated successfully"
else
	$(error ### ERROR: Unsupported target implementation: $(TARGET))
endif


## Generate CHEEP files
## @param TARGET=asic(default),pynq-z2,zcu104
.PHONY: cheep-gen
cheep-gen: mcu-gen
	@echo "### Generating cheep top and pad rings for ASIC..."
	python3 $(XHEEP_DIR)/util/mcu_gen.py $(MCU_GEN_OPTS) \
		--outdir $(ROOT_DIR)/hw/ip/ \
		--tpl-sv $(CHEEP_TOP_TPL)
	python3 $(XHEEP_DIR)/util/mcu_gen.py $(MCU_GEN_OPTS) \
		--outdir $(ROOT_DIR)/hw/ip/pad-ring/ \
		--tpl-sv $(PAD_RING_TPL)
	python3 $(XHEEP_DIR)/util/mcu_gen.py $(MCU_GEN_OPTS) \
		--outdir $(ROOT_DIR)/tb/ \
		--tpl-sv $(ROOT_DIR)/tb/tb_util.svh.tpl

	@echo "### Generating cheep files..."
	python3 util/cheep-gen.py $(CHEEP_GEN_OPTS) \
		--outdir hw/ip/packages \
		--tpl-sv hw/ip/packages/cheep_pkg.sv.tpl \
		--corev_pulp $(COREV_PULP)
	python3 util/cheep-gen.py $(CHEEP_GEN_OPTS) \
		--outdir sw/external/lib/runtime \
		--tpl-c sw/external/lib/runtime/cheep.h.tpl
	util/format-verible
	$(FUSESOC) run --no-export --target lint epfl:cheep:cheep
	@echo "### DONE! cheep files generated successfully"

## @section Simulation

## @subsection Verilator RTL simulation

## Build simulation model (do not launch simulation)
.PHONY: verilator-build
verilator-build:
	$(FUSESOC) run --no-export --target sim --tool verilator --build $(FUSESOC_FLAGS) epfl:cheep:cheep \
		$(FUSESOC_ARGS)

## Launch simulation
.PHONY: verilator-run
verilator-run: | check-firmware .verilator-check-params
	$(FUSESOC) run --no-export --target sim --tool verilator --run $(FUSESOC_FLAGS) epfl:cheep:cheep \
		--log_level=$(LOG_LEVEL) \
		--firmware=$(FIRMWARE) \
		--boot_mode=$(BOOT_MODE) \
		--max_cycles=$(MAX_CYCLES) \
		$(FUSESOC_ARGS)
	cat $(BUILD_DIR)/sim-common/uart.log

## Launch simulation without waveform dumping
.PHONY: verilator-opt
verilator-opt: | check-firmware .verilator-check-params
	$(FUSESOC) run --no-export --target sim --tool verilator --run $(FUSESOC_FLAGS) epfl:cheep:cheep \
		--log_level=$(LOG_LEVEL) \
		--firmware=$(FIRMWARE) \
		--boot_mode=$(BOOT_MODE) \
		--max_cycles=$(MAX_CYCLES) \
		--trace=false \
		$(FUSESOC_ARGS)
	cat $(BUILD_DIR)/sim-common/uart.log

## Open dumped waveform with GTKWave
.PHONY: verilator-waves
verilator-waves: $(BUILD_DIR)/sim-common/waves.fst | .check-gtkwave
	gtkwave -a util/heepidermis_wave_viewer.gtkw $<

## @subsection QuestaSim RTL simulation

## Build simulation model
.PHONY: questasim-build
questasim-build: $(DPI_LIBS)
	$(FUSESOC) run --no-export --target sim --tool modelsim --build $(FUSESOC_FLAGS) epfl:cheep:cheep \
		$(FUSESOC_ARGS)
	cd $(QUESTA_SIM_DIR) ; make opt

## Build simulation model and launch simulation
.PHONY: questasim-sim
questasim-sim: | check-firmware questasim-build $(QUESTA_SIM_DIR)/logs/
	$(FUSESOC) run --no-export --target sim --tool modelsim --run $(FUSESOC_FLAGS) epfl:cheep:cheep \
		--firmware=$(FIRMWARE) \
		--boot_mode=$(BOOT_MODE) \
		--vcd_mode=$(VCD_MODE) \
		--max_cycles=$(MAX_CYCLES) \
		$(FUSESOC_ARGS)
	cat $(BUILD_DIR)/sim-common/uart.log

## Launch simulation
.PHONY: questasim-run
questasim-run: | check-firmware $(QUESTA_SIM_DIR)/logs/
	$(FUSESOC) run --no-export --target sim --tool modelsim --run $(FUSESOC_FLAGS) epfl:cheep:cheep \
		--firmware=$(FIRMWARE) \
		--boot_mode=$(BOOT_MODE) \
		--vcd_mode=$(VCD_MODE) \
		--max_cycles=$(MAX_CYCLES) \
		$(FUSESOC_ARGS)
	cat $(BUILD_DIR)/sim-common/uart.log

## Launch simulation in GUI mode
.PHONY: questasim-gui
questasim-gui: | check-firmware $(QUESTA_SIM_DIR)/logs/
	$(MAKE) -C $(QUESTA_SIM_DIR) run-gui RUN_OPT=1 PLUSARGS="firmware=$(FIRMWARE) boot_mode=$(BOOT_MODE) vcd_mode=$(VCD_MODE)"

## DPI libraries for QuestaSim
.PHONY: tb-dpi
tb-dpi: $(DPI_LIBS)
$(BUILD_DIR)/sw/sim/uartdpi.so: hw/vendor/x-heep/hw/vendor/lowrisc_opentitan/hw/dv/dpi/uartdpi/uartdpi.c | $(BUILD_DIR)/sw/sim/
	$(CC) -shared -Bsymbolic -fPIC -o $@ $< -lutil

## @section FPGA implementation

## Synthesis for FPGA
.PHONY: vivado-fpga-synth
vivado-fpga-synth:
	@echo "### Running FPGA implementation..."
	$(FUSESOC) run --no-export --target $(TARGET) --build $(FUSESOC_FLAGS) epfl:cheep:cheep $(FUSESOC_ARGS)

## Program the FPGA using Vivado
.PHONY: vivado-fpga-pgm
vivado-fpga-pgm:
	@echo "### Programming the FPGA..."
	$(MAKE) -C $(FUSESOC_BUILD_DIR)/$(TARGET)-vivado pgm

## @section Benchmarks

## Launch benchmark simulations on Verilator and generate CSV throughput report
.PHONY: benchmark-throughput
benchmark-throughput: build/performance-analysis/throughput.csv
build/performance-analysis/throughput.csv: $(THR_TESTS) | build/performance-analysis/
	@echo "### Running benchmark simulations for throughput extraction..."
	python3 scripts/performance-analysis/throughput-analysis.py \
		$(THR_TESTS) $@

## Launch benchmark simulations on post-layout netlist and generate CSV power report
.PHONY: benchmark-power
benchmark-power: build/performance-analysis/power.csv
build/performance-analysis/power.csv: $(PWR_TESTS) | build/performance-analysis/
	@echo "### Running benchmark simulations for power extraction..."
	python3 scripts/performance-analysis/power-analysis.py \
		$(PWR_TESTS) \
		build/sim-common $@

## Generate throughput benchmark chart
.PHONY: charts
charts: build/performance-analysis/power.csv build/performance-analysis/throughput.csv
	@echo "### Generating charts..."
	python3 scripts/performance-analysis/benchmark-charts.py $^ build/performance-analysis

## @section Software

## CHEEP applications
.PHONY: app
app: $(BUILD_DIR)/sw/app/
ifneq ($(APP_MAKE),)
	$(MAKE) -C $(dir $(APP_MAKE))
endif
ifeq ($(TOOLCHAIN), GCC)
	@echo "### Building application for SRAM execution with GCC compiler..."
	CDEFS=$(CDEFS) $(MAKE) -f $(XHEEP_MAKE) $(MAKECMDGOALS) LINKER=$(LINKER) LINK_FOLDER=$(LINK_FOLDER) ARCH=rv32imc RISCV=$(RISCV)
		TOOLCHAIN=$(TOOLCHAIN) $(FUSESOC_FLAGS) $(FUSESOC_ARGS)
	find sw/build/ -maxdepth 1 -type f -name "main.*" -exec cp '{}' $(BUILD_DIR)/sw/app/ \;
else
	$(error ### ERROR: Unsupported toolchain: $(TOOLCHAIN))
endif

## Dummy target to force software rebuild
$(PARAMS):
	@echo "### Rebuilding software..."

## @section Utilities

## Check if the firmware is compiled
.PHONY: .check-firmware
check-firmware:
	@if [ ! -f $(FIRMWARE) ]; then \
		echo "\033[31mError: FIRMWARE has not been compiled! Simulation won't work!\033[0m"; \
		exit 1; \
	fi

## Update vendored IPs
.PHONY: vendor-update
vendor-update:
	@echo "### Updating vendored IPs..."
	find hw/vendor -maxdepth 1 -type f -name "*.vendor.hjson" -exec python3 util/vendor.py -vU '{}' \;
	$(MAKE) cheep-gen

## Check if fusesoc is available
.PHONY: .check-fusesoc
.check-fusesoc:
	@if [ ! `which fusesoc` ]; then \
	printf -- "### ERROR: 'fusesoc' is not in PATH. Is the correct conda environment active?\n" >&2; \
	exit 1; fi

## Check if GTKWave is available
.PHONY: .check-gtkwave
.check-gtkwave:
	@if [ ! `which gtkwave` ]; then \
	printf -- "### ERROR: 'gtkwave' is not in PATH. Is the correct conda environment active?\n" >&2; \
	exit 1; fi

## Check simulation parameters
.PHONY: .verilator-check-params
.verilator-check-params:
	@if [ "$(BOOT_MODE)" = "flash" ]; then \
		echo "### ERROR: Verilator simulation with flash boot is not supported" >&2; \
		exit 1; \
	fi

## Create directories
%/:
	mkdir -p $@


## @section Cleaning

## Clean build directory
.PHONY: clean
clean:
	$(RM) hw/ip/cheep_top.sv
	$(RM) hw/ip/pad-ring/pad-ring.sv
	$(RM) sw/device/include/cheep.h
	$(RM) -r $(BUILD_DIR)
	$(MAKE) -C $(HEEP_DIR) clean-all


## @section Format and Variables

## Verible format
.PHONY: format
format:
	@echo "### Formatting cheep RTL files..."
	util/format-verible

.PHONY: lint
	@echo "### Linting cheep RTL files..."
	$(FUSESOC) run --no-export --target lint epfl:cheep:cheep


## Static analysis
.PHONY: lint
lint:
	@echo "### Checking cheep syntax and code style..."
	$(FUSESOC) run --no-export --target lint epfl:cheep:cheep

## Print variables
.PHONY: .print
.print:
	@echo "APP_MAKE: $(APP_MAKE)"
	@echo "KERNEL_PARAMS: $(KERNEL_PARAMS)"
	@echo "FUSESOC_ARGS: $(FUSESOC_ARGS)"


# ----- INCLUDE X-HEEP RULES ----- #
export CHEEP_CFG
export PAD_CFG
export EXTERNAL_DOMAINS
export FLASHWRITE_FILE
export HEEP_DIR = $(ROOT_DIR)/hw/vendor/x-heep
XHEEP_MAKE 		= $(HEEP_DIR)/external.mk
include $(XHEEP_MAKE)
