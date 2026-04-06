
#In[]:
# Test the new transfer function

import numpy as np
import matplotlib.pyplot as plt
from scipy import signal

def plot_transfer_function(wg, ww, N, fs=1e6):
    # Calculate coefficients for a single stage
    # H_base(z) = (b0 + b1*z^-1) / (a0 + a1*z^-1)
    b_single = [0, 2**(wg - ww)]
    a_single = [1, -(1 - 2**(-ww))]

    # Calculate frequency response for one stage
    w, h_single = signal.freqz(b_single, a_single, worN=8000)

    b_following = [0, 1]
    a_following = [1, -(1 - 2**(-ww))]
    w, h_following = signal.freqz(b_following, a_following, worN=8000)

    # Cascade N stages: Magnitude is multiplied, Phase is added
    h_total = h_single*h_following**(N-1)

    # Frequency axis in Hz
    freq_hz = w * fs / (2 * np.pi)
    return freq_hz, 20 * np.log10(np.abs(h_total) + 1e-12)

wg  = [16, 16, 16]
ww  = [5,  4 , 5 ]
N   = [5,  5 , 6 ]
C   = ['g', 'b', 'r']

fig, ax1 = plt.subplots(figsize=(10, 6))

for g, w, n, c in zip(wg, ww, N, C):
    freq_hz, ampl_db = plot_transfer_function(wg=g, ww=w, N=n)
    ax1.plot(freq_hz, ampl_db, 'b', label=f'N={n}, $w_g$={g}, $w_w$={w}', color=c)

ax1.set_ylabel('Amplitude [dB]')
ax1.set_xlabel('Frequency [Hz]')
ax1.grid(True, which='both', linestyle='--', alpha=0.5)
ax1.set_xscale('log')
plt.tight_layout()
plt.show()



#In[]:


# import numpy as np
# import matplotlib.pyplot as plt
# from scipy import signal


# plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})

# def get_transfer_function(W_g, W_w):
#     alpha = 2**(-W_w)
#     gain = 2**(W_g - W_w)
#     b = [gain]
#     a = [1, -(1 - alpha)]
#     return b, a

# W_g = 16
# W_w_list = [1, 2, 3, 4, 5, 6]
# f_dsm = 1e6

# plt.figure(figsize=(6, 3))

# for i,W_w in enumerate(W_w_list):
#     b, a = get_transfer_function(W_g, W_w)
#     # Using more points for better log-scale resolution at low frequencies
#     w, h = signal.freqz(b, a, worN=np.logspace(-4, np.log10(np.pi), 2000))
#     freq = w / (2 * np.pi) * f_dsm
#     plt.semilogx(freq, 20 * np.log10(np.abs(h)), label=f'{W_w}',color='k', alpha=1/(i+1))

# # plt.title(f'Magnitude Response (Log Scale) for Wg={W_g}')
# plt.xlabel(r'$f_\text{in}$ (Hz)')
# plt.ylabel('Magnitude (dB)')
# plt.grid(True, which='both', linestyle='--', alpha=0.6)
# plt.axhline(96.3 - 3, color='red', linestyle=':', alpha=0.7, label='-3dB')
# plt.xlim(10, 1e6)
# plt.legend(title='Ww', loc='lower left')
# plt.ylim(60, 100)
# plt.yticks([60,80,100])
# plt.tight_layout()
# plt.savefig('figs/filter_theoretical_transfer.png',dpi=400)


# #In[]:

# def get_stage_tf(W_g, W_w):
#     alpha = 2**(-W_w)
#     gain = 2**(W_g - W_w)
#     return [gain], [1, -(1 - alpha)]

# W_g = 16
# W_w = 5
# f_dsm = 1e6
# num_stages = [1, 2, 3, 4, 5, 6]

# plt.figure(figsize=(6,3))

# # Set grayscale property cycle
# colors = plt.cm.gray(np.linspace(0, 0.7, len(num_stages)))

# for i, N in enumerate(num_stages):
#     # Stage 1 has Wg=16, subsequent have Wg=0
#     b_total, a_total = get_stage_tf(W_g, W_w)

#     for _ in range(N - 1):
#         b_next, a_next = get_stage_tf(0, W_w)
#         b_total = np.convolve(b_total, b_next)
#         a_total = np.convolve(a_total, a_next)

#     w, h = signal.freqz(b_total, a_total, worN=np.logspace(-4, np.log10(np.pi), 2000))
#     freq = w / (2 * np.pi) * f_dsm
#     plt.semilogx(freq, 20 * np.log10(np.abs(h)), label=f'{N}', color=colors[i])

# plt.axhline(96.3 - 3, color='red', linestyle=':', alpha=0.7, label='-3dB')
# # plt.title(f'Cascaded Filter Response (Ww=5, Wg_first=16, Wg_others=0)')
# plt.xlabel(r'$f_{in}$ (Hz)')
# plt.ylabel('Magnitude (dB)')
# plt.grid(True, which='both', linestyle='--', alpha=0.6)
# plt.ylim(0, 100)
# plt.xlim(10, 1e6)
# plt.legend(title='Stages (N)', loc='lower left')
# plt.tight_layout()
# plt.savefig('figs/filter_theoretical_transfer_cascaded.png', dpi=400)

