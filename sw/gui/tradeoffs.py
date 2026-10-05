"""Power and resolution estimates based on the VCO sensitivity model."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


MODEL_DIR = Path(__file__).resolve().parents[2] / "hw" / "vendor" / "analog-library" / "VCO" / "VCO_characteristics"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

from vco_model import VCOADCModel  # noqa: E402


class TradeoffModel:
    """Use sensitivity.py's operating-point axes and signal/noise equation.

    The demo runs both VCOs, so power includes P and N. Resolution combines
    their measured, channel-specific Allan deviations in quadrature.
    """

    def __init__(self) -> None:
        self.vco = VCOADCModel(data_folder=str(MODEL_DIR / "data"))
        self.g_values_us = np.geomspace(1.0, 100.0, 25)
        self.current_values_ua = np.linspace(0.04, 10.2, 19)
        self.rate_values_hz = np.geomspace(0.1, 10_000.0, 29)
        self.n_reference_v = 0.8
        self.n_reference_power_uw = float(self._vco_power_uw(self.n_reference_v))

    def _vco_power_uw(self, voltage_v):
        voltage = np.clip(np.asarray(voltage_v), 0.2, 0.85)
        return self.vco.interp_pvco(voltage) + self.vco.interp_pcnt(voltage)

    def power_uw(self, g_us, current_ua, p_mv=None, n_mv=None):
        g = np.asarray(g_us, dtype=float)
        current = np.asarray(current_ua, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            p_v = 0.8 - current / g if p_mv is None else np.asarray(p_mv) / 1000
        n_v = self.n_reference_v if n_mv is None else np.asarray(n_mv) / 1000
        result = self._vco_power_uw(p_v) + self._vco_power_uw(n_v) + 0.8 * current + 0.4
        return np.where((g > 0) & (current > 0), result, np.nan)

    def _adev(self, channel: str, voltage_v, rate_hz):
        voltage, rate = np.broadcast_arrays(np.asarray(voltage_v, dtype=float), np.asarray(rate_hz, dtype=float))
        points = np.stack((voltage.ravel(), np.clip(1.0 / rate.ravel(), 0.1, 5.0)), axis=-1)
        interpolator = self.vco.interp_adev_p if channel == "P" else self.vco.interp_adev_n
        return interpolator(points).reshape(voltage.shape)

    def resolution_bits(self, g_us, rate_hz, current_ua, p_mv=None, n_mv=None):
        g, rate = np.broadcast_arrays(np.asarray(g_us, dtype=float), np.asarray(rate_hz, dtype=float))
        current = np.asarray(current_ua, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            high_v = np.clip(0.8 - current / (g + 0.5), 0.2, 0.85)
            low_v = np.clip(0.8 - current / (g - 0.5), 0.2, 0.85)
        signal_v = np.abs(high_v - low_v)
        p_v = (high_v + low_v) / 2 if p_mv is None else np.asarray(p_mv, dtype=float) / 1000
        n_v = self.n_reference_v if n_mv is None else np.asarray(n_mv, dtype=float) / 1000
        p_hz = self.vco.interp_freq(p_v)
        n_hz = self.vco.interp_freq(n_v)
        p_slope = np.abs(self.vco.kvco_func(p_v))
        n_slope = np.abs(self.vco.kvco_func(n_v))
        with np.errstate(divide="ignore", invalid="ignore"):
            noise_p = p_hz * self._adev("P", p_v, rate) / p_slope
            noise_n = n_hz * self._adev("N", n_v, rate) / n_slope
            noise_v = np.sqrt(noise_p ** 2 + noise_n ** 2)
            snr_db = 10 * np.log10(signal_v ** 2 / noise_v ** 2) + 10 * np.log10(rate)
            bits = (snr_db - 1.7) / 6
        valid = (g > 0.5) & (rate > 0) & (current > 0) & (p_v >= 0.33) & (p_v <= 0.82) & (n_v >= 0.33) & (n_v <= 0.82)
        return np.where(valid & np.isfinite(bits), bits, np.nan)

    def power_map(self):
        g, current = np.meshgrid(self.g_values_us, self.current_values_ua)
        return self.power_uw(g, current)

    def resolution_map(self, current_ua: float):
        g, rate = np.meshgrid(self.g_values_us, self.rate_values_hz)
        return self.resolution_bits(g, rate, current_ua)
