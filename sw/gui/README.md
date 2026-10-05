# VCO GSR monitor

Launch the desktop interface from the repository root with:

```sh
make gui
```

It uses Tk from Python's standard library and `pyserial` for the UART. On Ubuntu,
install the system packages with `sudo apt install python3-tk python3-serial`, or
install `pyserial` into the Python environment used to launch the GUI. The
repository Python requirements also include `pyserial`.

Select an application and press **Build** to invoke `make jtag_build`. The green
**▶ RECORD** button opens OpenOCD and the serial port, then runs GDB inside the
app. It becomes a red **▮▮ PAUSE** button while recording. Pause stops GDB,
closes the serial port, and invokes `make jtag_close`. Closing the window also
cleans up connections started by the GUI. **RESET** clears the plotted and
in-memory history and starts a new session file with the next sample; previous
session files are kept.

Set **MCU frequency** in Hz and press **Config. board** to run
`make board_freq PLL_FREQ=...`. The GUI sets the UART baud rate to one twentieth
of that frequency, updates `SYS_FCLK_HZ` in the VCO application, and reconnects
the serial port if it is open. Rebuild the VCO application to apply the new
timer setting. The serial port and baud rate are also editable.

Set **VCO sampling frequency** in whole Hz before pressing **Build**. Build
updates `VCO_FS_HZ` in `test_VCO_counter/main.c`, runs `make jtag_build`, and
shows the `Cont` and `IntL` bank use lines from X-HEEP's memory report directly
below the Build button. Build and serial output are available in the terminal
panel at the bottom. The VCO registers refresh four times per sample period,
and the firmware only skips a sample if it has no new counter update.

The monitor reads the first two `uV` values from each firmware output line as
VCO P and VCO N, then calculates P − N in the GUI. The left status panel
retains at most three lines. The upper right plot shows P and N on a fixed
300–850 mV scale. The lower right plot shows tissue conductance in µS, using
1 µA by default. Both plots show raw samples as faint points and filtered
values as lines. The conductance line is calculated from the moving average
of P − N. Set the averaging window to 1 to see the unfiltered signal. The x
axes show seconds since the first sample. Drag the horizontal scrollbar to
review earlier samples, and press **Live** to return to the newest data.

Every sample is appended immediately to a session CSV under `sw/gui/outs/`.
**Save CSV…** exports the full session to a chosen path. The suggested filename
is the time of the Save action in `yyyy_mm_dd_hhmm.csv` format. Enter the
injected current magnitude in µA before saving. Exported conductance is
`current_µA × 1,000,000 / abs(P_µV − N_µV)` in µS; it is blank when P equals N.
The current entered at Save applies to the whole exported recording.

The middle panel shows the VCO transfer curve from the repository CSV and the
latest P and N operating points using the voltages and frequencies printed by
the firmware. Solid markers show averaged measured points; small rings show the
corresponding locations on the nominal curve. The markers use the same moving
average window as the signal lines and update when that window changes.
