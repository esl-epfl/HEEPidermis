# HEEPidermis packet format

This document describes the packets sent by HEEPidermis firmware to the GUI:
the transmission modes, packet fields, and rules for reconstructing the signal.
P and N are the two VCO channels; the measured voltage drop is P minus N.

## 1. Transmission modes

### Raw samples or dLC events

| Transmission | What is sent | How the GUI obtains the signal |
|---|---|---|
| **Raw** (`dlc_enabled = 0`) | An `A` configuration packet, followed by `R` sample packets. | Decode each sample independently using its frequencies and, in modes 0/1, its differential count. |
| **Digital level crossing (dLC)** (`dlc_enabled = 1`) | `A` configuration, `B` decoder initialization, then `D` event packets. `R` samples remain available as reference/diagnostic readings; `H` reports liveness and `X` reports buffer loss. | Start from the level in `B` and accumulate the signed level changes in `D`. This requires an uninterrupted event sequence. |

With dLC, a sample can produce no events, one event, or several events. One
`D` packet contains a burst of event bytes; it is not necessarily one crossing.

### Where P−N is calculated

The `mode` field selects where the difference is formed. This is separate from
the raw/dLC transmission choice above.

| `mode` | GUI name | How the difference is formed | Supported transmission |
|---|---|---|---|
| `0` | Hardware P−N | Read the hardware differential-counter register. P and N use the same sampling rate. | Raw or dLC |
| `1` | Chip software P−N | Firmware computes the difference from P/N frequencies and expresses it in phase counts. P may use an independent integration rate. | Raw or dLC |
| `2` | GUI P−N | Transmit both frequencies; the GUI converts them to voltages and subtracts them. | Raw only |

In modes 0 and 1, enabling dLC sends the selected differential count through
the hardware dLC encoder. In mode 2, the differential-count field in `R` is
zero and is ignored by the GUI.

## 2. How a packet is formed

Each UART packet is **one ASCII line**:

```text
TYPE,field_1,field_2,...,field_n\n
```

- `TYPE` is one letter: `A`, `R`, `B`, `D`, `H`, or `X`.
- Fields are comma-separated decimal integers, including a leading minus sign
  for signed values. The final field of `D` is the exception: hexadecimal bytes.
- `\n` terminates the packet. There is no length prefix or packet checksum.
- Numbers are text, so there is no multi-byte integer endianness to decode.
- In a `D` payload, each pair of hex characters represents one event byte,
  in transmission order: `2501` means bytes `0x25`, then `0x01`.
- Firmware messages beginning with `[i]` are informational text, not sample packets.

The tables below describe the **current firmware format**. All listed fields
are present. Field positions start at 1, immediately after the type letter.

### Packet overview

| Type | Purpose | Fields after the type, in wire order |
|---|---|---|
| `A` | Acquisition configuration | `mode, MCU_Hz, signal_rate_mHz, supply_rate_mHz, current_nA, idac_code, cycle_count, dlc_enabled` |
| `R` | Raw/reference sample | `sample_number, cycle_count, current_nA, idac_code, P_Hz, N_Hz, P_coarse, N_coarse, P_fine, N_fine, difference_phase_counts, difference_rate_Hz, valid` |
| `B` | Initialize/reseed dLC decoding | `MCU_Hz, signal_rate_mHz, log2_width, time_bits, discard_bits, initial_signed_level, P_Hz, current_nA, idac_code, cycle_count, mode, supply_rate_mHz, buffer_events` |
| `D` | dLC event burst | `packet_sequence, cycle_count, current_nA, idac_code, P_Hz, N_Hz, event_bytes_in_hex` |
| `H` | dLC heartbeat/reference update | `cycle_count, P_Hz, N_Hz, current_nA, idac_code` |
| `X` | dLC buffer-loss notification | `cycle_count, buffer_events` |

## 3. Packet fields

### A — Acquisition configuration

Sent when acquisition is configured, including startup and configuration changes.
The GUI uses it to establish the mode, rates, current, and cycle-counter timebase.

| Position | Entry | Meaning / units |
|---|---|---|
| 1 | `mode` | `0` hardware, `1` chip software, `2` GUI P−N. |
| 2 | `MCU_Hz` | MCU clock frequency, in Hz. Used to convert cycle counts to seconds. |
| 3 | `signal_rate_mHz` | Signal/N sampling rate, in millihertz; divide by 1000 for Hz. |
| 4 | `supply_rate_mHz` | Effective P integration rate, in millihertz. |
| 5 | `current_nA` | Injected current, in nA. |
| 6 | `idac_code` | Hardware current-register readback; expected to equal `current_nA / 40`. |
| 7 | `cycle_count` | Unsigned 32-bit MCU cycle timestamp. |
| 8 | `dlc_enabled` | `0` raw transmission; `1` dLC enabled. |

Example: mode 1, 10 MHz MCU, 10 Hz N and P sampling, 120 nA current, dLC enabled:

```text
A,1,10000000,10000,10000,120,3,1000000,1
```

