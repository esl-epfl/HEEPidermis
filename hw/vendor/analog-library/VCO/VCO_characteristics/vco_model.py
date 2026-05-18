# Copyright 2026 EPFL contributors
# SPDX-License-Identifier: Apache-2.0
#
# File: vco_model.py
# Author: Juan Sapriza & Ismail Essaidi
# Date: 08/04/2026
# Description: Implementation of the VCO model and analysis functions

from dataclasses import dataclass
import sys
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from scipy.interpolate import interp1d, RegularGridInterpolator
import os

# ---------------------------------------------------------------------------
# Data Models and Computation Classes
# ---------------------------------------------------------------------------
@dataclass
class VCOParams:
    vdd: float = 0.8

    vin_min_mV: float = 330.0
    vin_max_mV: float = 800.0
    vin_points: int = 300

    idc_min: float = 0.1
    idc_max: float = 10.0
    idc_points: int = 300

    sweep_points: int = 200

    allen_dev: bool = True

    @property
    def vin_range(self):
        return np.linspace(self.vin_min_mV, self.vin_max_mV, self.vin_points)

    @property
    def i_dc_range(self):
        return np.linspace(self.idc_min, self.idc_max, self.idc_points)

class VCOADCModel:
    def __init__(self, data_folder='data', params=None, representation="lut"):
        self.data_folder = sys.path[0] + '/' + data_folder
        self.params = params if params is not None else VCOParams()
        self.representation = representation.lower()
        
        self._load_transfer_data(file_path="VCO variability - transfer.csv")

    @staticmethod
    def polynomial_model(x, a, b, c):
        return a * x**2 + b * x + c
    
    def _load_transfer_data(self, file_path = "VCO variability - transfer.csv"):
        VCO_data_path = os.path.join(self.data_folder, file_path)
        df = self._clean_csv(os.path.basename(VCO_data_path))

        # BEGIN: For retrocompatibility 
        v_t = df.iloc[:, 0].values / 1000.0  # mV to V
        f_t = df.iloc[:, 1].values * 1000.0  # kHz to Hz

        self.v_range = (v_t.min(), v_t.max())
        self.interp_freq = interp1d(v_t, f_t, kind='linear', fill_value="extrapolate")
        self.interp_pvco = interp1d(v_t, df.iloc[:, 2].values, kind='linear', fill_value="extrapolate")
        self.interp_pcnt = interp1d(v_t, df.iloc[:, 3].values, kind='linear', fill_value="extrapolate")
        self.kvco_func = lambda v: (self.interp_freq(v + 1e-5) - self.interp_freq(v - 1e-5)) / 2e-5
        # END: For retrocompatibility

        # Load transfer curve data 
        cols = list(df.columns)
        self.vin_data = np.asarray(df[cols[0]], dtype=float)
        self.fosc_data = np.asarray(df[cols[1]], dtype=float)
        self.pvco_data = np.asarray(df[cols[2]], dtype=float)
        self.pcnt_data = np.asarray(df[cols[3]], dtype=float)

        self.piecewise_threshold = float(self.vin_data[np.where(self.fosc_data > 0)[0][0]])
        self.vin_active_mV = self.vin_data[self.vin_data >= self.piecewise_threshold]
        self.fosc_active_kHz = self.fosc_data[self.vin_data >= self.piecewise_threshold]

        # Variability (Allen Deviation)
        df_n = self._clean_csv(filename="VCO variability - summary N.csv")
        v_n, taus, grid = self._build_adev_grid(df_n)
        self.interp_adev = RegularGridInterpolator((v_n, taus), grid, bounds_error=False, fill_value=None)
        
        if self.representation == "lut":
            # Precompute the derivative LUT for dV/dF to speed up delta_G calculations
            if np.any(np.diff(self.fosc_active_kHz) <= 0):
                raise ValueError("LUT inversion requires fosc_active_kHz to be strictly increasing.")
            self.df_dv_lut_kHz_per_mV = self._build_lut_derivative(self.vin_active_mV , self.fosc_active_kHz)
        else:
            # Fit polynomial for the active region
            self.popt_poly, _ = curve_fit(self.polynomial_model, self.vin_active_mV, self.fosc_active_kHz)
    
    def _clean_csv(self, filename):
        path = os.path.join(self.data_folder, filename)
        df = pd.read_csv(path)
        for col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
        return df

    def _build_lut_derivative(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)

        dydx = np.empty_like(y, dtype=float)

        if len(x) < 2:
            raise ValueError("Need at least two LUT points to compute derivative.")

        # One-sided differences at boundaries
        dydx[0] = (y[1] - y[0]) / (x[1] - x[0])
        dydx[-1] = (y[-1] - y[-2]) / (x[-1] - x[-2])

        # Central differences inside
        dydx[1:-1] = (y[2:] - y[:-2]) / (x[2:] - x[:-2])

        return dydx
    
    def _build_adev_grid(self, df_n, fs_orig=10):
        voltages = np.array([float(c) for c in df_n.columns])
        taus = np.logspace(np.log10(0.1), np.log10(5.0), 20)
        grid = np.zeros((len(voltages), len(taus)))

        for i, col in enumerate(df_n.columns):
            data = df_n[col].dropna().values
            y = data / np.mean(data)
            for j, tau in enumerate(taus):
                m = int(round(tau * fs_orig))
                if 2 * m > len(y):
                    grid[i, j] = np.nan
                    continue
                weights = np.ones(m) / m
                y_bar = np.convolve(y, weights, 'valid')
                grid[i, j] = np.sqrt(0.5 * np.mean((y_bar[m:] - y_bar[:-m])**2))
        return voltages, taus, grid
    
    def _prepare_grid(self, vin_mV, i_dc_uA):
        vin_arr = np.atleast_1d(np.asarray(vin_mV, dtype=float))
        i_arr = np.atleast_1d(np.asarray(i_dc_uA, dtype=float))

        vin_2d = vin_arr[None, :]
        i_2d = i_arr[:, None]

        vin_scalar = np.isscalar(vin_mV)
        idc_scalar = np.isscalar(i_dc_uA)

        return vin_arr, i_arr, vin_2d, i_2d, vin_scalar, idc_scalar

    def _vin_idc_terms(self, vin_2d, i_2d):
        vin_V = vin_2d * 1e-3
        i_dc_A = i_2d * 1e-6
        denom = self.params.vdd - vin_V
        return vin_V, i_dc_A, denom

    def kvco_kHz_per_mV(self, vin_mV):
        vin_arr_mV = np.asarray(vin_mV, dtype=float)
        if self.representation == "poly":
            a, b, _ = self.popt_poly
            out = np.where(
                vin_arr_mV < self.piecewise_threshold,
                0.0,
                2 * a * vin_arr_mV + b
            )

        elif self.representation == "lut":
            out = np.interp(
                vin_arr_mV,
                self.vin_active_mV,
                self.df_dv_lut_kHz_per_mV,
                left=0.0,
                right=self.df_dv_lut_kHz_per_mV[-1]
            )
            out = np.where(vin_arr_mV < self.piecewise_threshold, 0.0, out)

        else:
            raise ValueError(f"Unknown representation: {self.representation}")

        return out.item() if np.isscalar(vin_mV) else out

    def _invalid_mask(self, vin_2d, denom, extra_mask=None):
        invalid = (
            (vin_2d < self.piecewise_threshold) |
            np.isnan(vin_2d) |
            (denom <= 0)
        )
        if extra_mask is not None:
            invalid = invalid | extra_mask
        return invalid

    def _restore_shape(self, out, vin_scalar, idc_scalar):
        if vin_scalar and idc_scalar:
            return out[0, 0].item()
        elif vin_scalar:
            return out[:, 0]
        elif idc_scalar:
            return out[0, :]
        return out
    
    def fosc_from_vin(self, vin_mV):
        """
        Piecewise polynomial model:
            if representation is "poly":
                f_osc = 0 if V_in < piecewise_threshold, else polynomial 
            else: LUT interpolation if representation is "lut".
        """
        vin_arr = np.asarray(vin_mV, dtype=float)

        if self.representation == "poly":
            a, b, c = self.popt_poly
            poly_val = np.maximum(a * vin_arr**2 + b * vin_arr + c, 0.0)
            out = np.where(vin_arr < self.piecewise_threshold, 0.0, poly_val)

        elif self.representation == "lut":
            out = np.interp(
                vin_arr,
                self.vin_active_mV,
                self.fosc_active_kHz,
                left=0.0,
                right=self.fosc_active_kHz[-1]
            )
            out = np.where(vin_arr < self.piecewise_threshold, 0.0, out)

        else:
            raise ValueError(f"Unknown representation: {self.representation}")

        return out.item() if np.isscalar(vin_mV) else out
    
    def vin_from_G(self, G_uS, i_dc_uA):
        G_arr = np.asarray(G_uS, dtype=float) * 1e-6
        i_arr = np.asarray(i_dc_uA, dtype=float) * 1e-6

        with np.errstate(divide='ignore', invalid='ignore'):
            vin_V = self.params.vdd - i_arr / G_arr

        vin_mV = vin_V * 1e3
        invalid = (G_arr <= 0) | np.isnan(G_arr) | np.isnan(i_arr)
        vin_mV = np.where(invalid, np.nan, vin_mV)

        return vin_mV.item() if np.ndim(vin_mV) == 0 else vin_mV

    def vdd_mV(self):
        return self.params.vdd * 1e3

    def conductance(self, vin_mV, i_dc_uA):
        _, _, vin_2d, i_2d, vin_scalar, idc_scalar = self._prepare_grid(vin_mV, i_dc_uA)
        _, i_dc_A, denom = self._vin_idc_terms(vin_2d, i_2d)

        with np.errstate(divide='ignore', invalid='ignore'):
            G_uS = (i_dc_A / denom) * 1e6

        invalid = self._invalid_mask(vin_2d, denom) | np.isnan(i_2d)
        G_uS = np.where(invalid, np.nan, G_uS)

        return self._restore_shape(G_uS, vin_scalar, idc_scalar)

    def df_osc_adev_Hz(self, vin_mV, fs_Hz):
        if self.interp_adev is None:
            fs_arr = np.asarray(fs_Hz, dtype=float)
            return np.zeros_like(fs_arr, dtype=float)

        vin_arr = np.asarray(vin_mV, dtype=float)
        fs_arr = np.asarray(fs_Hz, dtype=float)

        vin_V = vin_arr * 1e-3
        tau = np.clip(1.0 / fs_arr, 0.1, 5.0)

        vin_V, tau = np.broadcast_arrays(vin_V, tau)

        f_osc_Hz = np.asarray(self.fosc_from_vin(vin_arr), dtype=float) * 1e3
        f_osc_Hz, _ = np.broadcast_arrays(f_osc_Hz, tau)

        query_pts = np.stack([vin_V.ravel(), tau.ravel()], axis=-1)
        adev = self.interp_adev(query_pts).reshape(vin_V.shape)

        out = f_osc_Hz * adev
        return out.item() if out.ndim == 0 else out
    
    def df_osc_sampling_Hz(self, fs_Hz=None, avg_window=1):
        if fs_Hz is None:
            raise ValueError("fs_Hz must be provided to compute delta_f_osc_Hz.")

        fs_Hz = np.asarray(fs_Hz, dtype=float)
        avg_window = float(avg_window)
        invalid = (fs_Hz <= 0) | (avg_window <= 0)
        result = np.where(invalid, np.nan, fs_Hz * np.sqrt(avg_window))

        return result.item() if np.ndim(result) == 0 else result

    def df_osc_Hz(self, vin_mV, fs_Hz, variance=1, avg_window=1):
        df_samp = self.df_osc_sampling_Hz(fs_Hz, avg_window=avg_window)
        df_adev = variance * self.df_osc_adev_Hz(vin_mV, fs_Hz)
        out = np.maximum(df_samp, df_adev)
        return out.item() if np.ndim(out) == 0 else out
    
    def dVin_mV(self, df_osc_Hz, vin_mV):
        k_kHz_per_mV = self.kvco_kHz_per_mV(vin_mV)

        # Convert k from kHz/mV to Hz/mV
        k_Hz_per_mV = np.asarray(k_kHz_per_mV, dtype=float) * 1e3
        df_osc_Hz = np.asarray(df_osc_Hz, dtype=float)

        with np.errstate(divide='ignore', invalid='ignore'):
            out = df_osc_Hz / k_Hz_per_mV

        return out.item() if out.ndim == 0 else out

    def delta_G_uS(self, G_uS, vin_mV, i_dc_uA, fs_Hz, variance=1, avg_window=1):
        k_vco_kHz_per_mV = self.kvco_kHz_per_mV(vin_mV)  
        df_osc = self.df_osc_Hz(vin_mV=vin_mV, fs_Hz=fs_Hz, variance=variance, avg_window=avg_window)

        _, _, vin_2d, _, vin_scalar, idc_scalar = self._prepare_grid(vin_mV, i_dc_uA)
        vin_V = vin_2d * 1e-3

        G_2d = np.asarray(G_uS, dtype=float)
        if G_2d.ndim == 0:
            G_2d = np.array([[G_2d]])
        elif vin_scalar:
            G_2d = G_2d[:, None]
        elif idc_scalar:
            G_2d = G_2d[None, :]

        k_vco_kHz_per_mV_2d = np.asarray(k_vco_kHz_per_mV, dtype=float)
        if k_vco_kHz_per_mV_2d.ndim == 0:
            k_vco_kHz_per_mV_2d = np.full_like(vin_2d, k_vco_kHz_per_mV_2d, dtype=float)
        else:
            k_vco_kHz_per_mV_2d = np.broadcast_to(k_vco_kHz_per_mV_2d, k_vco_kHz_per_mV_2d.shape)

        k_vco_Hz_per_V = k_vco_kHz_per_mV_2d * 1e6

        with np.errstate(divide='ignore', invalid='ignore'):
            dG_uS = np.abs((df_osc * G_2d**2) / (k_vco_Hz_per_V * i_dc_uA + df_osc * G_2d))

        invalid = self._invalid_mask(vin_2d, self.params.vdd - vin_V)
        dG_uS = np.where(invalid, np.nan, dG_uS)

        return self._restore_shape(dG_uS, vin_scalar, idc_scalar)
    
    def idc_power_uW(self, vin_mV, i_dc_uA):
        vin_arr = np.asarray(vin_mV, dtype=float)
        i_arr = np.asarray(i_dc_uA, dtype=float)

        vin_V = vin_arr * 1e-3
        i_dc_A = i_arr * 1e-6
        power_idc_uW = i_dc_A * vin_V * 1e6


        invalid = (vin_arr < self.piecewise_threshold) | np.isnan(vin_arr) | np.isnan(i_arr)
        power_idc_uW = np.where(invalid, np.nan, power_idc_uW)

        return power_idc_uW.item() if np.isscalar(vin_mV) and np.isscalar(i_dc_uA) else power_idc_uW
    
    def pvco_from_vin(self, vin_mV):
        return np.interp(vin_mV, self.vin_data, self.pvco_data)

    def pcnt_from_vin(self, vin_mV):
        return np.interp(vin_mV, self.vin_data, self.pcnt_data)
    
    def i_dc_max(self, G_uS):
        return min(float(G_uS * (self.vdd_mV() - self.params.vin_min_mV) / 1000), self.params.idc_max)
    
    def compute_delta_G_range_nS(self, G_uS, fs_Hz, variance, avg_window):
        """Compute min/max deltaG range for given G and fs values"""
        i_vals = self.params.i_dc_range
        max_i_dc = self.i_dc_max(G_uS)
        i_vals_valid = i_vals[i_vals <= max_i_dc]
        
        deltaG_vals = []
        valid_mask = []
        for i_dc in i_vals_valid:
            vin_mV = self.vin_from_G(G_uS, i_dc)
            delta_G_us = self.delta_G_uS(
                G_uS=G_uS,
                vin_mV=vin_mV,
                i_dc_uA=i_dc,
                fs_Hz=fs_Hz,
                variance=variance,
                avg_window=avg_window
            )
            deltaG_vals.append(delta_G_us)
            valid_mask.append(np.isfinite(delta_G_us))

        
        deltaG_vals_nS = np.asarray(deltaG_vals, dtype=float) * 1000  # Convert to nS
        valid_mask = np.asarray(valid_mask, dtype=bool)

        if not np.any(valid_mask):
            return np.nan, np.nan

        return np.nanmin(deltaG_vals_nS[valid_mask]), np.nanmax(deltaG_vals_nS[valid_mask])

    # BEGIN: for retrocompatibility 
    def predict(self, vin_v, fs_adc=10, mode='single', v_cm=0.5):
        """
        vin_v: Input voltage (V). In 'diff' mode, this is the differential voltage (Vp-Vn).
        mode: 'single' or 'diff' (pseudo-differential).
        v_cm: Common-mode voltage for differential mode.
        """
        vin_v = np.atleast_1d(vin_v)
        fs_adc = np.atleast_1d(fs_adc)
        tau = np.clip(1.0 / fs_adc, 0.1, 5.0)

        # Handle broadcasting if fs_adc and vin_v have different shapes
        # e.g., if vin is 3D and fs is a scalar
        vin_v, tau = np.broadcast_arrays(vin_v, tau)

        if mode == 'single':
            f = self.interp_freq(vin_v)
            k = self.kvco_func(vin_v)

            # Vectorized 2D lookup: needs an (N, 2) array of points
            query_pts = np.stack([vin_v.ravel(), tau.ravel()], axis=-1)
            adev = self.interp_adev(query_pts).reshape(vin_v.shape)
            f_osc_error_Hz = f*adev
            ire_V = f_osc_error_Hz / np.abs(k)
            # Power (vectorized)
            pvco = self.interp_pvco(vin_v)
            pcnt = self.interp_pcnt(vin_v)
            p_tot = pvco + pcnt
            return {"f_osc": f, "k":k, "p_vco": pvco, "p_cnt": pcnt, "p_tot": p_tot, "p_vco2": 0, "p_cnt2": 0,"p_tot2": 0, "f_osc_error":f_osc_error_Hz,"ire": ire_V}
        else:
            # ... [Differential logic vectorized similarly] ...
            vp, vn = v_cm + vin_v/2, v_cm - vin_v/2
            f = self.interp_freq(vp) - self.interp_freq(vn)
            k = 0.5 * (self.kvco_func(vp) + self.kvco_func(vn))
            # ADEV lookups for both P and N branches
            pts_p = np.stack([vp.ravel(), tau.ravel()], axis=-1)
            pts_n = np.stack([vn.ravel(), tau.ravel()], axis=-1)
            adev_p = self.interp_adev(pts_p).reshape(vin_v.shape)
            adev_n = self.interp_adev(pts_n).reshape(vin_v.shape)
            ire_V = np.sqrt((adev_p*self.interp_freq(vp))**2 + (adev_n*self.interp_freq(vn))**2) / (2*np.abs(k))
            pvco = self.interp_pvco(vp)
            pcnt = self.interp_pcnt(vp)
            p_tot = pvco + pcnt
            pvco2 = self.interp_pvco(vn)
            pcnt2 = self.interp_pcnt(vn)
            ptot2 = pvco + pcnt + pvco2 + pcnt2
            return {"f_osc": f, "k":k, "p_vco": pvco, "p_cnt": pcnt, "p_tot": p_tot, "p_vco2": pvco2, "p_cnt2": pcnt2,"p_tot2": ptot2, "ire": ire_V}
    # END: for retrocompatibility 
