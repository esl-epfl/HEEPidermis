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

if 0:

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


#In[]:
# Compare against CIC


def H_z_SES(wg, ww0, wwi, fs=1e6):
    # first stage (with gain)
    b1 = [0, 2**(wg - ww0)]
    a1 = [1, -(1 - 2**(-ww0))]

    w, h1 = signal.freqz(b1, a1, worN=8000)
    his = []
    for ww in wwi:
        # remaining stages (no gain)
        b2 = [0, 1]
        a2 = [1, -(1 - 2**(-ww))]

        _, hi = signal.freqz(b2, a2, worN=8000)
        his.append(hi)

    # cascade
    h_total = h1
    for hi in his:
        h_total *= hi

    freq_hz = w * fs / (2*np.pi)
    return freq_hz, 20*np.log10(np.abs(h_total) + 1e-12)

def H_z_CIC(N, D, R, fs=1e6):
    # The delay of the comb section is M = R * D
    M = int(R * D)

    # Numerator: 1 - z^(-M)
    # This creates an array [1, 0, 0, ..., -1] of length M+1
    b = np.zeros(M + 1)
    b[0] = 1
    b[-1] = -1

    # Denominator: 1 - z^(-1)
    a = [1, -1]

    # Calculate frequency response for the single stage
    w, h = signal.freqz(b, a, worN=8000)

    # Cascade the stages by raising the response to the power N
    h_total = h**N

    # Convert to Hz and dB
    freq_hz = w * fs / (2 * np.pi)
    # Adding 1e-12 prevents log10(0) errors at the zeros of the filter
    magnitude_db = 20 * np.log10(np.abs(h_total) + 1e-12)

    return freq_hz, magnitude_db


%matplotlib widget

compare_cost    = 0
compare_droop   = 0
compare_atte    = 0
compare_test    = 1

if compare_cost:
    # SES params
    ses_wg = 16
    ses_n1 = 9
    ses_n2 = 0
    ses_ww = 4
    ses_df = 25
    # CIC params
    cic_df = 50
    cic_d  = 1
    cic_n  = 9


if compare_cost:
    # SES params
    ses_wg = 16
    ses_n1 = 9
    ses_n2 = 0
    ses_ww = 4
    ses_df = 25
    # CIC params
    cic_df = 50
    cic_d  = 1
    cic_n  = 9

if compare_droop:
    # SES params
    ses_wg = 16
    ses_n1 = 5
    ses_n2 = 20
    ses_ww = 4
    ses_df = 25
    # CIC params
    cic_df = 50
    cic_d  = 1
    cic_n  = 9

if compare_atte:
    # SES params
    ses_wg = 16
    ses_n1 = 5
    ses_n2 = 11
    ses_ww = 4
    ses_df = 25
    # CIC params
    cic_df = 50
    cic_d  = 1
    cic_n  = 6

ses_ww1 = np.ones(ses_n1)*ses_ww
ses_ww2 = np.ones(ses_n2)*ses_ww-2
ses_cost = (ses_n1+ses_n2)**2
cic_n_area = cic_n*1.56 # Area/stage difference wrt SES (D=1), Words=24 bits
cic_cost = (cic_n_area + (1+cic_d)*cic_n_area/cic_df) * ((2+cic_d)*cic_n_area)

fbw_Hz = 5e3
fnyq_Hz = fbw_Hz*2
fs_Hz   = fnyq_Hz*100 #  1e6


plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})
fig, axs = plt.subplots(figsize=(4.5,3), sharex=True)

ses_Hz, ses_dB = H_z_SES(wg=ses_wg, ww0=ses_ww1[0], wwi=np.concatenate((ses_ww1[1:],ses_ww2)), fs=fs_Hz)
ses_dB -= ses_dB[1]

alias_f = (fs_Hz / ses_df) - fnyq_Hz/2
alias_start_n = np.argmin(abs(ses_Hz - alias_f))
alias_start_dB  = ses_dB[alias_start_n]
alias_start_Hz  = ses_Hz[alias_start_n]

