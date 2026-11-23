# HEEPidermis workspace GUI

A single desktop workspace for acquiring, plotting and comparing signals from
multiple physiological devices. Launch it from the repository root with:

```sh
make gui
```

The window is organized for wide screens:

* **Left** — a scrollable configuration column with collapsible menus: device
  selection, the **SIGNALS** table, shared **DECOMPOSITION · ALL DEVICES**
  settings, and one hardware/signal-conditioning menu per enabled device.
* **Right** — stacks of time plots sharing one time axis and cursor. The
  signal table decides which device signal goes to which stack row and y-axis,
  and with which style, amplification, color, transparency (α), and z-order.
  User annotations draw full-height vertical markers on every row.
* **Bottom** — per-device report tabs on the left (output, messages, status),
  the **ANNOTATIONS** menu in the middle, and shared navigation on the right
  (time zoom, live, save, load). The sash between the plots and the bottom bar
  is draggable, so the reports and annotation menu can be given more vertical
  space when needed. A workspace **Messages** tab collects anything that could
  not be done (skipped recordings, unreadable rows, display errors).

## Devices and SDKs

Each device's connection, interrogation and configuration layer lives in its
own SDK package; the workspace only renders the panels and plots the signals:

| Device | SDK folder | Status |
| --- | --- | --- |
| HEEPidermis (X-HEEP VCO GSR board) | `sw/gui/sdk/heepidermis` | Full acquisition + dLC + analysis |
| BIOPAC MP160 (EDA100C / BioNomadix) | `sw/gui/sdk/biopac` | Full native Linux UDP acquisition |
| Test events (Stroop, MIST, SSST) | `sw/gui/sdk/tests` | Passive local listener + test launcher |
| Shimmer | `sw/gui/sdk/shimmer` | Placeholder (not implemented yet) |
| Empatica (E4) | `sw/gui/sdk/empatica` | Placeholder (not implemented yet) |

Every device panel exposes the same interface to the workspace:
`signals()` (signal catalogue for the table), `get_series()` (times, values
and processing segments), `plot_snapshot()` (Tk-free data for background
computation), `recording_snapshot()` (native samples and calibration for a
workspace export), `invalid_spans()` (shaded gaps) and `close()`.

### Tests device (Stroop, MIST, SSST)

The tests in `sw/gui/tests/` publish their phases and outcomes as colored
events on the shared measurement timeline:

```sh
make stroop         # Stroop test; events feed the workspace GUI (TEST_ARGS overridable)
```

Enable the **Tests** device in the workspace, pick a test from the dropdown
and press its **Open** button: it launches the selected test (like
`make stroop`) and listens to the feed
(`sw/gui/outs/tests/events.jsonl`, an append-only JSONL file; override with
`HEEPIDERMIS_TESTS_EVENTS` or the panel's event-path field). The device state
is observed, not assumed: **Open** always listens and opens a test window
unless one is already running; when the window finishes or is closed the
device reports **Closed** again; **Close** ends the test and stops listening.
If the test window was opened outside the GUI (e.g. `make stroop`), Open just
listens to it.

The configuration menu below the dropdown is generated from the selected
test's own `spec.py` (sliders, dropdowns, checkboxes and free entries; see
`sw/gui/tests/AGENTS.md`) and covers **every** parameter of the test. The
**Configuration** row uses file explorers, like the workspace's own "Load
configuration…": **Load…** opens a file explorer (starting in
`sw/gui/tests/<Test>/configs/`) and applies any `.hjson` on disk to the
form, **Save as…** writes the current form values to a `.hjson` file you
pick. When the
workspace is saved (a CSV or a configuration), the selected test, the loaded
configuration file and all form values are stored and restored on load.
Only values that differ from the test's own defaults are passed as
command-line flags, and **Launch options** accepts extra raw flags.
Stroop and [MIST](tests/MIST/README.md) are implemented; MIST adds arithmetic
practice, rest, control and adaptive stress blocks, with keyboard/voice
answers and the same colored markers and stress sounds.
Placeholder folders (SSST — not implemented yet) still appear in the
dropdown, but selecting one shows "No such test was found." and Open does
nothing.

