#In[]:
# Allan deviation plots

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def calculate_overlapping_adev(data, fs, taus):
    """Calculates Overlapping Allan Deviation for a frequency series."""
    y = data / np.mean(data) # Fractional frequency
    adevs = []
    tau0 = 1.0 / fs
    for tau in taus:
        m = int(round(tau / tau0))
        if 2 * m > len(y):
            adevs.append(np.nan)
            continue
        weights = np.ones(m) / m
        y_bar = np.convolve(y, weights, 'valid')
        diff = y_bar[m:] - y_bar[:-m]
        avar = 0.5 * np.mean(diff**2)
        adevs.append(np.sqrt(avar))
    return np.array(adevs)

def load_and_clean(filename):
    """Reads CSV, removes commas from numbers, and converts to float."""
    df = pd.read_csv(filename)
    for col in df.columns:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    return df

# Settings
fs = 10 # 10 Hz
taus = np.logspace(np.log10(0.1), np.log10(5.0), 20) # 0.1s to 5s

# Load Data
df_n = load_and_clean('data/VCO variability - summary N.csv')
df_p = load_and_clean('data/VCO variability - summary P.csv') # Ensure this file is present

fig, axes = plt.subplots(1, 3, figsize=(10, 3), sharey=True)
voltages = df_n.columns[5:]
colors = plt.cm.viridis(np.linspace(0, 1, len(voltages)))

for i, v in enumerate(voltages):
    n_data = df_n[v].dropna().values
    p_data = df_p[v].dropna().values if df_p is not None else None

    # 1. Plot VCO N
    adev_n = calculate_overlapping_adev(n_data, fs, taus)
    axes[0].loglog(taus, adev_n, color=colors[i], alpha=0.6)

    if p_data is not None:
        # Align lengths for differential calculation
        min_len = min(len(n_data), len(p_data))
        diff_data = n_data[:min_len] - p_data[:min_len]

        # 2. Plot VCO P
        adev_p = calculate_overlapping_adev(p_data[:min_len], fs, taus)
        axes[1].loglog(taus, adev_p, color=colors[i], alpha=0.6)

        # 3. Plot Pseudo-Differential
        adev_d = calculate_overlapping_adev(diff_data, fs, taus)
        axes[2].loglog(taus, adev_d, color=colors[i], alpha=0.8, label=f"{v}V")

# Formatting
titles = ['VCO N (Input)', 'VCO P (Supply Ref)', 'Pseudo-Differential (N-P)']
for ax, title in zip(axes, titles):
    ax.set_title(title, fontweight='bold')
    ax.set_xlabel('Tau (s)')
    ax.set_ylabel('Allan Deviation $\sigma_y(\\tau)$')
    ax.grid(True, which="both", ls="-", alpha=0.2)

axes[2].legend(title="Input Voltage", bbox_to_anchor=(1.05, 1), loc='upper left', fontsize='x-small', ncol=2)
plt.tight_layout()
plt.savefig('figs/vco_stability_report.png', dpi=600)


#In[]:
# Input referred error

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def load_and_clean(filename):
    df = pd.read_csv(filename)
    for col in df.columns:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    return df

def calculate_overlapping_adev(data, fs, taus):
    y = data / np.mean(data)
    adevs = []
    tau0 = 1.0 / fs
    for tau in taus:
        m = int(round(tau / tau0))
        if 2 * m > len(y):
            adevs.append(np.nan)
            continue
        weights = np.ones(m) / m
        y_bar = np.convolve(y, weights, 'valid')
        diff = y_bar[m:] - y_bar[:-m]
        avar = 0.5 * np.mean(diff**2)
        adevs.append(np.sqrt(avar))
    return np.array(adevs)

# Load data
df_n = load_and_clean('data/VCO variability - summary N.csv')
voltages = np.array([float(c) for c in df_n.columns])
f_means = np.array([df_n[col].mean() for col in df_n.columns])

# Calculate KVCO (Hz/V) from the summary data itself
# Since we don't have the transfer file, we derive it from the column headers and mean values
kvco = np.gradient(f_means, voltages)

# Constants
fs = 10
selected_taus = np.array([0.1, 0.5, 1.0])
selected_fs = np.array([1, 2, 10, 100])
colours = ["black", "gray", "lightgray", 'red']

