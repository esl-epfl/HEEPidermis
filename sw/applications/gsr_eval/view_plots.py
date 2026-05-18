#In[]:
%matplotlib inline

import numpy as np
import matplotlib.pyplot as plt

# one integer per line
data = np.loadtxt("conductance.txt", dtype=int)
fs_Hz = 200
time_s = np.arange(len(data))/fs_Hz

conductance_nS = data/1e3

#In[]:

plt.figure(figsize=(8, 3))
plt.plot(time_s/60, conductance_nS)
plt.xlabel("Time (mins)")
plt.ylabel("Conductance (nS)")
plt.grid(True)
plt.tight_layout()
plt.show()


plt.figure(figsize=(8, 3))
plt.plot(time_s, conductance_nS)
plt.xlabel("Time (s)")
plt.ylabel("Conductance (nS)")
plt.xlim(500e3/fs_Hz, 504e3/fs_Hz)
plt.ylim(4500, 6500)
plt.grid(True)
plt.tight_layout()
plt.show()