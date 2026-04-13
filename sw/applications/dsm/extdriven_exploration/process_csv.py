#In[]:
%matplotlib inline

import os
import re
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.interpolate import interp1d

filter = "SES"
fclk = "fclk_16MHz/"
fsin=146.484375
# fsin=976.5625
# fsin = 2929.6875
# for SES, 2929, 8MHz use time_scale = -1
time_scale = 1
outpath = f"./{int(fsin):g}Hz/{fclk}{filter}"
# outpath = "tests_NABLE_20_49_01_06_04_26/"

if fsin ==  146.484375:
  fsin = 146.484375 if filter=="SES" else 146.484375*1.5 # CIC filter needed *1.5 for DF=2, and *2 for DF=1

def process_files(outpath):
    global time_scale
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
                'f_clk_Hz': int(match.group(1))*1e3,
                'wg': int(match.group(2)),
                'ww': int(match.group(3)),
                'df': int(match.group(4)),
                'as': int(match.group(5)),
                'path': file_path
            }
            data = np.loadtxt(file_path, delimiter='\t')

            #FOR SOME REASON THE DF=1 IS DOING DF=2!!
            if time_scale == -1:
                time_scale = 2 if meta['df'] == 1 else 1

            meta['fs_sps'] = (meta['f_clk_Hz']/meta['df'])/time_scale
            time_s = data[:, 0]
            meta['time'] = time_s*time_scale

            data_int = data[:, 1]
            meta['signal'] = data_int
            parsed_data.append(meta)

            meta['fsin_Hz'] = fsin

    return parsed_data

all_results = process_files(outpath)


#In[]:
# Define auxiliary functions

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

#In[]:
# Crop and compute NRMSE

plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})

best_results = []

plot = 1

for result in all_results:
    result_crop_signal(result)
    result_fit_sin(result)
    result_compute_nrmse_window_db(result)

    if result['best_nrmse_db'] < -10:
        best_results.append(result)
        if plot:
            fig, axs = plt.subplots(2,1, figsize=(6,3))
            axs[0].set_title(f"AS:{result['as']} | DF {result['df']} | wg {result['wg']} | ww {result['ww']} | avg/best NRMSE: {result['nrmse_db']:1.0f}/{result['best_nrmse_db']:1.0f} dB" )
            axs[0].step(result["cropped_time"], result['cropped_fitted_sin'], c='g', linewidth=2)
            axs[0].step(result["cropped_time"], result["cropped_signal"], c='r')
            axs[1].step(range(len(result['nrmse_window_db'])),result['nrmse_window_db'],'-k')
            plt.show()

        # print(f"AS:{result['as']} | DF {result['df']} | wg {result['wg']} | ww {result['ww']} | avg/best NRMSE: {result['nrmse_db']:1.0f}/{result['best_nrmse_db']:1.0f} dB")
        data_range = max(result["cropped_signal"])-min(result["cropped_signal"])
        range_b = np.ceil(np.log2(data_range))
        avg = np.mean(result["cropped_signal"])
        gain_b = np.ceil(np.log2(avg))
        lsb = min(abs(np.diff(np.array(result["cropped_signal"]))[np.diff(np.array(result["cropped_signal"]))!=0]))

        # print(f"Range: {data_range} ({range_b} b) | avg: {int(avg)} ({gain_b} b) | lsb: {lsb}")

        print(f"{result['wg']}\t{result['ww']}\t{result['as']}\t{data_range}\t{int(avg)}\t{lsb}\t{result['df']}\t{result['nrmse_db']}\t{result['best_nrmse_db']}")

for result in all_results:
    if filter == "CIC":
        area_per_intg       = 461
        area_per_comb       = 4848
        ff_area_comb        = 3500
        other_area_comb     = area_per_comb - ff_area_comb
        area_per_delay      = ff_area_comb/16
        delays              = result['ww']
        eff_area_per_comb   = other_area_comb + delays*area_per_delay
        print(eff_area_per_comb, delays)
        freq_intg_MHz       = result['f_clk_Hz']/1e6
        freq_comb_MHz       = freq_intg_MHz/result['df']
        bitwidth            = 24
        datarate            = bitwidth*freq_intg_MHz/result['df']
        cost_intg           = area_per_intg*freq_intg_MHz
        cost_comb           = eff_area_per_comb*freq_comb_MHz
        stages              = np.log2(result['as'] + 1)
        total_cost          = stages*(cost_intg+cost_comb)*datarate
        print(f"total cost: {total_cost:1.2e}")
    if filter == "SES":
        area_per_stage      = 1300
        freq_stage_MHz      = result['f_clk_Hz']/1e6
        cost_stage          = area_per_stage*freq_stage_MHz
        bitwidth            = 24
        datarate            = bitwidth*freq_stage_MHz/result['df']
        stages              = np.log2(result['as'] + 1)
        total_cost          = stages*(cost_stage)*datarate
        print(f"total cost: {total_cost:1.2e}")

    result['complexity'] = total_cost

#In[]:
# Plot all results
import pandas as pd


df = pd.DataFrame(best_results)

print(len(df))

import plotly.express as px
pd.options.plotting.backend = "plotly"

fig = df.plot.scatter(
    x="complexity",
    y="nrmse_db",
    color="as",
    symbol='df',
    title="NRMSE vs Complexity",
    hover_data=["wg", "as", "df", 'ww']
)
fig.show()

print(len(best_results))


#In[]:
# Plot for paper
from matplotlib.ticker import FuncFormatter

result = best_results[0]

if fclk == "fclk_16MHz/":
    fig, ax = plt.subplots(figsize=(5,3))
else:
    fig, ax = plt.subplots(figsize=(3,3))

# ax.set_title(f"{filter} filter, NRMSE for a ~FS sine of {fsin/1e3:1.1f} kHz ")
colors = { 63:"red", 31:"green", 15:"blue"}
for AS in [63, 31, 15]:
    try:
        nrmse_windows = [ result['nrmse_window_db'] for result in best_results if result['as'] == AS]
        complexities  = [ result['complexity'] for result in best_results if result['as'] == AS]
        stages        = [ result['as'] for result in best_results if result['as'] == AS]

        box = ax.boxplot(nrmse_windows, positions=complexities+np.random.random(len(complexities))*0.1*np.array(complexities), widths=0.1*np.array(complexities), patch_artist=True,
                        showmeans=False, showfliers=False,
                        medianprops={"color": colors[AS], "linewidth": 1},
                        boxprops={"facecolor": colors[AS], "edgecolor": colors[AS],
                                "linewidth": 0.5, "alpha": 0.2},
                        whiskerprops={"color": colors[AS], "linewidth": 0.5},
                        capprops={"color": colors[AS], "linewidth": 0.5})
    except:
        pass

plt.grid(axis='y')
# plt.xlim(0.1,10)

plt.xlim(9e2,2e4)
plt.ylim(-80, -20)
plt.xscale('log')

formatter_u = FuncFormatter(lambda x, pos: f"{x:1.0e}")
ax.xaxis.set_major_formatter(formatter_u)


if fclk == "fclk_16MHz/":
    plt.ylabel("NRMSE (dB)")
    plt.xlim(5,5e2)
else:
    ax.set_yticklabels([])

# plt.xlabel(r"Complexity=$f_{s} \times$ bitwidth $\times$ stages")
plt.xlabel("Complexity")
plt.tight_layout()
plt.savefig(f"./figs/NRMSE_vs_complexity_{filter}_{int(fsin):g}Hz_{fclk[:-1]}.png", dpi=400)
plt.show()



