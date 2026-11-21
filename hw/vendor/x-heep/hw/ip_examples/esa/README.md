# Standalone ESA HW FIFO test

From the repository root, initialize the project tools and run the test:

```bash
source /home/juan/anaconda3/etc/profile.d/conda.sh
source .private/init.sh
hw/vendor/x-heep/hw/ip_examples/esa/run_tb.sh
```

The Verilator 4 testbench writes ESA's registers once before streaming, then
sends a deterministic 1000-sample signed neural-like signal every 100 clock
cycles. The signal combines a low frequency background, repeatable noise, and
biphasic spikes with deterministic pseudo-random intervals from 5 to 50 input
samples, inspired by the mock `rawNeural` signal in `ESA_for_DMA.m`.

ESA registers use the standard `reg_pkg::reg_req_t` / `reg_rsp_t` interface:

| Offset | Register | Meaning |
| --- | --- | --- |
| `0x00` | `enable` | Enables processing; writing zero clears both FIFOs and filter state |
| `0x04` | `hpf_enable` | Enables the signed first-difference high-pass, or bypasses it |
| `0x08` | `esa_window_shift` | ESA SES smoothing shift `Ww` (0–31) |
| `0x0c` | `esa_input_gain_shift` | ESA SES input gain shift `Wg` (0–31) |
| `0x10` | `feature_window_shift` | ESA feature SES smoothing shift `Ww` (0–31) |
| `0x14` | `feature_input_gain_shift` | ESA feature SES input gain shift `Wg` (0–31) |
| `0x18` | `output_decimation_rate` | ESA feature samples between FIFO outputs (must be nonzero) |

The processing chain consists of independent ready/available operations:

1. `esa_hpf_op` computes `x[n] - x[n-1]`, a simple high-pass with zero
   response to DC. It saturates to `+/-INT32_MAX` so the following magnitude
   fits in a positive signed 32-bit value.
2. `esa_abs_op` takes the two's-complement absolute value of the signed HPF
   result.
3. `esa_ses_average_op` produces the smoothed ESA output.
4. A second `esa_ses_average_op` produces `esa_feature`.
5. `esa_decimation_op` controls when features enter the output FIFO.

Both averaging stages use the SES recurrence
`accumulator += (input << Wg) - previous_average`, with
`output = accumulator >> Ww`, matching `ses_stage`. Setting `Wg = Ww` gives
unity steady-state gain; `Ww` controls smoothing. A shift of zero makes a stage
pass its input through. Configuration is held constant during streaming.

The standalone module resets disabled and keeps optional visualization latches
enabled. The `cheep_top` example enables ESA at reset because its register
interface is not yet mapped into the SoC peripheral bus; it disables the
visualization latches to omit those two registers from the chip. Its DMA example
also sets `InputSamplesPerTransaction` to 16 so ESA can signal completion after
processing and draining the test transfer.

The test checks generated features against a software model, their decimation
cadence, rejection of an invalid shift, and FIFO clearing when ESA is disabled.
It writes a VCD waveform to `hw/vendor/x-heep/hw/ip_examples/esa/build/esa.vcd`;
open it with the included signal view:

```bash
gtkwave hw/vendor/x-heep/hw/ip_examples/esa/build/esa.vcd \
  hw/vendor/x-heep/hw/ip_examples/esa/tb/esa.gtkw
```

`phase_i` is 1 during register setup and 2 during neural streaming. `.private/init.sh`
also attempts board frequency setup; a USB error from that step does not prevent
this local test from running.

The VCD includes `input_data_hold_q`, the output from each operation, and
`output_data_hold_q`. The GTKWave save file presents these in pipeline order as
analog traces.
