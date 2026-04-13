#In[]:
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

%matplotlib widget


# 1. Constants and Configuration
fs = 1e6
df = 16
lw = 11
file_path = 'dLC/test20.csv'

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