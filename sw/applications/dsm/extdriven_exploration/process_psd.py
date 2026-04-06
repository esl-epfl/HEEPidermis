#In[]:
import pandas as pd
import io
import re
import matplotlib.pyplot as plt
import numpy as np

%matplotlib inline

files = ["SNR/PSD_SES_fsin=2929Hz_fs=8MHz_and_DSM_bitstream.csv",
         "SNR/PSD_SES_fsin=2929Hz_fs=8MHz_and_DSM_bitstream_AS_version.csv"
         ]

all_results = []
for file in files:
    df = pd.read_csv(file)
    cols = df.columns

    for i in range(0, len(cols), 2):
        col_freq = cols[i]
        col_amp = cols[i+1]

        freq_vals = df[col_freq].dropna().values
        amp_vals = df[col_amp].dropna().values

        if "DSM" in col_freq or "raw bitstream" in col_freq:
            m = re.search(r'(.*)\s*\|\s*(S?Q?NR)=([\d.]+)\s*dB', col_freq)
            if m:
                name = m.group(1).strip()
                snr_val = float(m.group(3))
                all_results.append({
                    "name": name,
                    "sqnr_dB": snr_val,
                    "freq_hz": freq_vals,
                    "ampl_db": amp_vals,
                    "is_dsm": True
                })
        else:
            m = re.search(r'(SES_fclk_(\d+)_kHz__Wg_(\d+)_Ww_(\d+)_DF_(\d+)_AS_(\d+))\.csv\s*\|\s*SNR=([\d.]+)\s*dB', col_freq)
            if m:
                name = m.group(1)
                fclk = int(m.group(2))
                wg = int(m.group(3))
                ww = int(m.group(4))
                df_val = int(m.group(5))
                as_val = int(m.group(6))
                snr_val = float(m.group(7))

                all_results.append({
                    "name": name,
                    "filter": "SES",
                    "snr_dB": snr_val,
                    "fclk_kHz": fclk,
                    "wg": wg,
                    "ww": ww,
                    "df": df_val,
                    "as": as_val,
                    "freq_hz": freq_vals,
                    "ampl_db": amp_vals,
                    "is_dsm": False
                })
            print(name)

# Ensure we found the ideal
dsm_ideal = [r for r in all_results if r.get("is_dsm") and "raw bitstream" in r["name"]][-1]


#In[]:
# Try them all!

from scipy import signal

wgs  = [16, 16, 16]
wws  = [6,  6 , 3 ]
Ns   = [63,  31, 31 ]
df = 16
fsin = 2929.6875
fs_Hz = 1e6

plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})

