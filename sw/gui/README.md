# HEEPidermis GUI

Launch the desktop interface from the repository root with:

```sh
make gui
```

It uses Tk from Python's standard library and `pyserial` for the UART. The
power and resolution plots also use NumPy, pandas, and SciPy. On Ubuntu,
install `python3-tk`, then the GUI dependencies:

```sh
python3 -m pip install -r sw/gui/requirements.txt
```

Use the same Python environment when launching `make gui`.

The default application is `gsr/demo` (`sw/applications/gsr/demo/main.c`).
It is also the Makefile's default `PROJECT` for application builds.
Select an application and press **Build** to invoke `make jtag_build`. The green
**▶ RECORD** button opens OpenOCD and the serial port, then runs GDB inside the
app. GDB loads and starts the target, then disconnects so JTAG can update the
current and sampling rate during recording. The button becomes red **▮▮ PAUSE RECORDING** while recording; after pausing, it offers **Continue recording** if history remains, or **Start recording** after Reset.
Pause closes the serial port and invokes `make jtag_close`. Closing the window also
cleans up connections started by the GUI. **RESET** clears the plotted and
in-memory history and starts a new session file with the next sample; previous
session files are kept.

Set **MCU frequency** in Hz and press **Config. board** to run
`make board_freq PLL_FREQ=...`. The GUI sets the UART baud rate to one twentieth
of that frequency, updates `SYS_FCLK_HZ` in the demo and legacy VCO applications,
and reconnects the serial port if it is open. After **Config. board** succeeds,
the GUI asks you to reset the hardware. That reminder clears after GDB successfully
opens the target. Rebuild the application to apply the new timer setting. The serial
port and baud rate are also editable. Board PLL programming alone does not verify
that the MCU can run or halt at the selected frequency. If OpenOCD reports
`Hart 0 failed to halt during examine()`, the GUI stops before attempting GDB;
return to a known-working clock (10 MHz on the tested board), reset the board,
and rebuild. The physical cause of a failed halt needs a board-level clock,
power, and JTAG check; changing the firmware clock constant cannot repair it.

Set **Sampling frequency** with the slider below **Record**. It offers
0.1–0.9 Hz, 1–9 Hz, 10–90 Hz, and the same 1–9 steps through 10,000 Hz. It
starts at the rate in the demo source. Moving the slider while recording writes
the selected rate to the firmware over JTAG; the firmware rereads the volatile
setting and reconfigures VCO acquisition. Build still sets the startup rate.
For rates below 1 Hz, `gsr/demo` refreshes the VCO at 1 Hz and emits samples
at the chosen average rate. `test_VCO_counter` requires at least 1 Hz.
The current slider also sits below **Record**. It selects iDAC codes 1–255,
corresponding exactly to 40–10200 nA in 40 nA steps. During recording, releasing
the slider writes its value to firmware over JTAG and saves it as the startup
current for the next GUI/build. The initial startup current is approximately 500 nA;
the last selected value is retained. If the
existing firmware image has a different startup value, the GUI applies the saved
current after GDB starts the target. Build writes the selected startup frequency
and current to the source, runs `make jtag_build`, and
shows the `Cont` and `IntL` bank use lines from X-HEEP's memory report directly
below the Build button. Build output is available in the terminal panel at the
bottom. The legacy `test_VCO_counter` application remains
selectable; its sampling frequency is also applied at Build. Very high rates
can exceed the UART's capacity to print every sample.

In the saved UART trace from 2026-10-31, changing N sampling from 10 to 20 Hz
produced a strong alternating N-frequency component while P stayed comparatively
steady; reducing the MCU clock to 5 MHz did not remove it. This is consistent
with 50 Hz mains pickup aliasing to 10 Hz at the 20 Hz Nyquist frequency,
although hardware confirmation is still needed. Check grounding, electrode
contact and shielding, and compare another sampling rate such as 10 or 30 Hz.
The raw UART view and raw plot points are intentionally not filtered; the
moving-average control affects only processed lines and decomposition.

