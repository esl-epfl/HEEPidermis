#In[]:
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

%matplotlib widget


# 1. Constants and Configuration
fs = 1e6
df = 16
lw = 11
file_path = 'dLC/test5.csv'

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
        print(byte, f"{byte:08b}")
        # 2) Extract DL (2 LSBs) and Dn (6 MSBs)
        DL_raw = byte & 0x03       # Binary: 00000011
        Dn = byte >> 2             # Binary: 11111100 shifted
        Dn_list.append(Dn)

        # 3) Convert Dn into Dt
        Dt = Dn / (fs / df)
        Dt_list.append(Dt)
        # 4) Accumulate Dt into t
        current_t += Dt
        t_list.append(current_t)

        # 5) Take MSB of DL as sign (0=pos, 1=neg)
        # 6) Make DL only the LSB of DL (the magnitude)
        sign_bit = (DL_raw >> 1) & 0x01

        if Dt == 0: sign_bit ^= 1

        sign_list.append(sign_bit)
        magnitude = DL_raw & 0x01

        DL_list.append(magnitude)

        # Determine multiplier: 0 is positive (1), 1 is negative (-1)
        multiplier = -1 if sign_bit == 1 else 1

        # 7) Compute Db = magnitude * multiplier * 2^lw
        Db = magnitude * multiplier
        Db_list.append(Db)
        # 8) Accumulate Db into Ab
        current_Ab += Db

        # Store for plotting
        Ab_list.append(current_Ab)


# 4. Plotting
plt.figure(figsize=(5, 5))
plt.step(t_list, Ab_list, '-o', markersize=2, color='#2ca02c', linewidth=1.5,)
plt.title('Reconstructed Signal (t vs Ab)')
plt.xlabel('Time (t)')
plt.ylabel('Accumulated Amplitude (Ab)')
plt.grid(True, linestyle='--', alpha=0.7)
plt.tight_layout()

plt.show()