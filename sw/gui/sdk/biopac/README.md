# Native BIOPAC MP160 acquisition on Linux

This is an **experimental recorder and live desktop GUI** for the MP160, not an
official BIOPAC SDK. The native UDP input transport has been exercised on MP160
firmware **2.1.06** and **2.0.00** on Linux. The CLI requires only Python's standard
library; the GUI also uses Tkinter, NumPy and SciPy. Python 3.8+ is supported.
No Windows, Wine, BHAPI DLL, BIOPAC USB license key or proprietary software is
required by this implementation.

## Hardware profiles

| Profile ID | Hardware / EDA signal path | MP160 analog channel (one-based) | Nominal sensitivity | Physical filter declaration | MP160 sample rate |
| --- | --- | --- | --- | --- | --- |
| `eda100c` (CLI default) | MP160 + AMI100D + EDA100C | 3 | 2 µS/V (dropdown: 2/5/10/20) | 10 Hz / DC / DC | 100 Hz |
| `bionomadix-ppged-r` (`make biopac` default) | BN-TX / PPGED-4.3 **EDA input** → wireless link → PPGED-R receiver **output B, bank X** → MP160 | 9 | 5 µS/V, fixed (0–50 µS nominally mapped to 0–10 V) | DC to 3 Hz (factory-default EDA bandwidth) | 100 Hz |

The BioNomadix route, sensitivity and bandwidth above are **user-supplied setup
assumptions**, not automatic detection or measured calibration. Verify the
physical receiver settings, transmitter pairing and known-load readings before
interpreting EDA. The wireless hop is only between transmitter and receiver;
Linux still communicates with the MP160 over **Ethernet/UDP**, not wireless or
a proprietary USB interface.

Only one MP160 input channel is enabled. Selecting a profile loads software
defaults (including a **0 V calibrated zero**); channel, rate, sensitivity, zero
and filter declarations remain editable. Profiles do not pair the transmitter,
read hardware switches, or change receiver/amplifier gain or filters. Pause
before physically changing the setup. The program does not enable outputs or
update firmware; acquisition setup initializes the device's shared timers.

For backward-compatible filter storage, BioNomadix uses the existing three
metadata slots as **`["DC to 3 Hz", "N/A", "N/A"]`**. Slot 1 describes the overall
EDA bandwidth; slots 2/3 are unused, **not additional filter stages**. The GUI
labels them “Bandwidth / Unused / Unused”. This declaration does not apply a
3 Hz software filter.

## Ethernet connection

The MP160's USB connection is a USB-to-Ethernet adapter, not a serial port. It
lives on an IPv4 link-local network reachable through some wired adapter.

Discovery is transparent: with the interface field left blank, the recorder
searches every non-loopback interface that has an IPv4 address (subnet
broadcast, link-local broadcast and global broadcast, in parallel) and reports
which link each unit answered on; the link that answered is remembered in the
interface field for the next session. An explicit IP is probed unicast on all
interfaces. You can still restrict the search by naming one interface. Adapter
names can change across machines or USB ports, which the automatic scan
absorbs. On 2026-11-17, discovery found MP160 `1611A-0001A38` (firmware 2.0.00)
at **169.254.154.56** on **enxa0cec8057fcf**. The previous unit used
`169.254.164.181` on `enxa0cec853f4ca`. These are machine/device-specific
examples, not defaults.

The adapter carrying the device still needs a suitable IPv4 address. If no
matching NetworkManager profile exists, this creates a temporary dedicated
profile without gateway or DNS changes (substitute your adapter name):

```sh
nmcli connection add save no type ethernet \
  ifname enxa0cec8057fcf con-name opencode-biopac-probe \
  connection.autoconnect no ipv4.method manual \
  ipv4.addresses 169.254.111.111/16 ipv4.never-default yes \
  ipv4.ignore-auto-dns yes ipv6.method disabled
nmcli connection up uuid YOUR_PROFILE_UUID ifname enxa0cec8057fcf
```