The demo configures the iDAC and reads synchronized coarse/fine counters and
the signed hardware difference through the VCO SDK. Startup, stale and missed
frames are excluded. Fresh out-of-range frames remain available for diagnostics,
but do not enter conductance analysis or peak detection. The SDK is given the
configured board clock and real hardware timing by the demo.

The bottom row contains **Output**, **Messages**, **Save / Load**, and
**Navigation**. Firmware information starts with `[i]` on UART and appears in cyan with a timestamp;
GUI guidance appears in amber. It includes rebuild/reset reminders, pending
settings changes, acknowledgments, and acquisition feedback. The bottom output
area has two columns: system/build/debug output on the left and the exact raw
UART lines from the chip on the right. Its header describes only the selected
raw (`R`) and dLC (`D`) acquisition formats that are actually being transmitted,
with one format per line rather than every control frame.
The average stored rate and color legend appear above the memory-capacity bar. Consecutive
identical messages are collapsed only in the Messages panel.

Missing fresh counters, out-of-range channels and missed refreshes generate
throttled `[i]` feedback about checking contact, adjusting current or lowering
the sampling rate. Invalid samples retain their raw diagnostics but do not enter
decomposition or peak detection. With P serving as supply/reference and N as
the tissue signal, the nominal front-end relation is `N = P − current/conductance`.
Settings remain manual; firmware does not change them automatically.

While recording `gsr/demo`, move either runtime slider and release it. The GUI
writes its firmware volatile word through OpenOCD/JTAG, then reads it back. The
firmware picks up the new value; it reconfigures the VCO refresh rate for a
sampling change. It applies current directly to the iDAC, checks its register, and reseeds
acquisition so a pre-change frame is not labeled with the new current. UART `R` records
include both frequencies, raw coarse/fine counter registers, the selected
on-chip difference (except GUI mode), current, and a chip timestamp. See below
for the complete protocol. The legacy four-integer format is still accepted.
The GUI inverts the nominal VCO transfer curve to estimate voltages for plotting.
Informative lines are separate from samples. `[i] Current set: <nA> nA` and
`[i] Sampling set: <mHz> mHz` confirm settings even when no valid signal is present.
The dLC enable, level width and time-bit controls use the same atomic JTAG/readback
path and wait for `A`/`B` metadata confirmation. The GUI waits for these firmware
acknowledgments after JTAG writes. The demo still accepts
`I=<nA>` commands on UART for use outside the GUI.

The upper right plot shows P and N in mV, auto-scaled
to the visible samples with about 5% headroom so the data uses roughly 90% of
the plot height. The second right plot shows tissue conductance in µS using
the current reported with each sample. These two plots show raw samples as faint points and filtered
values as lines. With dLC the conductance line is stepped and the moving average
is bypassed for plots, operating points and decomposition. The default moving average window is 10 samples. The
conductance line is calculated from the moving average of P − N. Set the
averaging window to 1 to see the unfiltered signal. The x axes show seconds
since recording started, including pauses and missing data. Use the **Time zoom −/+** buttons to adjust the visible
time range, drag the horizontal scrollbar to review earlier samples, and press
**Live** to return to the newest data. The mouse wheel or a two-finger touchpad
gesture scrolls vertically over the left menu and horizontally through time
when the pointer is over the plots. Wheel gestures over entries, spinboxes,
comboboxes and sliders navigate without changing their values.

All three plots in the right column use the same time viewport, plot bounds,
cursor and vertical grid. The time labels are shown once on the bottom plot as
their common x-axis.

