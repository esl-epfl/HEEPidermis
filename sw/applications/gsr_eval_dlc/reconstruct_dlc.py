#In[]:
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

from scipy import signal

# VCO calibration tables (from VCO_sdk.c)
_TABLE_VIN_UV  = [330000,340000,360000,380000,400000,420000,440000,460000,480000,500000,
                   520000,540000,560000,580000,600000,620000,640000,660000,680000,700000,
                   720000,740000,760000,780000,800000]
_TABLE_FOSC_HZ = [24000,26130,31330,37320,45270,55150,67270,82680,99870,121190,
                   146020,175270,208990,247770,291780,341260,396650,457900,525140,598560,
                   677660,762750,853760,950200,1051710]
_VDD_UV        = 800000
_VCO_PHASES    = 62
_IDAC_LSB_NA   = 40

def _interp_vin_uV(f):
    if f <= _TABLE_FOSC_HZ[0]:  return float(_TABLE_VIN_UV[0])
    if f >= _TABLE_FOSC_HZ[-1]: return float(_TABLE_VIN_UV[-1])
    lo, hi = 0, len(_TABLE_FOSC_HZ) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if _TABLE_FOSC_HZ[mid] < f: lo = mid
        else: hi = mid
    x0, x1 = _TABLE_FOSC_HZ[lo], _TABLE_FOSC_HZ[hi]
    y0, y1 = _TABLE_VIN_UV[lo],  _TABLE_VIN_UV[hi]
    return y0 + (y1 - y0) * (f - x0) / (x1 - x0)

def _level_to_conductance_nS(level, level_width, vco_fs_hz, idac_nA):
    freq = level * level_width * vco_fs_hz / _VCO_PHASES
    vin  = _interp_vin_uV(freq)
    dv   = _VDD_UV - vin
    return (idac_nA * 1e6 / dv) if dv > 0 else 0.0


# 1. Constants and Configuration
_SCRIPT_DIR = Path(__file__).resolve().parent
file_path = _SCRIPT_DIR / 'sim.txt'

def _resample_to_sample_rate(event_ticks, event_values, sample_rate_hz):
    event_ticks = np.asarray(event_ticks, dtype=np.int64)
    event_values = np.asarray(event_values, dtype=float)

    if len(event_ticks) == 0:
        raise ValueError("No event ticks available for resampling")
    if sample_rate_hz <= 0:
        raise ValueError("SAMPLE_RATE_HZ must be > 0")

    uniform_ticks = np.arange(event_ticks[0], event_ticks[-1] + 1, dtype=np.int64)
    event_indices = np.searchsorted(event_ticks, uniform_ticks, side='right') - 1
    event_indices = np.clip(event_indices, 0, len(event_values) - 1)

    uniform_time = uniform_ticks.astype(float) / sample_rate_hz
    uniform_values = event_values[event_indices]

    return uniform_ticks, uniform_time, uniform_values

def _read_event_bytes(path):
    event_bytes = []

    with open(path, 'r') as f:
        for line in f.readlines()[2:]:
            parts = line.strip().split()
            if not parts:
                continue

            if parts[0] == 'HEX':
                text = ''.join(parts[1:])
                if len(text) % 2 != 0:
                    raise ValueError(f"odd number of hex digits in line: {line.rstrip()}")
                for i in range(0, len(text), 2):
                    event_bytes.append(int(text[i:i + 2], 16))
                continue

            if len(parts) >= 2:
                val = int(parts[1], 0)
                event_bytes.append(val & 0xFF)
                event_bytes.append((val >> 8) & 0xFF)

    return event_bytes

# 2. Read the file
# We read the first two lines separately as headers
with open(file_path, 'r') as f:
    ses_cfg = f.readline().strip()
    dlc_cfg = f.readline().strip()

# Parse DLC_CFG header for simulation parameters
dlc_params     = dict(kv.split('=') for kv in dlc_cfg.split() if '=' in kv)
sample_rate_hz = float(dlc_params['SAMPLE_RATE_HZ'])
lw             = int(dlc_params['LOG_LEVEL_WIDTH'])
initial_level  = int(dlc_params.get('INITIAL_LEVEL', 0))
idac_code      = int(dlc_params.get('IDAC_CODE', 7))
vco_fs_hz      = float(dlc_params['VCO_FS_HZ'])
level_width    = 1 << lw
idac_nA        = idac_code * _IDAC_LSB_NA

values_8bit = _read_event_bytes(file_path)

# 3. Processing
Dn_list = []
DL_list = []
Dt_list = []
sign_list = []
t_list = []
tick_list = []
Ab_list = []
Db_list = []