Use the UUID printed by `nmcli connection add`. If a matching profile already
exists, activate its UUID instead of creating another; inspect profiles with
`nmcli -f NAME,UUID,DEVICE connection show` and
`nmcli -g connection.interface-name connection show uuid YOUR_PROFILE_UUID`.
Profile names need not be unique. On the machine above, the profile created for
the current adapter is `577f3a1a-b87c-45c8-8fc5-590bb2ba33ec`.
`save no` makes it temporary; after a reboot, recreate it if absent or configure
a dedicated persistent profile. To remove only the profile you created:

```sh
nmcli connection delete uuid YOUR_PROFILE_UUID
```

The recorder itself does not change network configuration. NetworkManager may
require administrator permission for network setup. UDP ports 16004 and 16005
must be available on this interface. Close other BIOPAC acquisition programs;
only one recorder should control the device. Do not interrupt an existing run.

If the GUI reports **No MP160 answered on any network interface**, check that
the wired adapter has an IPv4 address (see the NetworkManager setup above) and
that the MP160 is powered. If it reports units on the wrong link or several
units, name the intended interface explicitly. Leave the IP field blank for
discovery before entering a previously saved IP from a different unit.

Unprivileged users are supported: when `SO_BINDTODEVICE` is not permitted, the
probes and the command channel bind the interface's own address instead, which
scopes the traffic to that link just as well.

## Live GUI

Launch the desktop monitor from the repository root:

```sh
make biopac
```

The make target supplies `--profile bionomadix-ppged-r --interface enxa0cec8057fcf
--ip 169.254.154.56` by default, loading that preset on each launch. Set
`BIOPAC_ARGS` to replace these arguments for another setup; `BIOPAC_ARGS=""`
uses saved GUI settings instead.

You can also launch directly from the `sw/` directory with
`python3 -m gui.sdk.biopac.panel` (options: `--profile`, `--interface`, `--ip`,
`--output-dir`, `--config`; `BIOPAC_ARGS=--demo make biopac` shows an explicitly
labelled simulation and never accesses hardware). Requirements: Tkinter
(`python3-tk`), NumPy, and SciPy (`sw/gui/requirements.txt`).

The GUI streams live plots of amplifier voltage, nominal skin conductance, and
the shared smoothness-prior tonic/phasic decomposition, and autosaves every
acquisition to `sw/gui/outs/biopac/last_output.csv` plus `.raw`, `.json`, and
`.udp.jsonl` sidecars. A new recording replaces this file; pause/resume appends.
**Save CSV…** renames the live CSV and sidecars, without rebuilding the recording.
Capture continues into the renamed file if still running. Saved CSVs (including the original batch CLI recordings) can be
reloaded and exported; configuration persists to `sw/gui/configs/biopac/last.json`.

In section **02**, choose **EDA100C + AMI100D** or **BioNomadix PPGED-R** in
**Hardware profile (manual preset)**. Selection fills pending settings, including
zero = 0 V; it does not send commands. Review the displayed channel, sensitivity
and physical filters, then Start or Apply. A saved configuration retains its
profile **and manual overrides**. An explicit `--profile` on the GUI command line
loads that preset instead of the saved acquisition settings. Old configurations
without a profile retain their values and use the EDA100C identity.

**Sensitivity** is a read-only dropdown of the physical gains each profile
supports: 2/5/10/20 µS/V for the EDA100C, and the fixed factory 5 µS/V for the
BioNomadix PPGED-R. Conductance stays `(voltage − zero) × sensitivity`; raw ADC
counts and volts are unaffected.

**Output rate (Hz)** offers the same decade steps as HEEPidermis (0.1–1000 Hz).
The MP160 hardware in this driver acquires at 100/200/500/1000 Hz; the driver
always uses the lowest validated hardware rate that can supply the requested
output rate (10 Hz → 100 Hz hardware, keeping 1 of every 10 samples) and keeps
evenly spaced original samples for the CSV, plots and analysis. The raw sidecar
still contains every hardware sample, so nothing is lost. Dropping is phase-exact
across UDP block boundaries and segments, but it applies **no anti-alias
filter** — the EDA signal path's own bandwidth remains the physical limit. The
rate takes effect on slider release, also while recording (a new segment is
started, like *Apply settings*); other pending declarations still need Apply.