Conductance uses the physical signed drop
`G_µS = current_µA × 1,000,000 / (P_µV − N_µV − offset_µV)`; zero or reversed
drops are invalid rather than being rectified. Filtering restarts whenever the
current, sample rate, source, validity or acquisition segment changes, so a
moving-average window never combines two current settings. The calibrated P−N
offset defaults to zero and should only be changed from a known-load
calibration. For a fixed resistor within the front-end operating range, the
corrected voltage drop should scale with current. A channel offset can cause
apparent current dependence, especially at low currents. Verify the nominal
VCO conversion and actual iDAC current against a known resistor before relying
on absolute conductance values.
After a live current step, the first second is marked invalid while the iDAC,
tissue and VCO measurements settle, preventing transition samples from appearing
as a conductance jump.

Click or drag over any right-hand plot to place a shared vertical time cursor.
The middle column then shows the transfer operating point and estimates averaged
through that selected sample. **Live** moves the cursor back to the newest sample
and resumes following incoming data.

Every sample, including its injected current and computed conductance, is
appended immediately to a session CSV under `sw/gui/outs/`.
**Save CSV…**, below Messages, exports the full session to a chosen path. The suggested filename
is the time of the Save action in `yyyy_mm_dd_hhmm.csv` format. Exported conductance is
`current_µA × 1,000,000 / (P_µV − N_µV − offset_µV)` in µS; it is blank
when the corrected drop is zero or negative.
Each sample retains the current active when it was recorded. New firmware also
reports the hardware iDAC code in every acquisition frame; the GUI checks that
`code × 40 nA` matches the reported current and rejects mismatches. Both the session
and exported CSV include that current, so a change between recordings does
not alter earlier conductance values.
Each CSV is accompanied by `<recording>.csv.configuration.json` and
`<recording>.csv.uart.log`. The first sidecar preserves the GUI configuration;
the second preserves every raw line received from the chip. Both are updated
with a live session, written with Save CSV, and automatically restored before a
recording enters explorer mode. Legacy CSVs without sidecars still load.

**Load recording…** opens either a continuously written session CSV or an
exported CSV without connecting to hardware. The loaded data can be browsed,
zoomed, re-screened and re-analyzed with the normal processing controls, then
exported again. Starting a new hardware recording clears the explorer history;
the source recording is never modified. Very old recordings without an injected
current column use the current selected in the GUI at import time; current files
always retain their stored per-sample current.
While a recording is loaded, hardware/run controls are disabled and shown
grayed out so the explorer cannot accidentally change a run configuration.
Processing, visualization, export and configuration-file actions remain
available. Press **RESET** to leave explorer mode and re-enable run controls.

Every GUI parameter is atomically mirrored to
`sw/gui/last_configuration.json` after it changes. Startup restores that file.
**Save configuration…** writes a named JSON configuration and **Load
configuration…** validates and applies one; acquisition settings take effect on
the device after the corresponding Build or live update.
Hover over a configuration control for one second to see a short explanation
of what it changes and whether it acts at build time, run time, or in analysis.

The left column is 575 pixels wide (15% wider than before); the extra 75 pixels
come from the right column, leaving the middle column at its existing width.

The middle panel shows the VCO transfer curve from the repository CSV and the
latest P and N operating points mapped from the firmware frequencies. Solid
markers show measured points (averaged in fixed-rate mode); small rings show the
corresponding locations on the nominal curve. The markers use the same averaging as the signal lines: the selected window
in fixed-rate mode and the selected instantaneous sample in dLC mode.
The wider middle column shares its available height evenly among the transfer,
power, and resolution plots, keeping all three visible. The interface uses a
dark theme.

