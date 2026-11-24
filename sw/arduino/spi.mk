# HEEPidermis external SPI programmer and ASIC clock control.
# Include in the repository's root makefile:
#   include sw/arduino/spi.mk
# before the X-HEEP external.mk inclusion.
#
# Examples:
#   make spi-ports
#   make spi-port PORT=/dev/ttyACM1
#   make programmer-clock-status
#   make programmer-clock-set CLK_FREQ=10000000
#   make programmer-clock-on
#   make spi-program verify run
#
# The clock is never implicitly enabled by these targets.

SPI_CONFIG_FILE := $(ROOT_DIR)/sw/arduino/.spi-port
SPI_PORT ?= $(shell if test -f "$(SPI_CONFIG_FILE)"; then cat "$(SPI_CONFIG_FILE)"; else printf '%s' '/dev/ttyACM0'; fi)
SPI_PYTHON ?= $(PYTHON)
SPI_CLI := $(ROOT_DIR)/sw/arduino/heep_cli.py
CLK_FREQ ?= 1000000
SPI_ELF ?= $(ROOT_DIR)/sw/build/main.elf

## Enable VCOp directly through SPI; external clock required, CPU in Boot ROM.
## Pico firmware verifies readback, then stops its clock. Does not load firmware.
.PHONY: VCO_clock
VCO_clock:
	$(SPI_PYTHON) $(SPI_CLI) --port "$(SPI_PORT)" vco-enable

# Lowercase Make goals are optional modifiers: 'make spi-program verify run'.
SPI_VERIFY_OPT = $(if $(filter verify,$(MAKECMDGOALS)),--verify,)
SPI_RUN_OPT = $(if $(filter run,$(MAKECMDGOALS)),--run,)
SPI_NO_RESET_OPT = $(if $(filter no-reset,$(MAKECMDGOALS)),--no-reset,)

.PHONY: spi-ports spi-port programmer-clock-status programmer-clock-on programmer-clock-off programmer-clock-set spi-reset spi-program verify run no-reset

## List serial ports currently visible to the host.
spi-ports:
	$(SPI_PYTHON) -m serial.tools.list_ports -v

## Save/show a local port. Example: make spi-port PORT=/dev/ttyACM1
## The saved path is intentionally ignored by Git.
spi-port:
	@if test -n "$(PORT)"; then \
		printf '%s\n' "$(PORT)" > "$(SPI_CONFIG_FILE)"; \
		printf 'Saved SPI programmer port: %s\n' "$(PORT)"; \
	else \
		printf 'SPI programmer port: %s\n' "$(SPI_PORT)"; \
	fi

## Report ASIC clock state and frequency.
programmer-clock-status:
	$(SPI_PYTHON) $(SPI_CLI) --port "$(SPI_PORT)" clock status

## Turn ASIC clock on at its previously configured frequency.
programmer-clock-on:
	$(SPI_PYTHON) $(SPI_CLI) --port "$(SPI_PORT)" clock on

## Turn ASIC clock off (GPIO21 and GPIO8 high-Z).
programmer-clock-off:
	$(SPI_PYTHON) $(SPI_CLI) --port "$(SPI_PORT)" clock off

## Set ASIC clock frequency while OFF. Example: make programmer-clock-set SPI_CLK_FREQ=2000000
programmer-clock-set:
	$(SPI_PYTHON) $(SPI_CLI) --port "$(SPI_PORT)" clock set "$(CLK_FREQ)"

## Reset the ASIC CPU domain to Boot ROM (not the whole board).
spi-reset:
	$(SPI_PYTHON) $(SPI_CLI) --port "$(SPI_PORT)" reset

## Program SRAM from ELF (default sw/build/main.elf).
## Optional goals: verify, run, no-reset; e.g. make spi-program verify run
## A functional ASIC clock is required; we do not enable it automatically.
spi-program:
	$(SPI_PYTHON) $(SPI_CLI) --port "$(SPI_PORT)" program "$(SPI_ELF)" \
		$(SPI_VERIFY_OPT) $(SPI_RUN_OPT) $(SPI_NO_RESET_OPT)

# 'verify', 'run', and 'no-reset' are Make goals, not standalone operations.
verify run no-reset:
	@if test -z "$(filter spi-program,$(MAKECMDGOALS))"; then \
		echo 'Use with make spi-program, e.g. make spi-program verify run' >&2; \
		exit 2; \
	fi