For the BioNomadix setup on the current machine:

```sh
cd /home/juan/Desktop/ESL/HEEPidermis/open_repo
make biopac
```

Confirm **channel 9, 100 Hz, 5 µS/V, zero 0 V, DC to 3 Hz / N/A / N/A**, verify
the receiver is paired and routed to output B / bank X, then **Connect** and
**Start recording**. The GUI's “Connected” means only that the **MP160** replied;
it does not confirm the wireless link. Pause before changing physical settings.

Behaviour notes:

- **Start/Pause**: recording runs in a worker thread; live samples are marked
  provisional until Pause/Disconnect stops the ADC and verifies every streamed
  byte against the device's frozen buffer.
- **Apply settings** during recording stops and verifies the current segment,
  then starts a new segment with the new settings and a visible gap; earlier
  samples keep their original profile and calibration. Segments are shaded if their
  verification failed, and ADC-rail (clipped) samples are marked invalid.
- Software processing (moving average, anti-alias resampling to 10 Hz, shared
  HEEPidermis EDA analysis) never alters recorded raw data. The live right-edge
  of the analysis is provisional and recomputed as data arrives; it is not a
  clinical or proprietary BIOPAC analysis.
- Session files close and flush on exit; closing waits for verification, so do
  not unplug the MP160 until the GUI reports it has stopped.

## Command-line recorder

From the repository root, first identify the MP160 without starting acquisition:

```sh
cd sw
python3 -m gui.sdk.biopac.acquire --interface enxa0cec8057fcf --probe
```

Record BioNomadix EDA using the discovered address (this example is the current
unit; the default channel, sensitivity and filters come from the profile):

```sh
cd sw
python3 -m gui.sdk.biopac.acquire --interface enxa0cec8057fcf --ip 169.254.154.56 \
  --profile bionomadix-ppged-r --rate 100 --seconds 10 \
  --output sw/gui/outs/biopac/bionomadix_eda_100hz.csv
```

For the original EDA100C wiring, select the original profile:

```sh
cd sw
python3 -m gui.sdk.biopac.acquire --interface enxa0cec8057fcf \
  --profile eda100c --seconds 10
```

Omitting `--profile` keeps the original defaults: channel 3, 100 Hz, 2 µS/V,
zero 0 V, 10 Hz / DC / DC. Existing `--channel 3 --rate 100 --sensitivity 2`
commands still work. Explicit `--channel`, `--rate`, `--sensitivity`,
`--zero-voltage` and `--filters` override the selected CLI profile regardless of
option order. For example, `--filters "DC to 3 Hz" "N/A" "N/A"` declares the
BioNomadix bandwidth; it does not program the receiver. Only use a nonzero
`--zero-voltage` or altered sensitivity from an actual calibration.

Use `--ip` to target a known device explicitly. Discovery ignores
the host's own broadcast query and refuses to choose between multiple units.
`--probe` is read-only. Acquisition is limited to the tested firmware; it never
attempts an automatic firmware upgrade.

Recordings go to `sw/gui/outs/biopac/` by default, or a new `--output /path/run.csv`.
Existing files are never overwritten. **Ctrl+C** during acquisition stops the ADC
and saves the shorter capture. Do not power-cycle or unplug a recording device.

Each capture produces:

- `.csv`: sample index, nominal device time, channel, signed ADC count, nominal
  voltage, nominal conductance, and exact ADC-rail flags. GUI sessions add the
  segment number, per-segment rate, manual amplifier declarations and an appended
  `hardware_profile` column per row. Existing columns keep their names/order.
- `.csv.raw`: original big-endian signed 16-bit ADC samples.
- `.csv.json`: device identity, firmware, manual amplifier settings, timing,
  calibration assumptions, integrity checks, and completion/error status.
- `.csv.udp.jsonl`: requests and responses for reproducing transport problems.