Every event is drawn as a **vertical line across every plot**, with its label
beside the line (`instructions`, `calibration`, `rest`, `test start`,
`correct`, `wrong`, `timeout`, `test end`, …). Each event carries its own
color from the test: verdict lines are **green** for `correct`, **red** for
`wrong` and `timeout`, and every informational phase stays white (see
`sw/gui/tests/AGENTS.md` for the event standard). Correct/wrong answers also
feed the signal catalogue (`1` for correct, `0` for wrong) and can be added
to the SIGNALS table like any signal. Per-stimulus lines are optional
(**Also show each stimulus onset**). Dense zoom-outs aggregate nearby markers
and note the hidden label count; zoom in to read them.

The event lines interleave with the signals by the **Z** (z-order) column of
the Tests rows: lowering the Tests row's z-order draws the lines *behind*
the signal traces, raising it draws them in front. Hiding every Tests row
(**Hide** button) removes the event lines too, while keeping the events
configured and saved.

Event timestamps come from the task's own clock at the moment each phase
happened (spoken answers use the recognition time, not the later feedback).
The workspace converts them to the common host-monotonic timeline, so
Tests events line up with HEEPidermis/BIOPAC samples even though the
devices and the task started at different moments. The per-device offsets are
stored in the workspace JSON sidecar and restored on load.

### Time alignment

All enabled devices map their samples onto one host-monotonic workspace
timeline. HEEPidermis anchors at its first recorded sample; BIOPAC reports its
acquisition origin (`timebase`); the Tests feed carries host-monotonic and
Unix timestamps for every event. The resulting per-device offsets are saved
with each workspace recording and applied again after **Load CSV…**. This is
software alignment on one computer's clock — not hardware synchronization —
so HEEPidermis timing keeps its usual UART reception uncertainty.

### The SIGNALS table

