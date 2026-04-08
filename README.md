# Welcome!

<p align="center"><img src="./docs/img/cheep_logo.png" width="500"></p>

HEEPidermis is a chip based on the open-source RISC-V platform [X-HEEP](https://github.com/esl-epfl/x-heep) intended to record Galvanic Skin Response (GSR) and Impedance (Z).
HEEPidermis is capable of injecting arbitrary-wave currents on the skin with an 8-bit iDAC and measure voltage through a 14-bit VCO-based ADC.
Parameters of the iDAC and ADC can be programmed dynamically using the on-board Ibex CPU. Acquired data can be stored in the 16 kB RAM memory for on-chip processing, compression, and transmission.

HEEPidermis can be interfaced through JTAG for code-debugging, SPI to obtain recordings from memory, or UART.
External ADCs and peripherals can also be commanded via SPI.

The Direct Memory Access (DMA) block allows the chip to operate autonomously without the constant interaction of the CPU. Given proper configuration, the DMA can take care of controlling the current injection profile, read data from the local ADC (or an external one), store data and/or transmit it through SPI.
The DMA can additionally re-direct data to a digital Level Crossing (dLC) block to filter data and reduce the output data rate. The output of the dLC can be stored in memory (along timestamps) or be exposed to be used as feedback loop for the analog front-end, interface with transmission circuits or as input to Spiking Neural Networks.
The dLC block also accepts inputs from analog LC ADCs (instead of the on-chip ADC).

HEEPidermis can also decimate 1-bit ΔΣ-ADCs' output with an integrated CIC filter or, alternatively, with an integrated custom smoothing stage, followed by the dLC block.

Being based on the X-HEEP platform, you can check its documentation on how to program, debug, or understand how it was built. We suggest using the [vendorized X-HEEP's documentation](../../hw/vendor/x-heep/docs/source/index.rst).
For all HEEPidermis-specific documentation, please refer to the documentation below.

<p align="center"><img src="./docs/img/HEEPidermis-diagram.svg" width="1000"></p>

<p align="center">
<img src="./docs/img/sizing-v6.png" width="45%" style="display:inline-block;" >
&nbsp;&nbsp;&nbsp;
<img src="./docs/img/HEEPidermis-fingerprint.JPG" width="50%" style="display:inline-block;">
</p>


## Index

- **Analog Front End**
  - [Analog subsystem](hw/ip/analog_subsystem/README.md)
  - [iDAC](hw/vendor/analog-library/iDAC/docs/README.md)
  - [VCO](hw/vendor/analog-library/VCO/docs/README.md)

- **Digital Back End**
  - [CPU](./docs/source/DBE/CPU.md)
  - [DMA (how we use it)](./docs/source/DBE/DMA.md)
  - [DMA](hw/vendor/x-heep/docs/source/Peripherals/DMA.md)
  - [SPI host](hw/vendor/x-heep/docs/source/Peripherals/SPI.md)
  - [SPI slave](hw/vendor/x-heep/docs/source/Peripherals/SPI_slave.md)
  - [digital Level-Crossing](hw/vendor/x-heep/docs/source/ExternalPeripherals/dLC.md)
  - [ΔΣ decimation (CIC filter)](./docs/source/DBE/CIC_filter.md)
  - [ΔΣ decimation (SES filter)](./docs/source/DBE/SES_filter.md)

- **PCBs**
  - [CHEEP-boards](./hw/vendor/cheep-boards/) - To program and provide power
  - [Breakout board](./hw/vendor/breakout-board/) - To solder the cheep and connect it to the CHEEP-boards.

- **Related documents**
  - [HEEPidermis](https://arxiv.org/abs/2509.04528)
  - [ΔΣ Decimation theory](https://ieeexplore.ieee.org/abstract/document/11044062)
  - [iDAC precursor](https://ieeexplore.ieee.org/document/11044212)
  - [VCO Design](https://arxiv.org/abs/2509.06698)

## Evaluating trade-offs in GSR recording

HEEPidermis is a complex SoC, and acquiring GSR keepeing a proper range, sensitivity, dynamic range, resolution and power can be very tricky! To aid in the process of choosing the right parameters we have modeled the performance and consumption of our front-end and integrated it into a visual model with which you can play.

<p align="center"><img src="./docs/img/plotter.png" width="80%" style="display:inline-block;">
</p>

You can use the plotter in two different ways:

1. **Forward control**: You choose the operating variables (injected current and sampling frequency) and study the obtained sensitivity for a given baseline value of conductivity (G)
2. **Target**: You select a target sensitivty and power consumption and let the tool find the optimal configuration.

## Prerequisites

Since this project is based on _X-HEEP_, it has the same prerequisites.

1. Follow _X-HEEP_'s [_Prerequisite_](https://github.com/esl-epfl/x-heep/tree/main#prerequisite) section to download the necessary tools.
To correctly generate hardware and software, an extended version of the _core-v-mini-mcu_ environment is required. To install it run:
   ```Bash
   make conda
   ```
Make sure the Conda environment is active:
   ```Bash
   conda activate heepidermis
   ```

2. Make sure that the following tools are available in your system's `PATH`:
   - To compile code: **RISC-V toolchain version 2022.01.17**, supporting at least `rv32imc`. Newer versions will work but compilation will have a bug when reporting memory usage.
   - To format SystemVerilog (in case you want to open a PR): **Verible version 0.0-4023-gc1271a00**
   - To simulate (open source): **Verilator 4.210**
   - To see waveforms out of Verilator: **GTKWave**
   - In case you have the licences:
     - Siemens QuestaSim version 2020.4 or above
     - Synopsys Design Compiler version 2020.09 or above

You can follow the instructions on how to install these tools on the [X-HEEP documentation - Manual setup](https://x-heep.readthedocs.io/en/latest/GettingStarted/Setup.html#manual-setup).


## Preparing the Boards

The easiest way to test HEEPidermis is to use the [CHEEP-boards](./hw/vendor/cheep-boards/), which provide all the tools (sw and hardware) to supply power and program HEEPidermis.

HEEPidermis is soldered (or placed on a socket) on the [breakout board](./hw/vendor/breakout-board/).

After soldering everything, you also need to:

### Connect chewing-gums

The chewing-gum (CG) boards are small shields that are connected to the main board, and which can be used to provide power or reference voltages to HEEPidermis, and measure that consumption.

| CG | Function | Nominal value | CG | Function | Nominal value |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 6 | DCORE VDD | 0.8 V | 7 | ACORE VDD | 1.2 V |
| 5 | IO VDD | 1.8 V | 8 | iREF | Connect a source of 400 nA |
| 4 | not used | - | 9 | REF VDD | 1.2 V |
| 3 | not used | - | 10 | LDO VREF | 0.8 V |
| 2 | not used | - | 11 | VCO VDD | 0.8 V |
| 1 | not used | - | 12 | LDO VDD | 1.2 V |

> _Note that the bottom three CGs on the picture below are not used, they are just used to inject voltages here and there with an external cable for testing._

⚠️ Remember to solder a shunt resistor below the CG! ~10 Ω should do fine for the range 1-100 µW. Consider that the INA will amplify the drop by 1000-2000×, has an offset of some 10s µV, and it cannot output more than 3.3 V.

<p align="center"><img src="./docs/img/HEEPidermis-on-board.png" width="1000"></p>

### Connect jumpers and switches

On the main board
* Each CG has three jumpers. To control them with the trimmer, connect the jumper next to it towards its screw. Connect the jumper on the side away from the trimmer to turn the power on. Connect the jumper innermost to the main baord away from the test point to make the output go to the main board.
* Connect the jumper for the IO VDD (next to CG 8) on 1.8 V
* No need to connect any jumper next to the oscilaltor (bottom of the main board)

On the breakout board there are several places where to connect jumpers to use the cheep on different configurations. Mainly, there are two rulers of pin headers around the cheep which can be used to connect or disconnect supplies. Many of them are short-circuited on both sides, so you just use the headers as test points or to inject a signal. The rest are marked with what would get to the cheep if you close the jumper. Additionally there are three-pin jumpers for you to choose what to supply the cheep with: an ideal supply coming from the main board's chewing gums, or a voltage/current coming from the on-cheep supplies.

Check the table on [the breakout board's readme](./hw/vendor/cheep-boards/) to see what connects what.

> ❗There are two pads on the bottom of the board that need to be short-circuited: They will connect the clock input to the cheep. You can select where to take the clock, if you have no clue what to do, put some solder tin on the two pair of pads that point to the main board to take the clock from there. Don't worry if you make a mistake and short a bit the other, it's not the end of the world.

There is a pair of switches on the breakout board, to the left of the cheep. You can eiter solder the switches to select the boot mode, or you can short with tin them the mid-pin with the one closest to the cheep to only use JTAG mode (explained below).

### Bug correction

🪲 You need to cut the pin below the `value` pin (the outter most pin, in row 31 of the main board) in the connector to the main board or nothing will work, sorry.

## Programming the ASIC

You can program the ASIC through 3 alternatives:
 - JTAG
 - Loading an external flash that HEEPidermis can read through SPI
 - Loading the program in RAM through the SPI slave peripheral

### Programming through JTAG

You need to connect the JTAG interface to an FTDI chip. This is avaialble on the [EPFL programmer](https://github.com/esl-epfl/x-heep-programmer-pmod) or on the [CHEEP boards](https://eslgit.epfl.ch/heep/cheep-boards) (currently only available upon request).

1. The BOOT switches should be set to

| Signal     | Value |
|------------|-------|
| FLASH_EXEC | 0     |
| BOOT_SEL   | 0     |

2. Set the system clock to 1 MHz. You can do that in two steps if you have the CHEEP board, and have exported the variable `$CHEEP_BOARDS` pointing to the folder where the repo is.
```bash
make board_config
make board_freq PLL_FREQ=1_000_000  #default
```

3. Build the SW/HW stack of the cheep
```bash
make cheep-gen
```

4. Compile the code
```bash
make app BOOT_MODE=jtag
```

1. Open openOCD and picocom to see the UART output
```bash
make jtag_open
```
1. Open GDB and run the program
```bash
make jtag_run
```
Useful commands:
> `c` (continue)
>
> `monitor reset hart`
>
> `monitor pc _start`
>
> `si` (next instruction)
>
> `disassembler`
>
> `x/8i` (see the following instructions).

1. Exit GDB with `ctrl+C` and type `quit`
2. Close the openOCD and uart terminals with
```bash
make jtag_close
```


### Programming from the FLASH

Testing... we seem to have a bug on the main board that prevents this from working 😬

### Programming through SPI

Testing...


## RTL Simulation

The included [`makefile`](./makefile) proves all the necessary rules to build _HEEPidermis_:


1. Generate all the RTL files (and software headers):
   ```bash
   make cheep-gen
   ```
   Some _X-HEEP_ configuration parameters (e.g. `BUS`, `MEMORY_BANKS` etc.) are still supported, although the default configuration may differ (check [`peripherals_config`](./config/peripherals_config)). In general, these should not be passed through the makefile targets but should be configured in the hjson files in the `config` folder.

2. Build the simulation model:
   ```bash
   make verilator-build     # Verilator
   make questasim-build     # QuestaSim
   ```

3. The software build process is primarily handled by _X-HEEP_, and most of its [example applications](./hw/vendor/x-heep/sw/applications/) will work on _HEEPidermis_ as well. You can choose other `BOOT_MODE`, just be sure to match it with the one used with `make *-build` (by default they use `fore`). `force` will be the faster for simulation.

```bash
make app PROJECT=test_dlc_vco BOOT_MODE=force
```

The combinations of compatible linker and boot modes can be found here:
<p align="center"><img src="docs/img/linker_bootmode_combinations.png" width="300"></p>



1. Launch the simulation with the compiled firmware:
   ```bash
   make verilator-run       # Verilator with waveforms dump
   make verilator-opt       # Verilator without waveforms dump
   make questasim-run       # QuestaSim
   make questasim-gui       # QuestaSim with GUI
   ```

   You can also set other parameters like the `FUSESOC_FLAGS`, `LOG_LEVEL`, `BOOT_MODE`, `VCD_MODE`, etc. to customize the simulation. Refer to the [`makefile`](./makefile) for more details.

2. _HEEPidermis_ `stdout` is exposed through a UART DPI interface at `/dev/pts/<N>`, where `N` is a number printed on the simulation log during execution. You can connect to it using `screen` or similar tools, e.g.:
   ```bash
   screen /dev/pts/0
   ```
   The UART output is also stored to the `uart.log` file in the common simulation directory (`build/sim-common/uart.log`).
   > Using `printf()` will significantly increase the firmware execution time and therefore the simulation. For quick debugging, it recommended to use the return value from `main` instead. When it is _not_ zero, the testbench will print it out at the end of the simulation.

3. The waveforms dumped during the simulation can be opened using:
   ```bash
   make verilator-waves # after Verilator simulation
   make questasim-waves # after QuestaSim simulation
   ```

This will open the waveforms (example from `test_dlc_vco` application):
<p align="center"><img src="docs/img/example_waveforms.png" width="500"></p>


Assuming that your firmware is already available, steps 1., 3., and 4. can be repeated with a single command using the `verilator-sim` or `questasim-sim` targets.


# Questions?

Have questions? curiosities? ideas on how to exploit HEEPidermis? Want to contribute on following versions of the SoC?
Drop an email :) juan.sapriza@epfl.ch