droop_f = fbw_Hz
droop_n = np.argmin(abs(ses_Hz - droop_f))
droop_dB  = ses_dB[droop_n]

axs.plot(ses_Hz/fbw_Hz, ses_dB,  '--', color='b', label=f"SES")
axs.axhline(droop_dB,linestyle=':', linewidth=1, color='b', label=f"droop: {droop_dB:1.1f} dB")
axs.axhline(alias_start_dB,linestyle='--', linewidth=1, color='b', label=f"Att: {alias_start_dB:1.1f} dB")
axs.scatter(alias_start_Hz/fbw_Hz, alias_start_dB, color='blue', marker='o', s=50)
axs.axvline(fs_Hz/fbw_Hz/ses_df,linestyle='-.', linewidth=1.5, color='blue', alpha=0.3)


cic_Hz, cic_dB = H_z_CIC(D=cic_d, N=cic_n, R=cic_df, fs=fs_Hz)
cic_dB -= cic_dB[1]

droop_f = fbw_Hz
droop_n = np.argmin(abs(cic_Hz - droop_f))
droop_dB  = cic_dB[droop_n]
droop_Hz  = cic_Hz[droop_n]

alias_f = (fs_Hz / cic_df) - fnyq_Hz/2
alias_start_n = np.argmin(abs(cic_Hz - alias_f))
alias_start_dB  = cic_dB[alias_start_n]
alias_start_Hz  = cic_Hz[alias_start_n]
axs.plot(cic_Hz/fbw_Hz, cic_dB,  '--', color='r', label=f"CIC (cost {cic_cost/ses_cost:1.1f}×)")
axs.axhline(droop_dB,linestyle=':', linewidth=1, color='r', label=f"droop: {droop_dB:1.1f} dB")
axs.axhline(alias_start_dB,linestyle='--', linewidth=1, color='r', label=f"Att: {alias_start_dB:1.1f} dB")
axs.scatter(alias_start_Hz/fbw_Hz, alias_start_dB, color='red', marker='o', s=50)
axs.axvline(fs_Hz/fbw_Hz/cic_df,linestyle='-.', linewidth=1.5, color='red', alpha=0.3)


axs.axvline(1,linestyle=':', linewidth=1.5, color='black', label=r'$\text{f}_\text{BW}$')
axs.axvline(0,linestyle='-.', linewidth=1.5, color='black', alpha=0.3, label=r'$\text{f}_\text{s}$')


for k in range(1, int(cic_df/2)+1):

        fc = (k * fs_Hz /fbw_Hz/ cic_df)
        width = (fnyq_Hz/fbw_Hz)
        plt.axvspan(fc - width/2, fc + width/2, color='red', alpha=0.07,
                    label='Alias CIC' if k==1 else "")

for k in range(1, int(ses_df/2)+1):
        fc = (k * fs_Hz /fbw_Hz/ ses_df)
        width = (fnyq_Hz/fbw_Hz)
        plt.axvspan(fc - width/2, fc + width/2, color='blue', alpha=0.07,
                    label='Alias SES' if k==1 else "")






axs.set_ylabel("Normalized gain (dB)")
# axs.set_ylim(-50,32)
axs.grid(which='both', alpha=0.5)
axs.legend(loc='lower left', fontsize=8, borderaxespad=1, framealpha=1)
plt.xscale("log")
# plt.xlim(200, 50e3)
plt.xlabel(r"Frequency (Normalized to $f_\text{BW}$)")
plt.ylim(-150,20)
plt.xlim(1e-1,1e1)

plt.tight_layout()
plt.savefig(f"./figs/SES_vs_CIC_H(z)_{'cost' if compare_cost else 'droop' if compare_droop else 'att' if compare_atte else "test"}.png", dpi=400)
plt.show()

