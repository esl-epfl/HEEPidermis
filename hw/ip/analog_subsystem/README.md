# Analog subsystem

The analog subsystem (ss), instantiated at `cheep_top` level, connects to the `cheep_peripherals` ss to get all the control signals for the analog blocks.

## Cheep Peripherals level

In the `cheep_peripherals` ss we now have the RTL digital back-end of the analog blocks: `vco_decoder` and `idac_ctrl`. These have a register interface where configurations are written to, they parse them and also generate control signals (e.g. `refresh`).

## Cheep top level

At `cheep_top` level, control signals are passed both to the analog ss and to the DMA (refresh notifications).

## Analog subsystem level

Here the digital signals are passed to the analog block models. Depending on the type of simulation, one or another block will be instantiated. All the different versions of these blocks are stored in `hw/vendor/analog-library`. Please refer to the documentation there to see how to add new blocks.