adev_results = []
ire_results = []
frequency_uncertainty_results = []
fquant_results = []
quant_results = []

for i, col in enumerate(df_n.columns):
    data = df_n[col].dropna().values
    adevs = calculate_overlapping_adev(data, fs, selected_taus)
    # IRE (V) = ADEV * (f / KVCO)
    # If KVCO is 0 or negative (due to noise in gradient), we handle it
    scaling_factor = f_means[i] / np.abs(kvco[i]) if kvco[i] != 0 else np.nan
    ires = adevs * scaling_factor
    # frequency_uncertainty_results (Hz) = ADEV * f 
    frequency_uncertainty_results.append(adevs * f_means[i])

    fquant = selected_fs#1/selected_taus
    vquant = fquant/np.abs(kvco[i]) if kvco[i] != 0 else np.nan
    quant_results.append(vquant)
    fquant_results.append(fquant)

    adev_results.append(adevs)
    ire_results.append(ires)

adev_results = np.array(adev_results) # (Voltages, Taus)
ire_results = np.array(ire_results)   # (Voltages, Taus)
frequency_uncertainty_results = np.array(frequency_uncertainty_results)   # (Voltages, Taus)
quant_results = np.array(quant_results)   # (Voltages, Taus)
fquant_results = np.array(fquant_results)   # (Voltages, Taus)




# Plotting
plt.rcParams['font.family'] = 'serif'

# Plot 1: ADEV vs Vin
if 0:
    plt.figure(figsize=(6, 3))
    for j, tau in enumerate(selected_taus):
        plt.plot(voltages, adev_results[:, j], 'o-', label=f'$\\fs = {1/tau:g} Hz$', c=colours[j])
    plt.yscale('log')
    plt.xlabel('Input Voltage $V_{in}$ (V)')
    plt.ylabel('Allan Deviation $\sigma_y(\\tau)$')
    plt.title('Fractional Frequency Stability (ADEV) vs $V_{in}$')
    plt.grid(True, which="both", ls="-", alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig('figs/adev_vs_vin_only.svg')

# Plot 2: IRE (V) vs Vin
plt.figure(figsize=(6, 3))
for j, tau in enumerate(selected_taus):
    plt.plot(voltages*1e3, ire_results[:, j], 'o-', markersize=4,c=colours[j], zorder=len(selected_taus)-j)

for j, fs in enumerate(selected_fs):
    plt.plot(voltages*1e3, quant_results[:, j], c=colours[j],linestyle='--', label=r'$f_{s}$'+f'={fs:g} Hz')

plt.yscale('log')
plt.xlim(200,850)
plt.xlabel('Input Voltage (mV)')
plt.ylabel('Input Referred Error (V)')
plt.title('― From Allan Deviation using 1σ of inter-sample variance\n--- From frequency quantization error', fontsize=9, loc='left')
plt.grid(True, which="both", ls="-", alpha=0.3)
plt.legend()
plt.tight_layout()
plt.savefig('figs/ire_vs_vin.svg')

# Plot 3: Frequency Uncertainty (Hz) vs Vin
plt.figure(figsize=(6, 3))
for j, tau in enumerate(selected_taus):
    fs_eff = 1 / tau
    plt.plot(
        voltages * 1e3,
        frequency_uncertainty_results[:, j],
        'o-',
        c=colours[j],
        label=rf'$f_{{int}}={fs_eff:g}$ Hz'
    )
plt.plot(voltages*1e3, fquant_results[:, -1], c=colours[-1], linestyle='--', label=r'$f_{int}$'+f'{selected_fs[-1]:g} Hz')


plt.yscale('log')
plt.xlim(200, 850)
plt.xlabel('Input Voltage (mV)')
plt.ylabel('Frequency Uncertainty (Hz)')
plt.title('― From Allan Deviation using 1σ of inter-sample variance\n--- From frequency quantization error', fontsize=9, loc='left')
plt.grid(True, which="both", ls="-", alpha=0.3)
plt.legend()
plt.tight_layout()
plt.savefig('figs/frequency_uncertainty_vs_vin.svg')

# Output some diagnostic data
print("Voltages:", voltages)
print("KVCO values (Hz/V):", kvco)
# %%
