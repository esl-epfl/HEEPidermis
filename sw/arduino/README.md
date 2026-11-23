# Programming through SPI

HEEPidermis can be programmed directly into SRAM through its SPI slave interface using an external microcontroller. The current implementation uses a Raspberry Pi Pico, which also provides an optional configurable system clock.

The programming tools are located in [`sw/arduino/`](./sw/arduino/):

- `heep_programmer/heep_programmer.ino`: microcontroller firmware for SPI transactions, CPU reset, memory readback, and clock generation.
- `heep_sdk.py`: Python SDK implementing the communication protocol and ELF programming.
- `heep_cli.py`: command-line interface built on top of the SDK.
- `spi.mk`: Makefile targets for programming and controlling the ASIC.

## Setup

Flash `heep_programmer.ino` onto the Raspberry Pi Pico using the Arduino IDE. The current pin assignments are:

| Pico GPIO | Function |
|---|---|
| GP16 | SPI MISO |
| GP17 | SPI CS |
| GP18 | SPI SCK |
| GP19 | SPI MOSI |
| GP21 | ASIC system clock output (GPOUT0), through R1 of the divider |
| GP8 | Divider reference (bottom of the divider, via R2): LOW while clocking, high-Z otherwise |
| GP14 / GP15 / GP13 | Serial / SPI-write / SPI-read activity LEDs |

Connect the signals to the appropriate ASIC board interfaces, ensuring compatible logic levels and a common ground.

The host requires Python with `pyserial` and `pyelftools`. Activate the HEEPidermis environment before proceeding.

From the repository root, list available serial ports and configure the external programmer:

```bash
make spi-ports
make spi-port PORT=/dev/ttyACM0
```

The selected port is saved locally in `sw/arduino/.spi-port`. It can also be overridden per command using `SPI_PORT=/dev/ttyACM1`.

## System clock control

The Pico generates the ASIC system clock through GPIO21 and a resistive divider (see below). With `CLOCK_ON_AT_STARTUP=0`, the clock is disabled at startup and both GP21 and GP8 are high-Z.

```bash
make programmer-clock-status
make programmer-clock-set CLK_FREQ=1000000
make programmer-clock-on
make programmer-clock-off
```

### Divider circuit

The VCOp output reaches the ASIC CLK pin through 470 Ω on the flex PCB (passive only). Its idle level at power-up is unknown (about LDO_VOUT ≈ 0.8 V seen, possibly 0 V), so the external clock must actively drive **both** edges:

```text
                    R1 330 Ω
 GP21 (GPOUT0) ────/\/\/\────┐
                              ├──── node ──── cable ──── ASIC CLK
 GP8 (LOW)  ────/\/\/\────────┘                          (VCOp -470 Ω-> CLK on the flex)
                    R2 82 Ω
```

With GP8 LOW the node swings 0 ↔ about `3.3 V · R2/(R1+R2)` ≈ 0.66 V (unloaded) behind a Thévenin resistance of about 66 Ω (R1‖R2). Both pins use 12 mA drive strength (GP21 with fast slew).

- **Clock on:** GP8 is driven LOW first, then GPOUT0 is routed to GP21.
- **Clock off:** GPOUT0 is stopped, then **both GP21 and GP8 become high-Z inputs with no pulls**. Either pin left as an output (even LOW) would load the VCO output through R1/R2, so once the VCO takes over it only sees the cable capacitance.

#### Checking the node level

The resistors are fixed (no trimmer), so the high level is set by R1/R2. To check it, with the cable **UNPLUGGED**:

1. `make programmer-clock-on`.
2. Scope the node: it should swing 0 ↔ about 0.66 V, at or slightly below LDO_VOUT. To change the level, change R1 or R2.
3. `make programmer-clock-off` (both pins back to high-Z).

The frequency is expressed in Hz. By default, frequency changes are prohibited while the clock is running. This behavior is controlled by `ALLOW_LIVE_CLOCK_FREQUENCY_CHANGE` in the Arduino firmware.

**Note:** This controls the ASIC system clock, not the SPI communication frequency. The SDK does not automatically enable the system clock before programming.

## Compiling and programming an application

First, compile the application for on-chip SRAM execution:

```bash
make app PROJECT=<application_name> BOOT_MODE=force
```

The resulting ELF file is normally located at `sw/build/main.elf`.

When the Pico supplies the ASIC system clock, enable it before accessing the chip:

```bash
make programmer-clock-on
```

Program the application:

```bash
make spi-program
```

Optional arguments enable memory verification and application execution:

```bash
make spi-program verify
make spi-program run
make spi-program verify run
```

By default, the programmer resets the CPU to Boot ROM before loading the ELF into SRAM. Verification reads the programmed memory back through SPI and compares it byte-for-byte against the expected image. If `run` is specified, the CPU starts executing from the ELF entry point after programming and optional verification.

A different ELF can be selected using:

```bash
make spi-program SPI_ELF=path/to/application.elf verify run
```

CPU reset can also be triggered independently:

```bash
make spi-reset
```

This resets the CPU domain through the power-manager registers; it **does not power-cycle** or reset the entire chip. For details on the registers involved and the reset sequence, see [Software-controlled CPU reset](./docs/software-reset.md).

The `no-reset` modifier is available for advanced use:

```bash
make spi-program no-reset
```

It should only be used when the CPU state is already controlled, since a running application could modify SRAM during programming or verification.

## SPI Programmer functions and debugging

The SPI programmer also supports direct access to the ASIC's memory and memory-mapped registers. This is particularly useful for debugging, as it allows reading and writing individual 32-bit words without compiling or uploading a new application.

