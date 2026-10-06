# VCO GSR monitor

Launch the desktop interface from the repository root with:

```sh
make gui
```

It uses Tk from Python's standard library and `pyserial` for the UART. The
power and resolution plots also use NumPy, pandas, and SciPy. On Ubuntu,
install `python3-tk` and the repository's Python requirements, or use the
equivalent system Python packages.

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
port and baud rate are also editable.

Set **Sampling frequency** with the slider below **Record**. It offers
0.1–0.9 Hz, 1–9 Hz, 10–90 Hz, and the same 1–9 steps through 10,000 Hz. It
starts at the rate in the demo source. Moving the slider while recording writes
the selected rate to the firmware over JTAG; the firmware rereads the volatile
setting and reconfigures the VCO controller. Build still sets the startup rate.
For rates below 1 Hz, `gsr/demo` refreshes the VCO at 1 Hz and emits samples
at the chosen average rate. `test_VCO_counter` requires at least 1 Hz.
The current slider also sits below **Record**. It selects iDAC codes 1–255,
corresponding exactly to 40–10200 nA in 40 nA steps. During recording, releasing
the slider writes its value to firmware over JTAG and saves it as the startup
current for the next GUI/build. The default startup current is 520 nA; if the
existing firmware image has a different startup value, the GUI applies the saved
current after GDB starts the target. Build writes the selected startup frequency
and current to the source, runs `make jtag_build`, and
shows the `Cont` and `IntL` bank use lines from X-HEEP's memory report directly
below the Build button. Build output is available in the terminal panel at the
bottom. The legacy `test_VCO_counter` application remains
selectable; its sampling frequency is also applied at Build. Very high rates
can exceed the UART's capacity to print every sample.

The demo uses the GSR controller to configure the iDAC and VCO, then reads
a synchronized P/N pair through the VCO SDK. It discards startup, stale,
missed, and out of range frames instead of plotting them. The SDK is given the
configured board clock and real hardware timing by the demo.

The **Messages** panel stays visible at the bottom of the left column. Firmware
information starts with `[i]` on UART and appears in cyan with a timestamp;
GUI guidance appears in amber. It includes rebuild/reset reminders, pending
settings changes, acknowledgments, and acquisition feedback. The bottom terminal
keeps the full compiler and debug output. Consecutive identical messages are
collapsed in the Messages panel.

After three seconds without a valid pair, the demo reports missing signal,
out-of-range P or N, an unresolved P−N difference, or missed refresh updates.
Warnings repeat at most every three seconds, and signal recovery is reported.
For the front-end model `P = reference − current / conductance`, a low P suggests
decreasing current, while a high P suggests increasing it. Invalid N suggests
checking its reference/supply. Missing signal requires checking the resistor or
electrodes; lower sampling rates can help with sparse counts. Unresolved P−N
suggests increasing current or lowering the sampling rate. These are manual
suggestions; the firmware does not automatically change your settings.

While recording `gsr/demo`, move either runtime slider and release it. The GUI
writes its firmware volatile word through OpenOCD/JTAG, then reads it back. The
firmware picks up the new value; it reconfigures the VCO refresh rate for a
sampling change. It applies current through the GSR controller, checks the iDAC
register. UART sample records contain only four comma-separated integers per line:
sample number, P frequency in Hz, N frequency in Hz, and injected current in nA.
The GUI inverts the nominal VCO transfer curve to estimate voltages for plotting.
Informative lines are separate from samples. `[i] Current set: <nA> nA` and
`[i] Sampling set: <mHz> mHz` confirm settings even when no valid signal is present.
The GUI waits for these firmware acknowledgments after JTAG writes. The demo still accepts
`I=<nA>` commands on UART for use outside the GUI.

The upper right plot shows P and N in mV, auto-scaled
to the visible samples with about 5% headroom so the data uses roughly 90% of
the plot height. The lower right plot shows tissue conductance in µS using
the current reported with each sample. Both plots show raw samples as faint points and filtered
values as lines. The default moving average window is 10 samples. The
conductance line is calculated from the moving average of P − N. Set the
averaging window to 1 to see the unfiltered signal. The x axes show seconds
since the first sample. Use the **Time zoom −/+** buttons to adjust the visible
time range, drag the horizontal scrollbar to review earlier samples, and press
**Live** to return to the newest data.

Click or drag over either right-hand plot to place a shared vertical time cursor.
The middle column then shows the transfer operating point and estimates averaged
through that selected sample. **Live** moves the cursor back to the newest sample
and resumes following incoming data.

Every sample, including its injected current and computed conductance, is
appended immediately to a session CSV under `sw/gui/outs/`.
**Save CSV…** exports the full session to a chosen path. The suggested filename
is the time of the Save action in `yyyy_mm_dd_hhmm.csv` format. Exported conductance is
`current_µA × 1,000,000 / abs(P_µV − N_µV)` in µS; it is blank when P equals N.
Each sample retains the current active when it was recorded. Both the session
and exported CSV include that current, so a change between recordings does
not alter earlier conductance values.

The middle panel shows the VCO transfer curve from the repository CSV and the
latest P and N operating points mapped from the firmware frequencies. Solid
markers show averaged measured points; small rings show the
corresponding locations on the nominal curve. The markers use the same moving
average window as the signal lines and update when that window changes.
The wider middle column shares its available height evenly among the transfer,
power, and resolution plots, keeping all three visible. The interface uses a
dark theme.

Below the transfer curve, the power map shows estimated sensing power across
conductance and injected current. The resolution map shows estimated bits
across conductance and sample rate at the current injection setting. The white
crosshairs mark the moving-average operating point and show its numeric
estimate. Each map has a labeled color scale. These maps use the transfer curve
and Allan-deviation data from
`hw/vendor/analog-library/VCO/VCO_characteristics/data/`, following the
equations and axes of `sensitivity.py`. Power includes both VCO channels and
the iDAC. Resolution combines P and N noise; the P variability CSV is used
for the lower-variance sensed channel and the N CSV for the noisier reference.
The resolution map extends to 10 kHz to follow the slider; estimates above the
measured timing range extrapolate the model.

The header uses `docs/img/cheep_logo.png` and `docs/img/HEEPidermis_QR.png`.

The GUI estimates a sample rate from the selected trailing sample window
(default 100) and shows how long the reserved 16 KiB bank would hold 8,192
16-bit delta samples at that rate. The capacity bar sits below the middle-column
heatmaps and fills in their palette; at capacity it turns red and stays full until
**RESET**. Linker placement keeps
code, constants, globals, heap, and stack in sram0 while leaving the 16 KiB
data region free for a future sample buffer.
The GSR JTAG build uses size optimization, a 1.5 KiB stack, and a 256-byte heap.
The CPU context area is word aligned to avoid a page-sized gap before program data.