GUI segment metadata contains the applied `settings.profile`, channel, rate,
gain, zero and filters. `hardware_profile_defaults` separately archives the
nominal preset(s), not hardware readback or measured calibration. CLI CSV
columns remain unchanged; keep its JSON sidecar with it to preserve the profile,
rate and calibration when reloading. Old CLI and GUI recordings remain readable;
missing profile IDs default to EDA100C without replacing explicit recorded
calibration. Export and reload preserve mixed-profile segments. A verification
flag refers to buffer integrity, **not** physical pairing or calibration.

The batch CLI **records to device memory, stops, then downloads** a verified
frozen snapshot. Duration is host-controlled, so the sample count can differ by
a few samples from `seconds × rate`. A capacity check rejects recordings that
could fill the device buffer. Supported nominal rates are 100, 200, 500, and
1000 Hz; sample timestamps are derived from the configured timer, not packet
arrival times. Host timestamps are not hardware-synchronized timestamps for
comparison with another instrument. The GUI uses the same verified frozen
buffers but streams provisional live samples while recording, polling the
device at about 10 Hz and stopping before the buffer wraps.

## Units and validation

The nominal conversion follows the 16-bit ±10 V ADC convention:

```text
voltage_V = signed_ADC_count × 20 / 65535
conductance_µS = (voltage_V − calibrated_zero_voltage_V) × sensitivity_µS_per_V
```

The default calibrated zero is 0 V. Set `--zero-voltage` only from a real
calibration, not the current tissue baseline. Negative readings are preserved,
not rectified or clamped. Changing a profile/gain/zero affects conductance only;
raw ADC counts and nominal voltage are preserved independently. The batch CLI
applies no software filtering or tonic/phasic analysis. The GUI's existing
moving-average and anti-aliased 10 Hz analysis use each segment's conductance
calibration without crossing segment boundaries. The physical 10 Hz or 3 Hz
bandwidth is not the MP160 sample rate.

**Obtaining ADC samples does not by itself validate electrode contact or absolute
conductance.** With no participant connected, check the zero and then use an
insulated known resistor between electrode contacts. For example, 1 MΩ should
give 1 µS, approximately 0.5 V at 2 µS/V; 100 kΩ should give 10 µS, approximately
5 V. Follow BIOPAC's amplifier calibration and electrode-use instructions.
Near-rail readings require checking contact, amplifier sensitivity, and the
amplifier's measurement range. Do not treat this prototype as a clinical device.

### BioNomadix validation procedure (not yet performed with known loads)

1. Confirm the transmitter is powered and **paired with this PPGED-R receiver**
   using the hardware's indicators/instructions. Connect the leads to its **EDA**
   input, not the PPG input. Check receiver **EDA output B, bank X → analog 9**
   physically. The software cannot read pairing state, RF quality, bank switches,
   gain or filters; a flat signal alone does not establish pairing.
2. With no participant connected, select `bionomadix-ppged-r`, 100 Hz, moving
   average 1, zero 0 V. Record separate short runs for open leads and each known
   resistor across the EDA leads. Let readings settle; inspect voltage as well
   as conductance, and use a new filename for each run.

   | Test condition | Expected nominal conductance | Expected voltage at 5 µS/V |
   | --- | --- | --- |
   | Open electrodes | approximately 0 µS | approximately 0 V |
   | 1 MΩ resistor | approximately 1 µS | approximately 0.2 V |
   | 100 kΩ resistor | approximately 10 µS | approximately 2 V |

3. Record measured values and resistor tolerances. Unexpected offsets or a
   consistent scale error require checking pairing, routing, actual gain and
   hardware calibration. Do not force agreement by rectifying or subtracting a
   subject baseline. Use a measured electrical zero only when justified by the
   hardware's calibration procedure. No accuracy tolerance is asserted here.
4. Check the raw trace for flat-topped saturation and the CSV `adc_clipped` flags
   (counts −32768 or 32767). Near ±10 V is near the MP160 ADC rail; nominal 50 µS
   at 5 µS/V is at the positive rail. Receiver/transmitter saturation or RF loss
   may occur without an ADC-rail flag; their behavior is not detected or modeled.