idx = 0
for target in all_results:
    try:
        wg = target['wg']
        ww = target['ww']
        n = target['as']
        df = target['df']
    except:
        print(f"skipping {target['name']}")
        continue

    small_axis = 1
    if small_axis:
        xp = dsm_ideal['freq_hz']
        fp = dsm_ideal["ampl_db"]
        x = target['freq_hz']
        y = target["ampl_db"]

        ampl_interp = np.interp(x=x, xp=xp, fp=fp )
        H_meas_db = 0.5*(y-ampl_interp)
    else:
        x = dsm_ideal['freq_hz']
        y = dsm_ideal["ampl_db"]
        xp = target['freq_hz']
        fp = target["ampl_db"]

        ampl_interp = np.interp(x=x, xp=xp, fp=fp )
        H_meas_db = 0.5*(ampl_interp-y)

    # remove the tone
    f_closest = min(abs(np.array(x)-fsin))
    f_tone_idx = int(np.argmin(abs(np.array(x)-fsin)))
    crop_range = int(8)

    f_meas_hz = np.concatenate((x[0:f_tone_idx-crop_range], x[f_tone_idx+crop_range:]))
    H_meas_db = np.concatenate((H_meas_db[0:f_tone_idx-crop_range], H_meas_db[f_tone_idx+crop_range:]))


    def get_H_z_theoretical(wg, ww, N, fs=1e6):
        # first stage (with gain)
        b1 = [0, 2**(wg - ww)]
        a1 = [1, -(1 - 2**(-ww))]

        # remaining stages (no gain)
        b2 = [0, 1]
        a2 = [1, -(1 - 2**(-ww))]

        w, h1 = signal.freqz(b1, a1, worN=8000)
        _, h2 = signal.freqz(b2, a2, worN=8000)

        # cascade
        h_total = h1 * (h2 ** (N - 1))

        freq_hz = w * fs / (2*np.pi)
        return freq_hz, 20*np.log10(np.abs(h_total) + 1e-12)

    freq_hz, H_theo_db = get_H_z_theoretical(wg, ww-1, N=np.log2(n+1), fs=fs_Hz)
    H_theo_db -= H_theo_db[0]
    H_meas_db -= np.mean(H_meas_db[6:30])

    fc_theoretical = -fs_Hz/(2*np.pi)*np.log(1-2**(-ww))

    fig, axs = plt.subplots(figsize=(4.5,2))

    axs.plot(f_meas_hz, H_meas_db,color='gray', label="Measured")
    axs.plot(freq_hz, H_theo_db,  '--', color='k', label="Theoretical")
    axs.axvline(fc_theoretical,linestyle=':', linewidth=1.5, color='k')
    axs.axhline(-3,linestyle=':', linewidth=1.5, color='k', label="Cutoff")
    axs.grid(which='both')
    axs.set_ylabel("Amplitude (dB)")
    axs.set_ylim(-50, 30)
    axs.set_title(f"$W_g$={wg}, $W_w$={ww}, stages={np.log2(n+1):g}, DF: {df}")
    axs.legend(loc='lower left')
    plt.xscale("log")
    plt.xlim(50, 50e3)
    plt.xlabel("Frequency (Hz)")
    plt.tight_layout()
    plt.show()



#In[]:
# Plot three selected transfers

%matplotlib inline

from scipy import signal

wgs  = [16, 16, 16]
wws  = [5,  5 , 4 ]
Ns   = [31,  15, 15 ]
df = 16
fsin = 2929.6875
fs_Hz = 1e6

plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})
fig, axs = plt.subplots(4,1, figsize=(4.5,4.5), sharex=True)

idx = 0
for wg, ww, n in zip(wgs, wws, Ns):
    idx += 1
    target = [r for r in all_results if not r.get("is_dsm") and r["wg"] == wg and r["df"] == df and r["ww"]==ww and r["as"]==n][0]

    # plt.figure()
    # plt.plot(dsm_ideal['freq_hz'], dsm_ideal["ampl_db"])
    # plt.plot(target['freq_hz'], target["ampl_db"])
    # plt.xscale("log")
    # plt.show()

    small_axis = 1
    if small_axis:
        xp = dsm_ideal['freq_hz']
        fp = dsm_ideal["ampl_db"]
        x = target['freq_hz']
        y = target["ampl_db"]

        ampl_interp = np.interp(x=x, xp=xp, fp=fp )
        H_meas_db = 0.5*(y-ampl_interp)
    else:
        x = dsm_ideal['freq_hz']
        y = dsm_ideal["ampl_db"]
        xp = target['freq_hz']
        fp = target["ampl_db"]

        ampl_interp = np.interp(x=x, xp=xp, fp=fp )
        H_meas_db = 0.5*(ampl_interp-y)

    # remove the tone
    f_closest = min(abs(np.array(x)-fsin))
    f_tone_idx = int(np.argmin(abs(np.array(x)-fsin)))
    crop_range = int(8)
    dc_crop = 3

    f_meas_hz = np.concatenate((x[dc_crop:f_tone_idx-crop_range], x[f_tone_idx+crop_range:]))
    H_meas_db = np.concatenate((H_meas_db[dc_crop:f_tone_idx-crop_range], H_meas_db[f_tone_idx+crop_range:]))


    def get_H_z_theoretical(wg, ww, N, fs=1e6):
        # first stage (with gain)
        b1 = [0, 2**(wg - ww)]
        a1 = [1, -(1 - 2**(-ww))]

        # remaining stages (no gain)
        b2 = [0, 1]
        a2 = [1, -(1 - 2**(-ww))]

        w, h1 = signal.freqz(b1, a1, worN=8000)
        _, h2 = signal.freqz(b2, a2, worN=8000)

        # cascade
        h_total = h1 * (h2 ** (N - 1))

        freq_hz = w * fs / (2*np.pi)
        return freq_hz, 20*np.log10(np.abs(h_total) + 1e-12)

    freq_hz, H_theo_db = get_H_z_theoretical(wg, ww-1, N=np.log2(n+1), fs=fs_Hz)
    H_theo_db -= H_theo_db[0]
    H_meas_db -= np.mean(H_meas_db[6:30])

    fc_theoretical = -fs_Hz/(2*np.pi)*np.log(1-2**(-ww))

    axs[idx].text(450, 15,f"$W_g$={wg}, $W_w$={ww}, stages={np.log2(n+1):g}",bbox=dict(facecolor='white', edgecolor='none', alpha=1, pad=0))
    axs[idx].plot(f_meas_hz, H_meas_db,color='gray', label="Measured")
    axs[idx].plot(freq_hz, H_theo_db,  '--', color='k', label="Theoretical")
    axs[idx].axvline(fc_theoretical,linestyle=':', linewidth=1.5, color='k')
    axs[idx].axhline(-3,linestyle=':', linewidth=1.5, color='k')
    axs[idx].grid(which='both')
    # axs[idx].set_ylabel("Amplitude (dB)")
    axs[idx].set_ylim(-50,32)



