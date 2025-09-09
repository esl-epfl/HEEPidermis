#  DMA

We have 2 DMA channels. Each DMA channel has an explicit purpose: 

1) For the ADC - to read data from the VCO-ADC decoder
2) For the iDAC - to write data to the iDAC controller

Two DMA channels are needed in order to perform transactions in parallel (injecting current and reading at the same time). 

## The ADC DMA

The ADC DMA should make a reading from the VCO decoder `value` register shortly after the reading value is ready. For this, we have included a dedicated [ADC-timer](./Timers.md) instantiated on the external peripheral subsystem. In SW the timer should be set to the sampling frequency of the ADC. When the timer count has finished it will trigger a refresh signal in the ADC decoder. This will propagate first to the VCO-ADC to get a sample, and few clock cycles later to an `vco_data_ready` signal that is used as a trigger for the DMA through `ext_dma_slot_rx[0]`. This slot enables the DMA to perform one data movement, from the source target (the decoder's `value` register) to a pre-configured destination. 

The ADC DMA is additionally connected to a **streaming accelerator: [the dLC block](./dLC.md)** on the HW-FIFO interface. It can be configured to pass the data through the dLC. This filters the data (decides if and what should be stored) and can proceed to store the resulting value instead of the original one obtained from the VCO-ADC.    

## The DAC DMA

The DAC DMA is used to write into the iDAC registers (primarly the `value` register). To perform this operation at the iDACs refresh-rate, the trigger is controlled by a dedicated [DAC-timer](./Timers.md) also on the external peripheral subsystem. 

## Alternative use: dual-channel VCO + DC current. 

In the case of using DC current, the DAC-DMA will be free, so it could be used to control a second VCO-ADC. 

> ⚠️⚠️⚠️ @ToDo: HOW???!!


## Alternative use: Double-tap operation of the VCO-ADC

If the sampling frequency for the ADC is too slow the counter that keeps track of the number of oscillations will overflow. One alternative to make readings in this case is to do a double-tap: Making a measurement, waiting a short period of time (at some point we had done the math and it was ~300 µs) and perform another reading. 

> ⚠️⚠️⚠️ @ToDo: HOW???!!


## Controlling it from software

The DMAs are completely compatible with the X-HEEP DMA, so please refer to its [documentation](https://x-heep.readthedocs.io/en/latest/Peripherals/DMA.html#) and [examples](https://github.com/esl-epfl/x-heep/tree/main/sw/applications/example_dma) for use cases. 

Below is basic use case configuring the DAC-DMA to write into the iDAC and the ADC-DMA to read the resulting values from the VCO-ADC. 

<details>
<summary>Example code to configure the DMAs</summary>

🎵 Never gonna give you up... 🎶  👀

</details>


## Questions? 

| Topic | Responsible |
|---------------|---------------|
| Mutlichannel | Tommaso Terzano |
| Reconfigurability | Tommaso Terzano |
| HW FIFO | Alessio Naclerio |
| Software | Juan Sapriza, Tommaso Terzano |
| General | Juan Sapriza, Tommaso Terzano, Davide Schiavone |