current_tick = 0
current_Ab = float(initial_level)

for byte in values_8bit:
    # 2) Extract DL (2 LSBs) and Dn (6 MSBs)
    DL_raw = byte & 0x03       # Binary: 00000011
    sign_bit = (DL_raw >> 1) & 0x01
    magnitude = DL_raw & 0x01
    multiplier = -1 if sign_bit == 1 else 1
    Db = magnitude * multiplier
    current_Ab += Db

    Dn = byte >> 2             # Binary: 11111100 shifted

    if Dn == 0:
        if Db_list:
            Db_list[-1] += Db
            Ab_list[-1] = current_Ab
        continue

    Dn_list.append(Dn)
    current_tick += Dn
    Dt = Dn / sample_rate_hz
    Dt_list.append(Dt)
    current_t = current_tick / sample_rate_hz
    t_list.append(current_t)
    tick_list.append(current_tick)
    sign_list.append(sign_bit)
    DL_list.append(magnitude)
    Db_list.append(Db)
    Ab_list.append(current_Ab)




conductance_list = [_level_to_conductance_nS(ab, level_width, vco_fs_hz, idac_nA) for ab in Ab_list]

crop_window_n = min(50, max(0, (len(t_list) - 3) // 4))
crop_end = -crop_window_n if crop_window_n else None
t_list         = np.array(t_list[crop_window_n:crop_end], dtype=float)
tick_list      = np.array(tick_list[crop_window_n:crop_end], dtype=np.int64)
lc_rec_norm    = np.array(conductance_list[crop_window_n:crop_end], dtype=float)
level_rec_norm = np.array(Ab_list[crop_window_n:crop_end], dtype=float)
db_rec_norm    = np.array(Db_list[crop_window_n:crop_end], dtype=float)

if len(t_list) == 0:
    raise ValueError("No reconstructed dLC records after cropping")


# Reconstruct the periodic signal expected by FE. dLC events are sparse change
# records; between events the decoded conductance level is held constant.
uniform_ticks, t_uniform, lc_rec_uniform = _resample_to_sample_rate(
    tick_list,
    lc_rec_norm,
    sample_rate_hz,
)

# Smoothing for comparison on the same uniform sample grid
if len(t_uniform) > 24 and t_uniform[-1] > t_uniform[0]:
    fs = sample_rate_hz
    # Low pass filter
    cutoff = min(3500, 0.45 * fs)
    order = 4      # Sharp enough to clean, soft enough to stay stable
    nyq = 0.5 * fs
    low = cutoff / nyq
    b, a = signal.butter(order, low, btype='low')
    Ab_smooth = signal.filtfilt(b, a, lc_rec_uniform)
else:
    Ab_smooth = lc_rec_uniform

lc_rec_smooth_norm = Ab_smooth

pd.DataFrame({
    'time_s':           t_uniform,
    'conductance_nS':   lc_rec_uniform,
}).to_csv(_SCRIPT_DIR / 'reconstructed_dlc.csv', index=False)

pd.DataFrame({
    'time_s':           t_list,
    'conductance_nS':   lc_rec_norm,
}).to_csv(_SCRIPT_DIR / 'reconstructed_dlc_events.csv', index=False)

pd.DataFrame({
    'time_s':               t_uniform,
    'conductance_lpf_nS':   lc_rec_smooth_norm,
}).to_csv(_SCRIPT_DIR / 'reconstructed_dlc_lpf.csv', index=False)


fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

axes[0].step(t_list, lc_rec_norm, label='Events', linewidth=1.0, where='post')
axes[0].plot(t_uniform, lc_rec_uniform, label='Uniform', linewidth=1.5)
axes[0].plot(t_uniform, lc_rec_smooth_norm, label='LPF', linewidth=1.5, linestyle='--')
axes[0].set_ylabel('Conductance (nS)')

axes[1].plot(t_list, level_rec_norm, label='dLC level', linewidth=1.5)
axes[1].set_ylabel('Level')

axes[2].step(t_list, db_rec_norm, label='Delta level', linewidth=1.5, where='post')
axes[2].set_ylabel('Delta level')
axes[2].set_xlabel(f'Time (s)  (SAMPLE_RATE_HZ={sample_rate_hz:g} Hz)')

for ax in axes:
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3)

fig.suptitle('dLC Reconstructed Conductance')
plt.tight_layout()
fig.savefig(_SCRIPT_DIR / 'reconstructed_conductance.png', dpi=150, bbox_inches='tight')
plt.show()


#In[]:

def nrmse_db(sig, ref):
    rmse    = np.sqrt(np.mean((sig - ref)**2))
    ampl    = (max(ref)-min(ref))
    nrmse   = rmse/ampl
    nrmse_db = 20 * np.log10(nrmse)
    return nrmse_db

def result_compute_nrmse_db( result ):
    sig = result["cropped_signal"]
    ref = result["cropped_fitted_sin"]

    result["nrmse_db"] = nrmse_db(sig, ref)
    return

def result_compute_nrmse_window_db(result, window_frac=0.1, step_frac=0.01):
    sig = result["cropped_signal"]
    ref = result["cropped_fitted_sin"]
    length_n = len(sig)
    window_size = int(length_n * window_frac)
    step_size = int(length_n * step_frac)

    result['nrmse_window_db'] = []
    for start in range(0, length_n - window_size + 1, step_size):
        end = start + window_size
        sig_window = sig[start:end]
        ref_window = ref[start:end]
        result['nrmse_window_db'].append(nrmse_db(sig_window, ref_window))

    result['best_nrmse_db'] = min(result['nrmse_window_db'])
    result_compute_nrmse_db(result)
    return

def result_crop_signal(result):
    og_signal  = result['signal']
    og_time    = result['time']

    n               = len(og_signal)
    start_idx       = int(n * 0.05)
    end_idx         = int(n * 0.95)
    cropped_signal  = og_signal[start_idx:end_idx]
    cropped_time    = og_time[start_idx:end_idx] - og_time[start_idx]

    result['cropped_signal']   = cropped_signal
    result['cropped_time']     = cropped_time
    return

def result_fit_sin(result):

    t       = result['cropped_time']
    y       = result['cropped_signal']
    fsin    = result['fsin_Hz']

    # 1. Create the known basis functions
    omega = 2 * np.pi * fsin
    sin_wave = np.sin(omega * t)
    cos_wave = np.cos(omega * t)
    ones = np.ones_like(t)

    # 2. Build the X matrix for linear regression
    # X shape will be (N, 3)
    X = np.column_stack([sin_wave, cos_wave, ones])

    # 3. Solve the linear least squares problem (Y = X * Beta)
    # Beta contains [c1, c2, c3]
    Beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    c1, c2, c3 = Beta

    # 4. Convert back to Amplitude, Phase, and Offset
    amplitude = np.sqrt(c1**2 + c2**2)
    phase = np.arctan2(c2, c1)
    offset = c3

    # Generate the aligned best-fit curve to compute your point-to-point error
    best_fit_y = amplitude * np.sin(omega * t + phase) + offset

    result['cropped_fitted_sin'] = best_fit_y

result = {
    "time" : t_uniform,
    "signal": lc_rec_uniform,
    "fsin_Hz": 2929.6875
}

all_results = [result]


plot = True
best_results = []

for result in all_results:
    result_crop_signal(result)
    result_fit_sin(result)
    result_compute_nrmse_window_db(result)

    if result['best_nrmse_db'] < -10:
        best_results.append(result)
        if plot:
            fig, axs = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
            axs[0].plot(result["cropped_time"], result['cropped_fitted_sin'],
                        label='Fitted sine', linewidth=1.5)
            axs[0].plot(result["cropped_time"], result["cropped_signal"],
                        label='Reconstructed', linewidth=1.5, linestyle='--')
            axs[0].set_ylabel('Conductance (nS)')

            axs[1].plot(result["cropped_time"][:len(result['nrmse_window_db'])],
                        result['nrmse_window_db'], label='Window NRMSE', linewidth=1.5)
            axs[1].set_ylabel('NRMSE (dB)')
            axs[1].set_xlabel('Time (s)')

            for ax in axs:
                ax.legend(loc='upper right')
                ax.grid(True, alpha=0.3)

            fig.suptitle(f"avg/best NRMSE: {result['nrmse_db']:1.0f}/{result['best_nrmse_db']:1.0f} dB")
            plt.tight_layout()
            plt.show()

        # print(f"AS:{result['as']} | DF {result['df']} | wg {result['wg']} | ww {result['ww']} | avg/best NRMSE: {result['nrmse_db']:1.0f}/{result['best_nrmse_db']:1.0f} dB")
        data_range = max(result["cropped_signal"])-min(result["cropped_signal"])
        range_b = np.ceil(np.log2(data_range))
        avg = np.mean(result["cropped_signal"])
        gain_b = np.ceil(np.log2(avg))
        lsb = min(abs(np.diff(np.array(result["cropped_signal"]))[np.diff(np.array(result["cropped_signal"]))!=0]))
