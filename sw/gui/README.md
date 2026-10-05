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
the slider writes its value to firmware over JTAG. Build writes the selected
startup frequency and current to the source, runs `make jtag_build`, and
shows the `Cont` and `IntL` bank use lines from X-HEEP's memory report directly
below the Build button. Build and serial output are available in the terminal
panel at the bottom. The legacy `test_VCO_counter` application remains
selectable; its sampling frequency is also applied at Build. Very high rates
can exceed the UART's capacity to print every sample; the terminal reports
skipped measurements when that occurs.

The demo uses the GSR controller to configure the iDAC and VCO, then reads
a synchronized P/N pair through the VCO SDK. It discards startup, stale,
missed, and out of range frames instead of plotting them. The SDK is given the
configured board clock and real hardware timing by the demo.

While recording `gsr/demo`, move either runtime slider and release it. The GUI
writes its firmware volatile word through OpenOCD/JTAG, then reads it back. The
firmware picks up the new value; it reconfigures the VCO refresh rate for a
sampling change. It applies current through the GSR controller, checks the iDAC
register, and prints a confirmation. The firmware confirms sampling changes
over UART as well. The GUI shows current as applied only after that confirmation
or a measurement line reporting the new current. An unconfirmed change shows
an error six seconds after the JTAG write. The firmware also prints
`I=<nA> nA` on each measurement line, so the GUI records the programmed current
used for each sample and computes conductance from it. The demo still accepts
`I=<nA>` commands on UART for use outside the GUI.

The monitor reads the first two `uV` values from each firmware output line as
VCO P and VCO N, then calculates P − N in the GUI. The full terminal output
appears across the bottom. The upper right plot shows P and N on a fixed
300–850 mV scale. The lower right plot shows tissue conductance in µS using
the current reported with each sample. Both plots show raw samples as faint points and filtered
values as lines. The default moving average window is 10 samples. The
conductance line is calculated from the moving average of P − N. Set the
averaging window to 1 to see the unfiltered signal. The x axes show seconds
since the first sample. Drag the horizontal scrollbar to
review earlier samples, and press **Live** to return to the newest data.

Every sample, including its injected current and computed conductance, is
appended immediately to a session CSV under `sw/gui/outs/`.
**Save CSV…** exports the full session to a chosen path. The suggested filename
is the time of the Save action in `yyyy_mm_dd_hhmm.csv` format. Exported conductance is
`current_µA × 1,000,000 / abs(P_µV − N_µV)` in µS; it is blank when P equals N.
Each sample retains the current active when it was recorded. Both the session
and exported CSV include that current, so a change between recordings does
not alter earlier conductance values.

The middle panel shows the VCO transfer curve from the repository CSV and the
latest P and N operating points using the voltages and frequencies printed by
the firmware. Solid markers show averaged measured points; small rings show the
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