Add a row with **+ Add signal row**. The **Hide**, color and **X** (remove)
controls sit directly after the device/signal names so they are always visible;
the remaining columns scroll horizontally inside the table when the window is
narrow. **Hide** removes a signal from the plots without deleting it — its
settings stay in the table, its data keeps recording and saving, and the
button flips to **Show** to bring it back (a hidden row is restored hidden
after **Load CSV…**). The table starts with a permanent **annotations** row
(device `annotations`, signal `markers`): it cannot be removed (**X** is
disabled) and has no color cell — it exists so the **Hide**/**Show** toggle
can silence or restore every annotation marker at once. Per row you choose the
device, one of its
signals, the stack row (each stack row is one plot; all share the time axis),
the y-axis side (Left/Right), the y-axis **Range** — `Auto` fits the visible
data, `Fixed` pins it to the Min/Max fields (NRMSE defaults to 0–100,
correlation to −1–1, device signals to Auto) — the line style
(line/markers/steps), an amplification (0.01x–100x, `normalize`, or
`normalize row/axis`), color, α and z-order (see the Tests section for how
z-order also layers event lines against the signals). `normalize row/axis`
applies one common scale to every signal in the same stack and y-axis,
overriding their individual amplification settings. When several fixed rows
share one axis, the axis spans the union of their ranges. Row colors are
picked from the same discrete color matrix as the annotations (no free-form
RGB chooser).

**+ Add comparison (A vs B)** adds a derived signal computed on the host
between two recorded signals — NRMSE, correlation, difference or ratio. Because
the devices are not hardware-synchronized, comparisons run on a common 20 Hz
host-time grid over a 2 s sliding window.

### Shared decomposition and configuration

**DECOMPOSITION · ALL DEVICES** selects the tonic/phasic algorithm, smoothing,
and peak prominence for every device's tonic/phasic signals. Feature-extraction
rows keep their own **conf.** parameters. Device sections contain acquisition,
calibration, reconstruction, and screening controls; file actions and
decomposition controls belong to the workspace.

**Save configuration…** and **Load configuration…** use one JSON file for the
entire workspace: device selection, device settings (including remembered
settings for disabled devices), all signal/comparison/feature rows, plot ranges
and styles, time span, and shared decomposition settings. Loading requires idle
devices and does not start hardware acquisition.

### Data and settings

* Workspace configuration: `sw/gui/configs/workspace/last.json` (auto-saved).
* Device settings: `sw/gui/configs/heepidermis/last.json`,
  `sw/gui/configs/biopac/last.json`; named HEEPidermis configurations in
  `sw/gui/configs/heepidermis/*.json`.
* Recordings: `sw/gui/outs/` (HEEPidermis sessions), `sw/gui/outs/biopac/`,
  and `sw/gui/outs/tests/events.jsonl` (append-only test event feed).

### Responsive display and continuous recording

Window extraction, features, comparisons and display reduction run in a background
job. Only one job is in flight; obsolete results from zoom/settings changes are
discarded. The plot status shows **Loading visualization…** while computation is
pending. Tk draws a bounded, extrema-preserving preview (at most 2,400 points per
trace); recording uses the original samples. Display errors are reported and retried.

Both acquisition workers write and flush CSV independently of GUI updates:

* HEEPidermis: `sw/gui/outs/last_output.csv`.
* BIOPAC: `sw/gui/outs/biopac/last_output.csv` (or its configured output folder).

**Pause → Start** appends to the same recording. **Stop → Start**, Reset/New session,
or the first recording in a new application session replaces `last_output.csv`.

**Save CSV…** asks for one filename (defaulting to `sw/gui/outs/`, the shared
recordings folder, which is created if missing) and saves **all enabled devices
and all
signal rows together**, over the full history available to the workspace at the
snapshot. Export runs in the background while acquisition continues writing its
original autosaves. The named snapshot is complete when the workspace status
says it was saved; later samples belong to subsequent snapshots.

Keep the following two files together:

* `<name>.csv`: native device samples, every device signal, configured
  signal/comparison/feature rows, and user annotations, with their original
  timestamps. No common-grid
  resampling is imposed on independent device signals. Test events are native
  rows of the `tests` device (`event_label`, `event_color`, `event_monotonic_s`, …);
  signal rows additionally carry `workspace_label` when a row has per-sample
  labels.
* `<name>.csv.workspace.json`: all workspace/device settings, native column
  schemas, sample counts, per-device clock offsets
  (`time_alignment.offsets_s`), and calibration/verification metadata needed
  to reload.

The CSV's `workspace_kind` is `sample` for native data, `signal` for calculated
series, and `annotation` for user annotations. `workspace_device` identifies the
source. Signal rows use
`workspace_signal`, `time_s`, `workspace_value`, `workspace_unit`, and
`workspace_segment`; `workspace_row` is the 1-based signal-table row, or blank
for a device's signal catalogue. Values retain physical units; visual
amplification and normalization are stored as plot settings. Native rows retain
their SDK column names. Transport-level UART logs and BIOPAC full-rate binary
captures remain alongside the original device autosaves.

**Load CSV…** restores the complete saved workspace in one operation, including
the devices, signal table, annotations, and shared decomposition settings. All native
recordings are validated before panels are changed. Stop HEEPidermis and
disconnect BIOPAC before loading. Missing sidecars or incomplete recordings are
reported rather than loading only part of the session. Choose a new filename
when saving; existing workspace recordings are not overwritten.

For compatibility, Load CSV also imports older single-device CSVs, detecting
HEEPidermis by `vco_p_uV` and BIOPAC by `adc_count`. Their native sidecars are
restored when available. The next workspace save includes all enabled devices.

### Annotations

The **ANNOTATIONS** menu (bottom bar) manages user markers on the shared time
axis. Click a plot to place the inspect cursor: a vertical dashed line. Click
within ±1% of the visible time window around an existing marker to select it.
Timestamp-only markers span every plot row; markers tied to a device signal
(choose it in the menu's **Signal** box) appear only on the rows showing that
signal.

The menu is laid out compactly: **+ Add / Edit / Delete** share the first row,
**Time**, color and **Signal** sit below it, the **label** field below that,
and the **description** field (hint shown inside the empty field) uses the
remaining height. The annotation list (time + label) runs down the right side
across the full menu height.

* The marker's **label** (drawn left-aligned just to the right of its line,
  in the marker color), **color**, **signal** association and **description**
  (kept in the menu; useful for notes) are set in the menu fields. Colors are
  picked from a discrete matrix (a luminosity ramp plus hue families) that
  opens above the color button.
* **+ Add** inserts a marker at the shown timestamp with the current fields;
  it appears on the plots immediately.
* **Edit** (only with a selected marker) applies field changes live; **Enter**
  accepts them, **Esc** cancels and restores, and clicking anywhere in the
  plots re-times the marker being edited. The timestamp is editable here too.
* **Delete** removes the selected marker but keeps its fields, so **+ Add**
  re-creates it (e.g. after nudging the timestamp).
* The list shows every annotation with its time and label; selecting one moves
  the inspect cursor to it, brings it into view and fills the fields.

Annotations also have a permanent **annotations** row in the SIGNALS table. It
has no trace color (each marker uses the color chosen in the menu) and its
**X** button is disabled — it cannot be deleted, only **hidden** with the
Hide/Show toggle, which removes every marker from the plots until shown again.

Annotations are exported as `workspace_kind="annotation"` CSV rows and restored
by **Load CSV…**. Reading is tolerant: unknown record kinds, unreadable
annotations, and missing columns are skipped and reported in the workspace
**Messages** tab instead of failing the load, so recordings from other GUI
versions always load whatever they can.

### Running a device panel standalone

```sh
make biopac        # BIOPAC panel with its own plots (BIOPAC_ARGS overridable)
make stroop        # Stroop test window; publishes events for the workspace
make gui           # full multi-device workspace
```

## Dependencies

Tk from Python's standard library, `pyserial` for the HEEPidermis UART, and
NumPy/pandas/SciPy for the plots and analysis; `hjson` for the test
settings files. On Ubuntu, install `python3-tk`, then the GUI dependencies:

```sh
python3 -m pip install -r sw/gui/requirements.txt
```

Voice answers additionally need Vosk + sounddevice (see
`sw/gui/tests/Stroop/README.md`); the keyboard response mode needs nothing
beyond Tk.

Use the same Python environment when launching `make gui`.

## HEEPidermis device panel

The default application is `gsr/demo` (`sw/applications/gsr/demo/main.c`).
It is also the Makefile's default `PROJECT` for application builds.
Select an application and press **Build** to invoke `make jtag_build`. The green
**▶ RECORD** button opens OpenOCD and the serial port, then runs GDB inside the
app. GDB loads and starts the target, then disconnects so JTAG can update the
current and sampling rate during recording. The button becomes red **▮▮ PAUSE RECORDING** while recording; after pausing, it offers **Continue recording** if history remains, or **Start recording** after Reset.
Pause closes the serial port and invokes `make jtag_close`. Closing the window also
cleans up connections started by the GUI. **RESET** clears the plotted and
in-memory history while paused; the next Start replaces `last_output.csv`.
Use Save CSV first to retain a recording under a chosen name.

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

The bottom row contains device **Output** and **Messages** reports plus shared
**Navigation** and CSV actions. Firmware information starts with `[i]` on UART and appears in cyan with a timestamp;
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
appended by the serial reader to `sw/gui/outs/last_output.csv` before delivery to
the GUI. The workspace's **Save CSV…** includes these samples in the combined
recording described above. Exported conductance is
`current_µA × 1,000,000 / (P_µV − N_µV − offset_µV)` in µS; it is blank
when the corrected drop is zero or negative.
Each sample retains the current active when it was recorded. New firmware also
reports the hardware iDAC code in every acquisition frame; the GUI checks that
`code × 40 nA` matches the reported current and rejects mismatches. Both the session
and exported CSV include that current, so a change between recordings does
not alter earlier conductance values.
Each native HEEPidermis autosave is accompanied by `<recording>.csv.configuration.json` and
`<recording>.csv.uart.log`. The first sidecar preserves the GUI configuration;
the second preserves every raw line received from the chip. Both are updated
with a live session and automatically restored when importing a native recording.
Workspace exports use the combined `.workspace.json` sidecar instead.
Legacy CSVs without sidecars still load.

The workspace's **Load CSV…** opens a saved workspace or a native session CSV
without connecting to hardware. The loaded data can be browsed,
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
`sw/gui/configs/heepidermis/last.json` after it changes. Startup restores that file.
The workspace's **Save configuration…** writes one JSON configuration for all
devices and signals; **Load configuration…** validates and applies it. Acquisition settings take effect on
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
**ARTIFACT REJECTION** in the HEEPidermis section.
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

Workspace CSV export computes tonic/phasic signals and the configured feature
and comparison rows over the full saved history. These are signal records in
the combined CSV; the native sample records retain their acquisition metadata
and dLC bytes. Undefined derived values are represented as NaN.

## Differential acquisition modes

The HEEPidermis section groups settings into **CONTROL**, **PARAMETERS**, **DLC MODE**,
**SERIAL COMMUNICATION**, **VISUALIZATION**, and **ARTIFACT REJECTION**. MCU frequency
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

See [HEEPidermis packet format](PROTOCOL.md) for transmission modes, packet
field tables, dLC byte encoding, and signal reconstruction.

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
