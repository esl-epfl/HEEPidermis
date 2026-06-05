# GSR Evaluation Workflow

This directory contains a small workflow for evaluating the GSR
feature-extraction path against simulation output. The main idea is to run the
same native FE code on:

- the original conductance trace, which gives the ground truth;
- the reconstructed conductance printed by `test_reconstruction`, which gives
  the simulation result.

The flow is:

1. Run `test_reconstruction` and save its UART conductance output.
2. Generate `input_signal.h` from `conductance.txt` and produce the GT FE CSV.
3. Generate `input_signal.h` from the simulation output and produce the sim FE
   CSV.
4. Compare the two FE CSV files with `compare.py`.

## Files

- `txt_to_c_array.py`: converts a text signal into `input_signal.h` so the
  native FE code can run on it.
- `sim.txt`: default input file used by `txt_to_c_array.py`.
- `conductance.txt`: raw conductance input used by the analog resistor model.
- `input_signal.h`: generated C array consumed by the native FE build.
- `main.c`, `descompv5.c`, `descompv5.h`: native C feature-extraction path.
- `Makefile`: builds and runs the native host executable. We do not run this
  path on chip because the arrays are too large.
- `compare.py`: compares ground-truth FE output with simulation FE output.
- `plot_gt_fe_output.py`: plots a ground-truth CSV over a selected sample range.

## 1. Run `test_reconstruction`

Run the simulation workflow from the repository root. A typical Verilator flow
is:

```bash
make app PROJECT=test_reconstruction BOOT_MODE=force
make verilator-run BOOT_MODE=force
```

The UART output is stored in:

build/sim-common/uart.log

Copy the numeric conductance samples into `sim.txt`.

## 2. Produce the Ground-Truth FE CSV

First generate `input_signal.h` from the original conductance trace. For the
full `conductance.txt` file, use the same window as `resistor.sv`:

```python
INPUT_TXT_FILE = _SCRIPT_DIR / "conductance.txt"
OUTPUT_FILE = _SCRIPT_DIR / "input_signal.h"
START_INDEX = 499999
END_INDEX = 600000
DIVISOR = 1000
```

Notes:

- `START_INDEX` is zero-indexed, so line `500000` in `conductance.txt` is index
  `499999`.
- `END_INDEX` is exclusive, so `600000` includes line `600000`.
- `conductance.txt` is in pS codes; `DIVISOR = 1000` converts it to nS.

Then run:

```bash
cd sw/applications/gsr_eval
python3 txt_to_c_array.py \
  --input conductance.txt \
  --start 499999 \
  --end 600000 \
  --divisor 1000
make clean
make run
cp fe_output_xHz.csv gt_fe_output.csv
```

This writes `input_signal.h`, defining:

- `signal_length`
- `signal[]`

and then writes the native FE output CSV.

### Produce a rate-matched GT FE CSV

If `conductance.txt` is sampled faster than the simulation FE input, downsample
the raw GT before running FE. For example, 200 Hz GT versus 20 Hz simulation
uses a factor of 10:

```bash
python3 txt_to_c_array.py \
  --input conductance.txt \
  --start 499999 \
  --end 600000 \
  --divisor 1000 \
  --downsample-factor 10 \
  --downsample-mode pick
make clean
make run
cp fe_output_xHz.csv gt_fe_output_20Hz.csv
```

Use `--downsample-mode pick` when the simulation samples an instantaneous
conductance value. Use `--downsample-mode mean` only if the simulated
measurement averages over the interval. The generated CSV header includes
`sample_step`, and `compare.py` uses it automatically. In general, use the
`Nominal GT step/sample` printed by the old high-rate comparison as the
downsample factor when it is an integer.

To test the opposite assumption, oversample a slower input before FE. This is
useful for checking the alternative where the reconstructed or simulated
waveform is expanded up to the GT rate:

```bash
python3 txt_to_c_array.py \
  --input sim.txt \
  --start 0 \
  --end none \
  --divisor 1 \
  --downsample-factor 1 \
  --oversample-factor 10 \
  --oversample-mode hold
make clean
make run
cp fe_output_xHz.csv sim_fe_output_20Hz_oversampled_hold.csv
```

Use `--oversample-mode hold` or `zoh` for zero-order hold, and
`--oversample-mode linear` for linear interpolation. Oversampling writes a
fractional `sample_step` such as `0.1`; `compare.py` uses this metadata when
mapping simulation FE rows back to GT rows.

## 3. Produce the Simulation FE CSV

Now run the same native FE code on the reconstructed conductance samples from
simulation. These are the equivalent converter settings:

```python
INPUT_TXT_FILE = _SCRIPT_DIR / "sim.txt"
OUTPUT_FILE = _SCRIPT_DIR / "input_signal.h"
START_INDEX = 0
END_INDEX = None
DIVISOR = 1
```

Then run:

```bash
python3 txt_to_c_array.py --input sim.txt --start 0 --end none --divisor 1
make clean
make run
cp fe_output_xHz.csv sim_fe_output_2Hz.csv
```

Use a filename that includes the sampling frequency, such as
`sim_fe_output_2Hz.csv`, or pass the sampling frequency explicitly to
`compare.py` with `--sampling-freq`.

## 4. Compare Ground Truth and Simulation

Run:

```bash
python3 compare.py gt_fe_output.csv sim_fe_output_2Hz.csv
```

Useful options:

```bash
python3 compare.py gt_fe_output.csv sim_fe_output_2Hz.csv --plot
python3 compare.py gt_fe_output.csv sim_fe_output_2Hz.csv --save-plot=compare_plot.png
python3 compare.py gt_fe_output.csv sim_fe_output_2Hz.csv --gt-skip 10
python3 compare.py gt_fe_output.csv sim_fe_output_2Hz.csv --trim 5
python3 compare.py gt_fe_output.csv sim_fe_output_2Hz.csv --sampling-freq 2
python3 compare.py gt_fe_output.csv sim_fe_output_2Hz.csv --align nominal
python3 compare.py gt_fe_output.csv sim_fe_output_2Hz.csv --align fit --fit-offset
```

For a rate-matched GT CSV, use it directly:

```bash
python3 compare.py gt_fe_output_20Hz.csv sim_fe_output_20Hz.csv --sampling-freq 20 --plot
```

The comparison reports NRMSE for:

- `Signal (g_nS)`
- `Tonic`
- `Phasic`

Lower NRMSE is better. For `NRMSE_db`, more negative values are better.

## Alignment Notes

`compare.py` supports three alignment modes:

- `--align nominal`: use metadata directly.
- `--align fit`: fit an effective GT step to the data.
- `--align auto`: start from nominal and only use the fitted step if it is much
  better.

If the first simulation samples include initialization overhead, use
`--gt-skip`. If the last samples include end-of-window artifacts, use `--trim`.

If metadata is missing from the simulation CSV, these defaults are used:

- `SAMPLING_FREQ`: inferred from the filename, otherwise `10 Hz`
- `GT_LINE_START`: from GT CSV metadata, otherwise `500000`
- `GT_CHANGE_RATE_HZ`: `20000`
- simulation refresh multiplier: `100`