Below the transfer curve, the resolution map shows estimated bits across
conductance and sample rate at the current injection setting. The power map is
below it and shows estimated sensing power across conductance and injected current. The white
crosshairs mark the moving-average operating point and show its numeric
estimate. White dashed integer contours mark equal resolution from 8 bits
upward and equal sensing power in µW without numeric labels. Both color scales
start at zero. These maps use the transfer curve
and Allan-deviation data from
`hw/vendor/analog-library/VCO/VCO_characteristics/data/`, following the
equations and axes of `sensitivity.py`. Power includes both VCO channels and
the iDAC. Resolution combines P and N noise; the P variability CSV remains the lower-variance model and N the higher-variance
model. P is now the supply/reference channel and N the signal channel. Resolution
uses each channel's integration rate; with an independent P rate the heatmap
uses that fixed P rate along its N-rate axis.
The resolution map extends to 10 kHz to follow the slider; estimates outside the
measured timing range extend the model assumptions.

The header uses `docs/img/cheep_logo.png` and `docs/img/HEEPidermis_QR.png`.

The GUI estimates the stored sample/event rate from the selected trailing window
(default 100), excluding pauses and acquisition gaps, and estimates the recording
capacity of the reserved 16 KiB bank. Fixed-rate samples use two bytes; dLC event
bytes use one byte (including time-only and crossing-continuation packets).
Heartbeat display points use no sample-bank space. The bar below the middle-column
heatmaps can show **Acquisition type** (fixed rate red, dLC green) or **Average
sampling rate** (the heatmap palette on a fixed logarithmic 0.1–10,000 Hz scale).
Changing the rate window recalculates colors from all saved event timestamps.
At capacity the bar stays full and red until **RESET**; host recording continues.
This is an estimate of payload storage, without metadata/timestamp overhead. Linker placement keeps
code, constants, globals, heap, and stack in sram0 while leaving the 16 KiB
data region free for a future sample buffer.
The GSR JTAG build uses size optimization, a 1.5 KiB stack, and a 256-byte heap.
The CPU context area is word aligned to avoid a page-sized gap before program data.


## Tonic/phasic analysis and peaks

