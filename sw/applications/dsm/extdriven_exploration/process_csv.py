#In[]:

import os
import re
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.interpolate import interp1d

filter = "CIC"
# fsin = 2929.6875
# fsin=976.5625
fsin=146.484375
outpath = f"./{int(fsin):g}Hz/fclk_8MHz/{filter}"

if fsin ==  146.484375:
  fsin = 146.484375 if filter=="SES" else 146.484375*1.5 # CIC filter needed *1.5 for DF=2, and *2 for DF=1

def process_files(outpath):
    path = Path(outpath)
    # Grab all .csv files starting with SES
    files = sorted(list(path.glob(f"{filter}*.csv")))

    print(len(files))
    parsed_data = []

    for i, file_path in enumerate(files):
        fname = file_path.name

        # 1. Parse filename using Regex
        # Pattern matches: SES_fclk_DIGITS_kHz__Wg_DIGITS_Ww_DIGITS_DF_DIGITS_AS_DIGITS
        pattern = r"_fclk_(\d+)_kHz__Wg_(\d+)_Ww_(\d+)_DF_(\d+)_AS_(\d+)"
        match = re.search(pattern, fname[3:])

        if match:
            meta = {
                'f_clk_kHz': int(match.group(1)),
                'wg': int(match.group(2)),
                'ww': int(match.group(3)),
                'df': int(match.group(4)),
                'as': int(match.group(5)),
                'path': file_path
            }

            # 2. Load Data
            # Assumes Column 0: Time (s), Column 1: Amplitude (int)
            data = np.loadtxt(file_path, delimiter='\t') # or ',' based on your previous save
            time_s = data[:, 0]
            data_int = data[:, 1]

            meta['time'] = time_s
            meta['signal'] = data_int
            parsed_data.append(meta)

    return parsed_data



all_results = process_files(outpath)

#In[]:

def compute_nrmse_db(results, f_sin_hz=3000, osr=128, plot=False):
    """
    Computes NRMSE in dB relative to an ideal sine.
    """
    signal = results['signal']
    time = results['time']

    # 1. Take central 90%
    n = len(signal)
    start_idx = int(n * 0.05)
    end_idx = int(n * 0.95)
    t_90 = time[start_idx:end_idx]
    s_90 = signal[start_idx:end_idx]

    # 2. Normalize to [0, 1] with mean 0.5
    s_min, s_max = np.min(s_90), np.max(s_90)
    s_norm = (s_90 - s_min) / (s_max - s_min)
    # Ensure mean is exactly 0.5 (optional shift if signal isn't perfectly symmetric)
    s_norm = s_norm - np.mean(s_norm) + 0.5

    # 3. Find sub-sample 0.5 crossings using interpolation
    # Find rising crossing (first time it goes from <0.5 to >0.5)
    rising_indices = np.where((s_norm[:-1] < 0.5) & (s_norm[1:] >= 0.5))[0]
    falling_indices = np.where((s_norm[:-1] > 0.5) & (s_norm[1:] <= 0.5))[0]

    if len(rising_indices) == 0 or len(falling_indices) == 0:
      results['nrmse_db'] = 0
      return 0

    first_idx = rising_indices[0]
    last_idx = falling_indices[-1]

    def get_crossing_time(idx):
        # Linear interpolation: t = t1 + (0.5 - y1) * (t2 - t1) / (y2 - y1)
        t1, t2 = t_90[idx], t_90[idx+1]
        y1, y2 = s_norm[idx], s_norm[idx+1]
        return t1 + (0.5 - y1) * (t2 - t1) / (y2 - y1)

    t_start = get_crossing_time(first_idx)
    t_end = get_crossing_time(last_idx)
    duration = t_end - t_start

    # 4. Generate Target Sine and New Time Scale
    fs_target = f_sin_hz
    t_new = np.arange(0, duration, 1/fs_target)

    # Target sine: starts at 0.5 rising, mean 0.5, amp 0.5
    sine_target = 0.5 + 0.5 * np.sin(2 * np.pi * f_sin_hz * t_new)

    # 5. Interpolate Original Signal onto t_new
    # We shift t_90 so that t_start is 0 for alignment
    interp_func = interp1d(t_90 - t_start, s_norm, kind='cubic', fill_value="extrapolate")
    s_resampled = interp_func(t_new)



    # 6. Compute NRMSE
    rmse = np.sqrt(np.mean((s_resampled - sine_target)**2))
    # NRMSE = RMSE / Range (Range is 1.0 because of our normalization)
    nrmse = rmse / 1.0

    nrmse_db = 20 * np.log10(nrmse)

    results['nrmse_db'] = nrmse_db
    results['cropped_data'] = s_resampled
    results['cropped_time'] = t_new
    results['cropped_ref'] = sine_target

    if plot:
      plt.figure(figsize=(5,2.5))
      plt.step(t_new, sine_target, c='g')
      plt.step(t_new, s_resampled, c='r')
      plt.title(f"Ww:{results['ww']} | Wg:{results['wg']} | AS: {results['as']} |DF: {results['df']} | NRMSE: {results['nrmse_db']:1.1f}")
      plt.show()

    return nrmse_db

#In[]:
%matplotlib inline

best_dB_idx = 0

for idx, results in enumerate(all_results):
  db_val = compute_nrmse_db(results, f_sin_hz=fsin, plot=True)
  if results['nrmse_db'] < all_results[best_dB_idx]['nrmse_db']:
      best_dB_idx = idx

best_result = all_results[best_dB_idx]

plt.figure(figsize=(5,2.5))
plt.step(best_result['cropped_time'], best_result['cropped_ref'], c='g')
plt.step(best_result['cropped_time'], best_result['cropped_data'], c='r')
plt.title(f"Ww:{best_result['ww']} | Wg:{best_result['wg']} | DF{best_result['df']} | NRMSE:{best_result['nrmse_db']:1.1f}")
plt.show()

plt.figure(figsize=(5,2.5))
length_n = len(best_result['time'])
start_after_some_time = int(0.1*length_n)
plt.step(best_result['time'][start_after_some_time:], best_result['signal'][start_after_some_time:], c='g')

import matplotlib.ticker as ticker
ax = plt.gca()
bit_width = 16 # Or use your 'ww' variable here
def to_binary(x, pos):
    return format(int(x), f'0{bit_width}b')
ax.yaxis.set_major_formatter(ticker.FuncFormatter(to_binary))
ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))

plt.title(f"Raw signal extracted")
plt.show()


#In[]:
# Plot all results
import pandas as pd


df = pd.DataFrame(all_results)

# 1. Calculate the Complexity Metric
# Using log2(as + 1) to handle the bit-depth contribution of 'as'
df['complexity'] = (1/df['df']) * df['wg'] #* np.log2(df['as'] + 1)

import plotly.express as px
pd.options.plotting.backend = "plotly"

df.plot.scatter(
    x="complexity",
    y="nrmse_db",
    color="ww",
    title="NRMSE vs Complexity",
    hover_data=["f_clk_kHz", "wg", "as", "df"]
)