wg = 16
ww = 5
n  = 31
df = 16
target = [r for r in all_results if not r.get("is_dsm") and r["wg"] == wg and r["df"] == df and r["ww"]==ww and r["as"]==n][0]

offset = 0
axs[0].text(450, -150,f"$W_g$={wg}, $W_w$={ww}, stages={np.log2(n+1):g}",bbox=dict(facecolor='white', edgecolor='none', alpha=1, pad=0))
axs[0].semilogx(dsm_ideal["freq_hz"], dsm_ideal["ampl_db"] + offset, color='red', label="Input", linewidth=2.5)
axs[0].semilogx(target["freq_hz"], target['ampl_db'], color='k', label="Output")
# axs[0].set_ylabel('Amplitude (dB)')
axs[0].grid(True, which="both")
axs[0].legend(loc='upper left', fontsize=8, borderaxespad=0.2)
fc_theoretical = -fs_Hz/(2*np.pi)*np.log(1-2**(-ww))
axs[0].axvline(fc_theoretical,linestyle=':', linewidth=1.5, color='k', label='Cutoff')
axs[0].text(fc_theoretical+300, -25, "← cutoff", fontsize=9, bbox=dict(facecolor='white', edgecolor='none', alpha=0.8, pad=0.5))
axs[0].set_ylim(-170,20)
axs[0].set_title("Amplitude (dBFS) and Gain (dB)")

axs[idx].legend(loc='lower left', fontsize=8, borderaxespad=0.1)
plt.xscale("log")
plt.xlim(200, 50e3)
plt.xlabel("Frequency (Hz)")
plt.tight_layout()
plt.savefig("figs/different_transfers.png",dpi=400)
plt.show()



#In[]:

xlim_0 = 50
xlim_1 = 50e3

# Plot 1
plot1_cands = [r for r in all_results if not r.get("is_dsm") and r["wg"] == 16 and r["df"] == 16]
# min_as = min(r["as"] for r in plot1_cands)
plot1_targets = [r for r in plot1_cands if r["as"] == 31]
plot1_targets.sort(key=lambda x: x["ww"])

plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})

fig, axs = plt.subplots(figsize=(4.5, 2))

wg = 16
ww = 5
n  = 31
df = 16

target = [r for r in all_results if not r.get("is_dsm") and r["wg"] == wg and r["df"] == df and r["ww"]==ww and r["as"]==n][0]

offset = 10
axs.semilogx(dsm_ideal["freq_hz"], dsm_ideal["ampl_db"] + offset, color='red', label="Measured input", linewidth=2)
axs.semilogx(target["freq_hz"], target['ampl_db'], color='k', label="Measured output")

axs.set_ylabel('Amplitude (dB)')
axs.legend(loc='upper left', title="5 stages\nWw:")
axs.grid(True, which="both", ls="--")
axs.set_xlim(xlim_0,xlim_1)