The third row on the right shows tonic and phasic conductance with independent
vertical scales (tonic left, phasic right), a shared time cursor, and blue phasic
peak markers. The algorithm dropdown changes analysis on the host immediately;
no firmware rebuild is needed. The choices are floating-point Python counterparts
of [Blanca's implementations](https://github.com/Blanca-c-m/x-heep/tree/main/sw/applications/tfg_blanca):

- `desprueba.c`: banded elimination, corresponding to v1.
- `descompv3.c` / `descompv5.c`: cached banded factorization.
- `descompinversa.c`: multiplication by a precomputed inverse.

These are different solvers for the **same smoothness-prior model**:
`(I + λ² D₂ᵀD₂) tonic = conductance`, `phasic = conductance − tonic`.
The default λ is 1, as in the source application; larger λ produces a smoother
tonic baseline. Python uses accurate floating-point solves rather than reproducing
the C versions' shift-based division approximations. Matrices are bounded blocks
with overlapping margins; the inverse option uses smaller blocks than the other
solvers, so block-edge estimates may differ slightly.

Analysis uses the plotted conductance, resampled at up to 10 Hz. The moving
average is bypassed when dLC is active.
Raw data is linearly interpolated. dLC reconstruction can use zero-order hold
(the default), linear interpolation or a natural cubic spline; the selected
reconstructed signal is the input to tonic/phasic decomposition. Pauses,
acquisition gaps, current/rate changes and invalid conductance values delimit
independent analysis intervals. Solves run in a worker so they do not block UART
reading. Live right-edge estimates, including peaks, are provisional and can
change as more data arrives.

Peak detection uses [SciPy `find_peaks`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.find_peaks.html)
with positive height/prominence (default 0.01 µS, adjustable), at least 0.5 s
width and 1 s separation. Prominence and temporal constraints follow the approach
used by [NeuroKit's EDA peak routines](https://neuropsychology.github.io/NeuroKit/_modules/neurokit2/eda/eda_findpeaks.html);
this GUI uses its own SciPy settings rather than claiming an exact NeuroKit port.

The timeline above the scrollbar shows green acquisition intervals, red missing
or invalid periods, gray pauses, and blue detected peaks. Click/drag it to browse the recording.
Missing data is declared after `max(3 s, 2.5 / selected rate)` without a sample;
time-only dLC packets and heartbeats prevent a quiet signal being marked missing.

When movement screening is enabled, a sample is excluded if P (the reference)
and N move together with comparable amplitude, either as a common step or as a
correlated change over the selected motion window. Exclusions are shaded dark
red on every signal plot and red on the timeline. The same full-height red
shading is used for lead-off, missing acquisition, invalid hardware samples and
invalid conductance. These periods split filtering/decomposition segments and
are recorded separately from hardware acquisition validity in CSV. Detected
phasic peaks are drawn as one-second-wide blue background bands through all
three plots, without a separate point marker. The
motion threshold, window and minimum correlation are adjustable under
**PROCESSING**.
Defaults are a 2 s window, at least 1 mV of reference movement, correlation
of 0.8, and a signal/reference movement ratio between 0.5 and 2. Common steps
hold the exclusion for one window. This is a tunable movement heuristic;
review flagged periods against the raw P/N traces. Turning screening off or
changing its settings recomputes exclusions for the full in-memory recording.
CSV `valid` reflects the resulting analysis validity; `acquisition_valid`
preserves the original hardware validity, `motion_artifact` records the motion
decision, `current_settling` identifies the one-second exclusion after an iDAC
step, and `voltage_offset_mV` records the calibration used. Session CSV
rows retain the screening/calibration active when appended; Save CSV recomputes
and exports the full session with the current settings.

CSV export recomputes the chosen analysis over the full session and includes
`tonic_uS`, `phasic_uS`, `phasic_peak_detected` (0/1), and
`filtered_conductance_uS` (tonic + phasic), together with algorithm, source,
sampling rate, stored bytes, raw dLC byte and segment. The continuously appended
session file keeps raw/reconstructed samples and event bytes; derived columns are
computed when exporting. Undefined conductance/decomposition values are blank.

## Differential acquisition modes

The left pane groups settings into **CONTROL**, **PARAMETERS**, **DLC MODE**,
**SERIAL COMMUNICATION**, **VISUALIZATION**, and **PROCESSING**. MCU frequency
and Config. board are on the left of CONTROL; Application, Build and its memory
summary are on the right. Memory-bar coloring is in VISUALIZATION.

Select **Difference mode** before Build:

| Mode | Difference calculation | P supply sampling | dLC |
| --- | --- | --- | --- |
| Hardware P−N | Signed `VCO_DECODER_CNT` register, including fine phase | Synchronized with N | DMA from the hardware difference register |
| Chip software P−N | Firmware subtracts the independently integrated P/N readings | Selectable; follows N by default | DMA from a volatile SRAM0 difference word |
| GUI P−N | GUI subtracts the reported P/N measurements | Selectable; follows N by default | Disabled |

**P supply sampling follows N** is on by default. Uncheck it in software/GUI
mode to choose P's integration rate with its slider. Releasing the slider writes
`gsr_supply_rate_millihz` over JTAG; zero means follow N. Firmware rereads this
volatile word, reconfigures acquisition and acknowledges the change. Both raw
coarse registers and fine thermometer phases are reported in every mode.

This hardware has one shared refresh clock. In software/GUI mode it runs at the
faster requested rate (at least 1 Hz). Each channel integrates its phase counts
until its own sampling deadline. Unequal rates use alternating integration lengths
on that shared grid to preserve the requested average rate; the latest P estimate
is held between P updates. Startup waits for the first P integration. Hardware
mode uses the synchronized shared interval; below 1 Hz it decimates 1 Hz reads.
Individual frequencies are normalized by their actual integration lengths.

If either channel falls below the VCO's calibrated minimum frequency (24 kHz),
firmware treats it as lead-off: it suppresses raw samples, dLC packets and
heartbeats. An `[i] Lead-off:` message still explains the missing contact, and
configuration acknowledgments remain available. dLC drains pending DMA without
transmitting its output and establishes a fresh baseline when contact returns.
The GUI also ignores lead-off samples from older firmware. Signal plots stay at
their last received time, while the status timeline continues marking missing
data. Valid samples resume at their actual timestamps, preserving the gap and
starting a new trace/processing segment.

The upper plot always shows the two raw voltage estimates. In hardware/software
mode the selected difference is converted to a signal-channel N estimate using
the raw P supply reference and the nonlinear nominal VCO curve; GUI mode uses
the two raw voltages directly. The GSR plot includes faint raw ground-truth
conductance points. CSV contains both the selected and raw differences and
conductances, both frequencies/counters, mode, P sampling rate and event type.

The hypothetical SRAM1 payload is **one signed 16-bit difference per signal
sample** in all three fixed-rate modes; raw P/N diagnostics do not add to it.
CSV also exposes `difference_word_int16` and its power-of-two phase-count scaling
`difference_word_shift`. dLC still counts **one byte per event**; raw reference
and heartbeat rows cost zero sample-bank bytes. No history is actually stored
in SRAM1 yet. Timestamp/metadata overhead is excluded from this estimate.

## Optional dLC acquisition

Build hardware or chip software mode to make dLC available; GUI mode unchecks
and disables it. **Use dLC**, level width and time bits (1–6 of eight) can then
be changed while recording. The GUI atomically writes all three volatile words,
firmware drains any pending DMA transaction, reconfigures acquisition and emits
fresh metadata. Startup values still come from Build. The guarded 34-byte DMA
buffer occupies the beginning of SRAM1 in hardware/software mode; the GUI capacity
bar uses the remaining 16,350 bytes for its acquisition-history estimate.

Hardware mode passes the **hardware P−N decoder register** directly through DMA
and the hardware dLC. Software mode writes its difference into an aligned,
volatile SRAM0 word; memory-to-dLC DMA reads that word and the resulting bytes are transmitted.
Normal memory pacing avoids losing a short VCO trigger during SRAM0 arbitration.
The board's DMA read FSM can prefetch an identical extra word, so software-mode
events from one transaction use the actual chip timestamp, rather than treating
the FIFO's delta-time input-read count as uniform ADC sample time. The word and its raw P/N reference are held until the finite DMA transaction
completes. An acquisition error stops in `wfi` and reports a reset reminder; it
does not repeatedly reenter startup.
Neither mode performs dLC encoding or reconstruction in software on the chip.

Python reconstructs the encoded frequency difference (62 phase counts per VCO
cycle), then recovers N from the raw P supply reference. Both raw channels are
still transmitted and retained as ground truth. Enable **Compare raw conductance
dots with dLC output** to overlay both only on the tissue-conductance plot. An invalid raw channel or
reconstructed frequency excludes that row from decomposition and peaks.

The **Reconstruction interpolation** option beside the dLC width/time controls
switches between zero-order hold, linear, quadratic and natural cubic spline reconstruction.
Only stored amplitude crossings anchor the trajectory. Time-overflow packets
advance the decoder clock and locate later crossings but do not become amplitude
knots. In chip-software dLC mode, the UART cycle remains the event timestamp
because DMA input-read ticks do not represent uniform ADC samples. Reference
and heartbeat frames are not knots either. The selected
trajectory is also the signal passed to decomposition, including while exploring
a loaded recording.

The conductance background uses thin quantizer level lines instead of a regular
horizontal grid. Levels are converted from counter
differences through the VCO transfer curve and conductance equation using the
reference voltage at that time. They are therefore nonuniform and can curve as
the reference changes. Each sample stores
its dLC level index, signed level change, width, discarded-bit count and reference
side, so a runtime width change affects the grid only from its firmware-confirmed
change onward and remains visible when reviewing or reloading the recording. Small
arrows on the conductance x-axis show crossing direction and scale with the number
of levels crossed. Because conductance is inverse to P−N voltage, the arrow
direction is opposite to the dLC level sign. Amplitude-overflow packets sharing
one timestamp are stacked, while their final cumulative level is the sole
interpolation knot at that time. Redundant raw/heartbeat reference points are
not drawn twice.
Each amplitude-overflow packet and its final crossing have separate, larger
arrows stacked at their shared timestamp; time overflow remains a hollow circle.
Older recordings can recover these fields from their saved raw UART sidecar.

The GSR trace uses steps in its usual color. Crossings and amplitude overflows
appear as upward or downward arrows on the time axis; zero-time amplitude
continuations stack at the same timestamp. A hollow **○** on the axis marks
time overflow only. Phasic peak
markers and timeline bars are light blue. Moving averaging is bypassed throughout
dLC plotting and analysis.

Input scaling discards enough low bits to fit the signed 16-bit hardware input.
Level width is expressed after this scaling; metadata supplies the scale.
Hysteresis is disabled because this RTL updates its level on suppressed reversals.
The **Event buffer** selector allocates 1–1024 dLC packets (16 by default) plus one guard word
in SRAM. It is a build-time setting: rebuild the MCU application after changing
it. The FPGA dLC
must wait until its input and output FIFOs are empty before asserting done, and
the DMA write side must drain the entire output burst rather than stopping at
the one-input-word transfer count. Updating only the MCU application cannot
repair an FPGA image with the old DMA/dLC handshake; rebuild and load the FPGA
bitstream as well. The updated DMA uses its output window to stop writing at the
guard word, drains any additional packets safely, and signals that the sample
was lost. Firmware emits `X`, reseeds the dLC, and the GUI shades the affected
interval yellow on the signal plots and timeline; it excludes that sample from
decomposition. In chip-software mode, a `D` frame carries all events from
one input sample and uses the same chip cycle as its corresponding `R` frame.
All power-of-two level widths from 1 to 32768 counts and all 1–6 time-bit allocations are selectable
independently; neither the GUI nor firmware rejects a combination based on a
theoretical maximum event count. A burst beyond the chosen capacity is not
lossless: it is flagged, not silently reconstructed. This remains a single-shot
DMA path, not a streaming ring. An FPGA bitstream predating the output cap is
not protected against memory overwrite, regardless of the selected size.

UART protocol (all numeric fields are integers; event payload is hexadecimal):

```text
A,mode,MCU_Hz,signal_rate_mHz,supply_rate_mHz,current_nA,idac_code,cycle_count,dlc_enabled
R,sample_number,cycle_count,current_nA,idac_code,P_Hz,N_Hz,P_coarse,N_coarse,P_fine,N_fine,difference_phase_counts,difference_rate_Hz,valid
B,MCU_Hz,signal_rate_mHz,log2_width,time_bits,discard_bits,initial_signed_level,P_Hz,current_nA,idac_code,cycle_count,mode,supply_rate_mHz,buffer_events
D,packet_sequence,cycle_count,current_nA,idac_code,P_Hz,N_Hz,event_bytes_in_hex
H,cycle_count,P_Hz,N_Hz,current_nA,idac_code
X,cycle_count,buffer_events
```

Modes are 0 (hardware), 1 (chip software), 2 (GUI). GUI-mode `R` puts zero in
`difference_phase_counts`; the GUI computes the difference itself. `A` is sent
at startup and rate changes; `B` additionally seeds the dLC decoder. `D` carries
real hardware bytes, including time-only overflow and large-crossing continuation.
`X` reports that more than `buffer_events` packets were produced for one input;
the excess was discarded and the dLC was reseeded.
`H` is a held-value/liveness heartbeat during quiet periods. Cycle counters handle
32-bit rollover; packet gaps invalidate reconstruction and request pause/start.
The GUI remains compatible with the previous B/D/H and legacy sample formats.