### R — Raw/reference sample

Carries both channel readings and the differential count. With dLC enabled,
this packet updates the reference readings; it does not reset the dLC level.

| Position | Entry | Meaning / units |
|---|---|---|
| 1 | `sample_number` | Sample index, incremented for each `R`. |
| 2 | `cycle_count` | MCU cycle timestamp for this sample. |
| 3 | `current_nA` | Injected current, in nA. |
| 4 | `idac_code` | Hardware current-register readback. |
| 5 | `P_Hz` | P frequency calculated by firmware, in Hz. |
| 6 | `N_Hz` | N frequency calculated by firmware, in Hz. |
| 7 | `P_coarse` | Raw P coarse-counter register reading. |
| 8 | `N_coarse` | Raw N coarse-counter register reading. |
| 9 | `P_fine` | Raw P fine-phase register bit pattern, printed as a decimal integer. |
| 10 | `N_fine` | Raw N fine-phase register bit pattern, printed as a decimal integer. |
| 11 | `difference_phase_counts` | Signed P−N phase count in modes 0/1; zero placeholder in mode 2. |
| 12 | `difference_rate_Hz` | Rate used to scale the differential count into Hz; at least 1 Hz, including sub-Hz transmission. |
| 13 | `valid` | `1` if both reported frequencies are within the firmware's valid VCO band; otherwise `0`. |

The coarse/fine fields preserve the register readings for inspection. They are
not voltages; in particular, the fine register is not a decimal fractional part.
The GUI uses the already-decoded `P_Hz` and `N_Hz` fields.

### B — dLC initialization

Establishes the absolute starting level and the event-byte layout. A new `B`
resets the decoder's accumulated ticks and expected `D` sequence number to zero.

| Position | Entry | Meaning / units |
|---|---|---|
| 1 | `MCU_Hz` | MCU clock frequency, in Hz. |
| 2 | `signal_rate_mHz` | Signal sampling rate, in millihertz. |
| 3 | `log2_width` | Level width exponent, 0–15: one level is `2^log2_width` scaled counts. |
| 4 | `time_bits` | Number of event-byte bits allocated to elapsed ticks, 1–6. |
| 5 | `discard_bits` | Number of low input bits discarded before encoding, 0–15. |
| 6 | `initial_signed_level` | Signed absolute level from which event changes accumulate. |
| 7 | `P_Hz` | Initial P reference frequency. Subsequent `R`, `D`, and `H` packets update it. Hardware-mode initialization can report zero before a reading is available. |
| 8 | `current_nA` | Injected current, in nA. |
| 9 | `idac_code` | Hardware current-register readback. |
| 10 | `cycle_count` | MCU timestamp anchoring this decoder initialization. |
| 11 | `mode` | `0` hardware or `1` chip software; `2` is not allowed. |
| 12 | `supply_rate_mHz` | Effective P integration rate, in millihertz. |
| 13 | `buffer_events` | Maximum event bytes buffered for one input, 1–1024. |

### D — dLC event burst

| Position | Entry | Meaning / units |
|---|---|---|
| 1 | `packet_sequence` | Starts at zero after `B`; increments once per `D` packet, not per event byte. |
| 2 | `cycle_count` | MCU timestamp. Chip-software mode uses the corresponding `R` sample's timestamp; hardware mode timestamps the completed burst at output. |
| 3 | `current_nA` | Injected current, in nA. |
| 4 | `idac_code` | Hardware current-register readback. |
| 5 | `P_Hz` | P reference frequency, in Hz. |
| 6 | `N_Hz` | Measured N frequency, in Hz, retained for comparison with the reconstructed value. |
| 7 | `event_bytes_in_hex` | Consecutive two-character hex bytes, without `0x` prefixes or separators. One to `buffer_events` bytes. |

The event-byte layout is defined in section 4. Missing or out-of-order sequence
numbers invalidate reconstruction: later deltas cannot recover the missing level.

### H — Heartbeat

Updates the reference frequencies and reports liveness without changing the
accumulated dLC level. Firmware emits these approximately once per second while
dLC is active and usable fresh readings are available.

| Position | Entry | Meaning / units |
|---|---|---|
| 1 | `cycle_count` | MCU cycle timestamp. |
| 2 | `P_Hz` | Latest P reference frequency, in Hz. |
| 3 | `N_Hz` | Latest measured N frequency, in Hz. |
| 4 | `current_nA` | Injected current, in nA. |
| 5 | `idac_code` | Hardware current-register readback. |

### X — Event-buffer loss

Sent when one input produces more events than the configured buffer can hold.
The GUI marks the affected reference sample as lost and discards its decoder;
firmware reconfigures and sends a new initialization before decoding resumes.

| Position | Entry | Meaning / units |
|---|---|---|
| 1 | `cycle_count` | MCU timestamp associated with the overflow. |
| 2 | `buffer_events` | Buffer capacity that was exceeded, **not** the number of lost events. |

## 4. How a dLC event byte is formed

Let `T = time_bits` from `B`. Each byte contains:

```text
Most significant bit                         Least significant bit
+-------------------+----------+----------------------------------+
| elapsed ticks: T  | sign: 1  | magnitude: 7 − T bits             |
+-------------------+----------+----------------------------------+
```

| Entry | Extraction from byte `b` | Interpretation |
|---|---|---|
| `dt` | `b >> (8 − T)` | Ticks added to the accumulated event clock. |
| `sign` | `(b >> (7 − T)) & 1` | `0` increases the level; `1` decreases it. |
| `magnitude` | `b & (2^(7 − T) − 1)` | Number of levels changed; this is sign-magnitude, not two's complement. |

For every byte, in order:

```text
ticks = ticks + dt
level = level + (magnitude if sign == 0 else −magnitude)
```

| Event | Encoding | Effect |
|---|---|---|
| Level crossing | Nonzero magnitude | Change the level by the signed magnitude. |
| Time overflow | Zero magnitude | Advance ticks without changing the level. Not an amplitude interpolation point. |
| Amplitude continuation | Nonzero magnitude with `dt = 0` | Continue a large change that did not fit in one byte, without advancing ticks. |

**Example with `time_bits = 4`:** the layout is `[dt:4][sign:1][magnitude:3]`.

| Hex byte | Bits | Decoded effect |
|---|---|---|
| `25` | `0010 0 101` | Advance 2 ticks, increase 5 levels. |
| `1b` | `0001 1 011` | Advance 1 tick, decrease 3 levels. |
| `f0` | `1111 0 000` | Advance 15 ticks, no level change (time overflow). |
| `07` | `0000 0 111` | Increase 7 levels at the same tick (amplitude continuation). |

Thus a `D` payload of `2501` increases the level by 5, then by 1, while
advancing only 2 ticks in total.

## 5. Reconstructing and interpreting the signal

### Raw transmission

Let `V(f)` be the GUI's inverse nominal VCO transfer curve: frequency in Hz
to voltage in µV.

| Mode | Reconstruction from an `R` packet |
|---|---|
| `0` or `1` | `difference_Hz = difference_phase_counts × difference_rate_Hz / 62`; `N_reconstructed = P_Hz − difference_Hz`; `delta_uV = V(P_Hz) − V(N_reconstructed)`. |
| `2` | `delta_uV = V(P_Hz) − V(N_Hz)`. No dLC decoding is involved. |

The factor 62 is the number of VCO phase counts per oscillator cycle.

### dLC transmission (modes 0 and 1)

Initialize `level` from `B`, then apply every event byte in each `D`:

```text
decoder_rate_Hz = max(1, signal_rate_mHz / 1000)
difference_Hz = level × 2^(log2_width + discard_bits) × decoder_rate_Hz / 62
P_reconstructed = P_Hz
N_reconstructed = P_Hz − difference_Hz
delta_uV = V(P_reconstructed) − V(N_reconstructed)
```

Current packets use **P as the reference**; there is no reference-selector bit
in `B`. The reported `N_Hz` remains a separate measured reading, rather than
replacing the reconstructed N value.

For display and analysis, amplitude changes provide the interpolation points:
ZOH holds each value, Linear joins points, Quadratic fits degree-two curves,
and Spline uses a natural cubic spline. Equal-time points keep the final
cumulative value. Time-overflow bytes do not add amplitude points.

### Time, conductance, and validity

- **Time:** `elapsed_s += ((cycle_count − previous_cycle) mod 2^32) / MCU_Hz`.
  `A`/`B` anchor the cycle timebase to host elapsed time. The current GUI stores
  decoded events at their enclosing `D` packet's timestamp; the event decoder
  also accumulates the `dt` ticks described above.
- **Conductance:** `G_uS = (current_nA / 1000) × 10^6 / (delta_uV − offset_uV)`.
  The optional offset is a measured voltage correction. A nonpositive current
  or corrected voltage drop produces no conductance value.
- **Validity:** respect `R.valid`, check `idac_code = current_nA / 40`, and
  require usable measured/reconstructed frequencies within 24,000–1,051,710 Hz.
  A buffer-loss notification or broken dLC sequence prevents continuous
  reconstruction until the decoder is initialized again.

## Compatibility and implementation references

Older recordings may omit `idac_code` or the trailing `B` configuration fields.
Early `B` formats use N as the reference instead of P. The GUI also accepts
the legacy four-number line `sample_number,P_Hz,N_Hz,current_nA`. These are
compatibility formats; the tables above specify what current firmware emits.

- Firmware packet generation: [`acquisition_demo.h`](../applications/gsr/demo/acquisition_demo.h) and [`dlc_demo.h`](../applications/gsr/demo/dlc_demo.h).
- GUI packet parsing: [`sdk/heepidermis/panel.py`](sdk/heepidermis/panel.py), `_handle_acquisition_line` and `_handle_dlc_line`.
- Event-byte decoding: [`sdk/heepidermis/acquisition.py`](sdk/heepidermis/acquisition.py), `DLCDecoder`.