# axs[1].set_title('Amplitude vs Frequency (Wg=16, Ww=5)')
# axs.set_xlabel('Frequency (Hz)')
axs.set_xticklabels([])
axs.set_ylabel('Amplitude (dB)')
axs.legend(loc='upper left')
axs.grid(True, which="both", ls="--")
axs.set_xlim(xlim_0,xlim_1)

fc_theoretical = -fs_Hz/(2*np.pi)*np.log(1-2**(-ww))
axs.axvline(fc_theoretical,linestyle=':', linewidth=1.5, color='k', label='Cutoff')

plt.tight_layout()
plt.savefig('figs/psd_vs_tone.png', dpi=400)













#In[]:

xlim_0 = 100
xlim_1 = 100e3

# Plot 1
plot1_cands = [r for r in all_results if not r.get("is_dsm") and r["wg"] == 16 and r["df"] == 16]
# min_as = min(r["as"] for r in plot1_cands)
plot1_targets = [r for r in plot1_cands if r["as"] == 31]
plot1_targets.sort(key=lambda x: x["ww"])

plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})

fig, axs = plt.subplots(2,1,figsize=(6, 6), sharex=True)
window_size = 1
weights = np.ones(window_size) / window_size

max_ww = max(r["ww"] for r in plot1_targets)
min_ww = min(r["ww"] for r in plot1_targets)

for r in plot1_targets:
    # Lower ww -> darker gray -> higher alpha
    # Scale alpha between 1.0 (lowest ww) to 0.3 (highest ww)
    if max_ww == min_ww:
        alpha = 1.0
    else:
        alpha = 1.0 - 0.7 * (r["ww"] - min_ww) / (max_ww - min_ww)

    smoothed_amp = np.convolve(r["ampl_db"], weights, mode='same')
    axs[0].semilogx(r["freq_hz"], smoothed_amp, color='black', alpha=alpha, label=f'{r["ww"]}')

# axs[0].set_title('Amplitude vs Frequency (Wg=16, DF=16, AS=min)')

offset = 10

window_size = 1
weights = np.ones(window_size) / window_size
smoothed_amp = np.convolve(dsm_ideal["ampl_db"], weights, mode='same')
axs[0].semilogx(dsm_ideal["freq_hz"], smoothed_amp + offset, color='red', label="Input", linewidth=2)

axs[0].set_ylabel('Amplitude (dB)')
axs[0].legend(loc='upper left', title="5 stages\nWw:")
axs[0].grid(True, which="both", ls="--")
axs[0].set_xlim(xlim_0,xlim_1)

# Plot 2
plot2_cands = [r for r in all_results if not r.get("is_dsm") and r["wg"] == 16 and r["ww"] == 5 and r["df"] == 16]
plot2_targets = [r for r in plot2_cands]
plot2_targets.sort(key=lambda x: x["as"])


max_as = max(r["as"] for r in plot2_targets) if plot2_targets else 1
min_as = min(r["as"] for r in plot2_targets) if plot2_targets else 0

for r in plot2_targets:
    # Lower AS -> darker gray -> higher alpha
    if max_as == min_as:
        alpha = 1.0
    else:
        alpha = 1.0 - 0.7 * (r["as"] - min_as) / (max_as - min_as)

    smoothed_amp = np.convolve(r["ampl_db"], weights, mode='same')
    axs[1].semilogx(r["freq_hz"], smoothed_amp, color='black', alpha=alpha, label=f'{np.log2(r["as"]+1):g}')

smoothed_amp = np.convolve(dsm_ideal["ampl_db"], weights, mode='same')
axs[1].semilogx(dsm_ideal["freq_hz"], smoothed_amp+offset, color='red', label="Input", linewidth=2)

# axs[1].set_title('Amplitude vs Frequency (Wg=16, Ww=5)')
axs[1].set_xlabel('Frequency (Hz)')
axs[1].set_ylabel('Amplitude (dB)')
axs[1].legend(loc='upper left', title="Ww=5\nstages:")
axs[1].grid(True, which="both", ls="--")
axs[1].set_xlim(xlim_0,xlim_1)

plt.tight_layout()
plt.savefig('figs/psd_analysis.png', dpi=400)