These operations are exposed through `heep_cli.py`, which communicates with the Raspberry Pi Pico running `heep_programmer.ino`.

All commands below are executed from the repository root.

### Reading and writing 32-bit words

To read a 32-bit word from a given address:

```bash
python3 sw/arduino/heep_cli.py read32 0xEFC
```

The command returns the address and its contents in hexadecimal:

```text
0x00000EFC: 0x557D8082
```

To write a 32-bit value to an address:

```bash
python3 sw/arduino/heep_cli.py write32 0xEFC 0x557D8082
```

The programmer confirms that the write transaction was issued:

```text
0x00000EFC <- 0x557D8082
```

A write acknowledgement does not guarantee that the target memory or register retained the value. To verify the operation, read the address again:

```bash
python3 sw/arduino/heep_cli.py read32 0xEFC
```

Both addresses and values can be specified in hexadecimal (`0x...`) or decimal. Addresses must be 4-byte aligned, and write values must fit in an unsigned 32-bit word.

### Serial port selection

By default, the CLI uses `/dev/ttyACM0`. A different port can be specified with `--port`:

```bash
python3 sw/arduino/heep_cli.py --port /dev/ttyACM1 read32 0xEFC
```

Unlike the Makefile commands, direct CLI invocations do not automatically use the port saved by `make spi-port`. The port can also be configured using the `HEEP_SPI_PORT` environment variable.

### Important considerations

- The ASIC must have a working system clock for memory access. If the Pico provides the clock, enable it using `make programmer-clock-on`.
- Memory access is performed through the SPI slave interface and does not require the CPU to execute a program.
- Read and write operations do not automatically reset or halt the CPU. A running application may modify memory concurrently.
- Writing to memory-mapped registers can immediately affect peripheral or system behavior. Some registers may be read-only, self-clearing, or have side effects.
- The example address above is illustrative; use an address that is safe to access in the current application.

For larger or automated memory transfers, the Python SDK in `heep_sdk.py` also exposes `read_block()` and `write_block()`.

# Running HEEPidermis from the internal VCO clock

HEEPidermis can operate using **VCOp as its system clock**, removing the need for a continuous external clock. VCOn remains available for skin-conductance sensing, while VCOp can also be monitored as a reference for supply-voltage variations and pseudo-differential measurements.

**1. Initialize VCOp using the external clock**

Start with the Raspberry Pi Pico providing the system clock (1 MHz):

```bash
make programmer-clock-on
make spi-reset
make VCO_clock
```

`make VCO_clock` enables VCOp directly through SPI and verifies the ENABLE register at `0x2007100C`. It preserves VCOn and leaves an already enabled VCOp running without toggling it. No initialization application is compiled, uploaded, or executed. Keep the CPU in Boot ROM during this operation to avoid concurrent register changes. If the readback succeeds, the Pico firmware turns its clock off immediately, as part of the same command; if it fails, the clock stays on. Readback confirms the enable setting, not that the VCO is oscillating, and there is no pause to check the VCO output first. If the ASIC stops responding, restore the external clock with `make programmer-clock-on`.

**2. Switch to the VCO clock**

The Pico clock is already off after `make VCO_clock`. The CPU is already waiting in Boot ROM from step 1. CPU-domain resets preserve the VCO configuration.

**Handover:** no physical switching is needed. Sequence: `make programmer-clock-on` → `make VCO_clock` → `make programmer-clock-off` (VCO_clock already stops the clock on success; this is a harmless explicit step and the fallback if you stop manually) → both GP21 and GP8 are high-Z, VCOp drives CLK alone → `make spi-reset` (CPU-domain reset, now clocked by the VCO).

**Warnings:**
- Do not leave the cable plugged into an unpowered Pico: the Pico pins then cannot stay high-Z and would load or clamp the VCO output.
- The off state is **high-Z, not LOW**. The CLK node is undefined unless the VCO drives it.

**3. Compile and program subsequent applications**

Any application running with VCOp as the clock source must be compiled with:

```bash
make app PROJECT=test_VCO_counter BOOT_MODE=force CDEFS=VCO_IS_SYSCLK
```

The `VCO_IS_SYSCLK` compile-time definition activates protection in `sw/external/lib/drivers/VCO_decoder/VCO_decoder.h`, making calls to `VCOp_enable(true)` and `VCOp_enable(false)` no-ops.

VCOn remains independently configurable.

Program and execute the application using:

```bash
make spi-program verify run
```

The default SPI programming reset only resets the CPU domain, preserving the running VCO. This allows successive applications to be uploaded without switching back to the external clock.

<!-- **4. Limitations and precautions**

- Never directly modify the VCOp enable bit while it supplies the system clock. The compile-time flag protects calls through the driver, not arbitrary register writes.
- When both VCOs are enabled, the hardware decoder's combined output represents the difference between their measurements. Independent P/N coarse counters remain readable through `VCOp_get_coarse()` and `VCOn_get_coarse()`.
- Avoid modifying VCOp biasing or supply conditions in ways that could stop oscillation or change the clock frequency outside the CPU's operating limits.
- When changing `CDEFS`, ensure CMake is reconfigured so that the new definition is actually applied.
- The VCO clock configuration is retained across the tested CPU-domain reset, **not** across power cycling or a full hardware reset.
- All SPI logic levels must remain compatible; the tested setup uses discrete level shifting for the ASIC-to-Pico MISO signal. -->

**Validation:** The previous firmware-based clock handover and subsequent SPI programming were demonstrated on hardware. The direct SPI enable path requires hardware validation; register readback alone does not verify oscillation.