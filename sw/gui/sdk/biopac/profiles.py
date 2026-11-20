"""Manual EDA hardware presets shared by the GUI and the standard-library CLI.

These are configuration declarations, never MP160/receiver readback. BioNomadix
values describe the user's specified setup and require physical verification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HardwareProfile:
    name: str
    channel: int  # One-based MP160 analog input number, not a receiver A/B label.
    sensitivity: float  # Nominal µS/V; does not affect raw ADC-to-voltage conversion.
    filters: tuple
    notes: str
    rate: int = 100
    zero_voltage: float = 0.0
    sensitivities: tuple = ()  # Physical nominal gain choices, in µS/V.


DEFAULT_PROFILE = "bionomadix-ppged-r"
PROFILES = {
    "eda100c": HardwareProfile(
        name="EDA100C + AMI100D", channel=3, sensitivity=2.0,
        filters=("10 Hz", "DC", "DC"),
        sensitivities=(2.0, 5.0, 10.0, 20.0),
        notes="MP160 + AMI100D + EDA100C. Match the physical channel, gain and three filter switches; "
              "absolute conductance still requires calibration with known loads.",
    ),
    "bionomadix-ppged-r": HardwareProfile(
        name="BioNomadix PPGED-R", channel=9, sensitivity=5.0,
        # Preserve the three existing metadata slots without inventing stages:
        # first = overall EDA bandwidth; second and third = not applicable.
        filters=("DC to 3 Hz", "N/A", "N/A"),
        sensitivities=(5.0,),  # Factory gain: +10 V for 50 µS (BioNomadix manual).
        notes="BN-TX / PPGED-4.3 EDA input → PPGED-R output B, bank X → MP160 channel 9. "
              "Nominal 0–50 µS = 0–10 V; factory EDA bandwidth DC to 3 Hz. "
              "Verify receiver pairing and routing physically. These supplied defaults are not hardware-validated; "
              "pairing, gain and filters are not read or controlled by this software.",
    ),
}


def get_profile(key):
    if not isinstance(key, str) or key not in PROFILES:
        raise ValueError(f"Unknown EDA hardware profile: {key!r}")
    return PROFILES[key]
