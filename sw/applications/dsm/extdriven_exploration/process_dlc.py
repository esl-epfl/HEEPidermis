#In[]:
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

from scipy import signal

%matplotlib widget


# 1. Constants and Configuration
fs = 1e6
df = 16
lw = 11
file_path = 'dLC/test18.csv'

# 2. Read the file
# We read the first two lines separately as headers
with open(file_path, 'r') as f:
    ses_cfg = f.readline().strip()
    dlc_cfg = f.readline().strip()

# Read the remaining data (assuming space or tab delimited based on your snippet)
# We take the second column (index 1)
df_data = pd.read_csv(file_path, skiprows=2, header=None, sep='\s+')
values_16bit = df_data[1].values

# 3. Processing
Dn_list = []
DL_list = []
Dt_list = []
sign_list = []
t_list = []
Ab_list = []
Db_list = []

current_t = 0
current_Ab = 0.5

for val in values_16bit:
    # Split the 16-bit number into two 8-bit numbers
    # Assuming Little Endian (Low Byte first, then High Byte)
    bytes_8bit = [val & 0xFF, (val >> 8) & 0xFF]

    for byte in bytes_8bit:
        try:
            # 2) Extract DL (2 LSBs) and Dn (6 MSBs)
            DL_raw = byte & 0x03       # Binary: 00000011
            sign_bit = (DL_raw >> 1) & 0x01
            magnitude = DL_raw & 0x01
            multiplier = -1 if sign_bit == 1 else 1
            Db = magnitude * multiplier
            current_Ab += Db

            Dn = byte >> 2             # Binary: 11111100 shifted

            if Dn == 0:
                Db_list[-1] += Db
                Ab_list[-1] = current_Ab
            else:
                Dn_list.append(Dn)
                Dt = Dn / (fs / df)
                Dt_list.append(Dt)
                current_t += Dt
                t_list.append(current_t)
                sign_list.append(sign_bit)
                DL_list.append(magnitude)
                Db_list.append(Db)
                Ab_list.append(current_Ab)
        except:
            pass




crop_window_n = 50
t_list      = t_list[crop_window_n:-crop_window_n]
lc_rec_norm = Ab_list[crop_window_n:-crop_window_n]
lc_rec_norm = lc_rec_norm - min(lc_rec_norm)
lc_rec_norm = np.array(lc_rec_norm)/(max(lc_rec_norm)-min(lc_rec_norm))


# Smoothing for comparison
t_smooth = np.linspace(t_list[0], t_list[-1], len(t_list) * 10)
Ab_linear = np.interp(t_smooth, t_list, lc_rec_norm)
fs = 1 / (t_smooth[1] - t_smooth[0])
# Low pass filter
cutoff = 3500  # Slightly above 3kHz to avoid hitting the passband too hard
order = 4      # Sharp enough to clean, soft enough to stay stable
nyq = 0.5 * fs
low = cutoff / nyq
b, a = signal.butter(order, low, btype='low')
Ab_smooth = signal.filtfilt(b, a, Ab_linear)

lc_rec_smooth_norm = Ab_smooth - min(Ab_smooth)
lc_rec_smooth_norm = np.array(lc_rec_smooth_norm)/(max(lc_rec_smooth_norm)-min(lc_rec_smooth_norm))


plt.figure(figsize=(5, 5))
plt.plot(t_list, lc_rec_norm, '-o', markersize=2, color='red', linewidth=1.5, label='LC reconstructed')
plt.plot(t_smooth, lc_rec_smooth_norm, color='blue', linewidth=1.5, label="LC rec + LPF'd")

plt.title('Reconstructed Signal (t vs Ab)')
plt.xlabel('Time (t)')
plt.ylabel('Accumulated Amplitude (Ab)')
plt.grid(True, linestyle='--', alpha=0.7)
plt.tight_layout()
plt.show()

#In[]:

from scipy.interpolate import make_interp_spline

from scipy import signal


plt.figure(figsize=(5, 5))
# 1. Setup oversampled time axis
# We use simple linear interpolation first to get a high-res "noisy" signal


# 2. Design a Low-Pass Filter
# fs is the "new" sampling frequency after oversampling

# Normalize frequency to Nyquist (fs/2)


# 3. Apply the filter forward and backward (Zero-phase)
# This removes the "jagged" edges from the linear interpolation


# 4. Plot

# plt.plot(t_list, Ab_list, 'o', markersize=3, color='#2ca02c', alpha=0.5)
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
    "time" : t_list,
    "signal": lc_rec_norm,
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
            fig, axs = plt.subplots(2,1, figsize=(6,3), sharex=True)
            axs[0].set_title(f"avg/best NRMSE: {result['nrmse_db']:1.0f}/{result['best_nrmse_db']:1.0f} dB" )
            axs[0].step(result["cropped_time"], result['cropped_fitted_sin'], c='g', linewidth=2)
            axs[0].step(result["cropped_time"], result["cropped_signal"], c='r')
            axs[1].step(result["cropped_time"][:-11],result['nrmse_window_db'],'-k')
            plt.show()

        # print(f"AS:{result['as']} | DF {result['df']} | wg {result['wg']} | ww {result['ww']} | avg/best NRMSE: {result['nrmse_db']:1.0f}/{result['best_nrmse_db']:1.0f} dB")
        data_range = max(result["cropped_signal"])-min(result["cropped_signal"])
        range_b = np.ceil(np.log2(data_range))
        avg = np.mean(result["cropped_signal"])
        gain_b = np.ceil(np.log2(avg))
        lsb = min(abs(np.diff(np.array(result["cropped_signal"]))[np.diff(np.array(result["cropped_signal"]))!=0]))