5. Pause/stop and check segment verification. A byte-perfect MP160 recording can
   still contain the wrong channel, an unpaired receiver output or miscalibrated
   EDA. Confirm the resistor response occurs on analog 9 before interpreting skin
   measurements. Filter bandwidth, wireless latency, dropout behavior and
   absolute calibration require separate physical verification.

## Transport details and tests

The transport was investigated using the user's supplied AcqKnowledge installation
media and verified on the physical device. Discovery is `iDnt` on UDP 16005;
commands and replies use UDP 16004, framing byte 1, and an 8-bit zero-sum checksum.
Final buffer verification stops ADC acquisition with command `0x12`; live reads
remain provisional until then. The MP160's
extended byte counter (`0x91`) and extended buffer read (`0x93`) are used instead
of the legacy 16-bit counter (`0x11`). Replies do not carry command IDs, so requests
are strictly serialized and are not blindly retried after a timeout.

Channels in the CLI, GUI, settings, CSV and `MP160.capture`/`start_live` APIs are
**one-based (1–16)**. The existing start command `0x82` uses a big-endian 16-bit
analog bitmask `1 << (channel - 1)` followed by a zero digital-input mask. Thus
channel 3 selects bit 2 (`0x0004`), while channel **9** selects bit **8**
(`0x0100`), not channel 8 or bit 9. The channel-9 payload is
`03 00 01 00 00 00` and the complete checksummed frame is
`01 82 03 00 01 00 00 00 79`. Receiver output **B** is a physical routing label,
not an instruction to acquire MP160 analog channel 2. Automated tests exercise
channels 1, 3, 8, 9 and 16 in both batch and live paths.

Chunked downloads fit in an Ethernet MTU and verify header, length, checksum,
buffer wrap, a stable final byte counter, and repeated reads of the frozen buffer.
Failed captures retain diagnostic sidecars but are never labeled complete.

Earlier hardware validation on firmware 2.1.06 included a 10-second, 100 Hz capture
(1,002 samples), separate captures at 200, 500, and 1,000 Hz, and a live streamed
run (2,179 samples at 1,000 Hz) whose streamed bytes matched the final frozen
buffer exactly. Observed rates agreed with their configured values.
Multi-packet downloads, repeated frozen-memory reads, and Ctrl+C stop/save were
exercised on the device. This validates transport and timing consistency, not
absolute conductance calibration.

On **2026-11-17**, MP160 `1611A-0001A38`, firmware **2.0.00**, accepted the same
channel-9 start payload and produced a **201-sample / 2.004 s** frozen capture at
100 Hz. Live channel-9 captures contained **130, 260, 651 and 1,304 samples** at
100, 200, 500 and 1,000 Hz respectively; every streamed byte matched the frozen
buffer, and each run ended with ADC status **0x80 (stopped)**. This supports
adding 2.0.00 to the firmware allowlist without changing the UDP commands.
These measurements verify **MP160 transport/channel selection and timing** only:
the input/load and receiver pairing were not independently established. They
do **not** validate the BioNomadix resistor response, 5 µS/V scaling, wireless
behavior or DC–3 Hz bandwidth. Those checks remain pending.

The profile-aware CLI also saved a 201-sample, 100 Hz channel-9 capture, and the
GUI completed connect → record → pause/verify → export → close on Python 3.8.20
with 210 samples. Neither run established a known electrical input. The CLI
transport-check artifacts are in the ignored
`outs/bionomadix_ch9_transport_check_20261117.csv*` files on the development machine.

## Tests

No hardware is required:

```sh
python3 -m unittest discover -s sw/gui/sdk/biopac -p 'test_*.py'
python3 -m unittest discover -s sw/gui/sdk/heepidermis -p 'test_*.py'
```

Profile tests cover channel masks, CLI defaults/overrides, nominal conversion,
raw-data preservation, GUI selection, settings persistence, mixed-profile CSV
round trips, processing boundaries and legacy files/configurations. GUI tests
need a display and are skipped without one; none of these tests uses hardware.

References: [MP160 communication troubleshooting](https://www.biopac.com/knowledge-base/mp160-troubleshoot/)
and [EDA100C calibration](https://www.biopac.com/product/eda-electrodermal-activity-amplifier/).
