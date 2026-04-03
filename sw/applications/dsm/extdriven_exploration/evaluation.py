#In[]:
# Init

%matplotlib widget
import numpy as np
import matplotlib.pyplot as plt
import re
import os



#In[]:
# Check the results
#############################################################

def finalize_block(header, xs, ys, rawname=False, plot=False):

      # Define the variable names you want to extract
    variables = ['fclk', 'Wg', 'Ww', 'DF', 'AS']

    # Create a dictionary to store the results
    extracted_data = {}

    for var in variables:
        # This regex looks for the variable name followed by a colon and digits
        match = re.search(fr"{var}:(\d+)", header)
        if match:
            extracted_data[var] = int(match.group(1))


    xs = np.arange(len(ys))
    ys = np.array(ys)

    # take second half
    ys2 = ys[len(ys)//3:]

    # remove DC offset
    ymin = ys2.min()
    ymax = ys2.max()
    ys2 = ys2 - (ymin + (ymax - ymin)/2)

    # find 3 zero crossings with linear interpolation
    zc = []
    for i in range(1, len(ys2)):
        if ys2[i-1] <= 0 and ys2[i] > 0 or ys2[i-1] >= 0 and ys2[i] < 0:
            frac = -ys2[i-1] / (ys2[i] - ys2[i-1])
            zc.append((i-1) + frac)
            if len(zc) == 3:
                break

    # sampling frequency
    fs_Hz = extracted_data['fclk']*1e3/extracted_data['DF']
    xs = xs/ fs_Hz

    if plot:
        plt.figure(figsize=(7,2))
        plt.title(header + f"  | {fs_Hz/1e3:1.2f} kHz")
        plt.plot(xs, ys, marker=".")
        plt.xlabel("x")
        plt.ylabel("y")
        plt.show()

    # save
    if rawname:
        fname = header
    else:
        fname = "test_with_N_ABLE/"+re.sub(r"[^\w\-_.]", "_", header) + ".csv"
    np.savetxt(fname, np.column_stack((xs, ys)), delimiter=', ')

#In[]
# Take from the uart
#############################################################

xs, ys = [], []
header = None

plot = False

with open("../../../../uart.log") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        if line.startswith("SES") or line.startswith("CIC"):
             header = line
             break

    for line in f:
        line = line.strip()
        if not line:
            continue

        if line.startswith("SES") or line.startswith("CIC"):
            finalize_block(header, xs, ys, plot=plot)
            header = line
            xs, ys = [], []
        else:
            try:
              a, b = line.split()
              xs.append(float(a))
              ys.append(float(b))
            except:
                print(line)

finalize_block(header, xs, ys,plot=plot )

#In[]:
# Correct wrong axis
#############################################################

# import glob

# f_sig = 3e6 / 1024

# for fname in glob.glob("out_coarse_144/*.csv"):
#     data = np.loadtxt(fname, delimiter=",")
#     xs = data[:, 0]          # sample index
#     ys = data[:, 1]

#     finalize_block( fname, xs, ys, True )

#In[]
# Coarse exploration heatmaps
#############################################################

from collections import defaultdict

# parse
pat = re.compile(
    r"metric=([\d.]+).*Wg_(\d+)_Ww_(\d+)_DF_(\d+)_AS_(\d+)"
)

data = defaultdict(list)

with open("out_coarse_144/results.txt") as f:
    for line in f:
        m = pat.search(line)
        if not m:
            continue
        metric, Wg, Ww, DF, AS = m.groups()
        metric = float(metric)
        Wg, Ww, DF, AS = map(int, (Wg, Ww, DF, AS))
        data[(AS, DF)].append((Wg, Ww, metric))

# unique axes
all_Wg = sorted({v[0] for vals in data.values() for v in vals})
all_Ww = sorted({v[1] for vals in data.values() for v in vals})

wg_idx = {v: i for i, v in enumerate(all_Wg)}
ww_idx = {v: i for i, v in enumerate(all_Ww)}

AS_vals = sorted({k[0] for k in data})
DF_vals = sorted({k[1] for k in data})

fig, axes = plt.subplots(len(AS_vals), len(DF_vals),
                         figsize=(2*len(DF_vals), 2*len(AS_vals)),
                         squeeze=True)

vmin, vmax = 0, 50

for i, AS in enumerate(AS_vals):
    for j, DF in enumerate(DF_vals):
        Z = np.full((len(all_Ww), len(all_Wg)), np.nan)
        for Wg, Ww, metric in data.get((AS, DF), []):
            Z[ww_idx[Ww], wg_idx[Wg]] = metric

        ax = axes[i, j]
        im = ax.imshow(
            Z, origin="lower", aspect="auto",
            cmap="RdYlGn", vmin=vmin, vmax=vmax
        )
        ax.set_title(f"AS={AS}, DF={DF}")
        ax.set_xticks(range(len(all_Wg)))
        ax.set_yticks(range(len(all_Ww)))
        ax.set_xticklabels(all_Wg)
        ax.set_yticklabels(all_Ww)
        ax.set_xlabel("Wg")
        ax.set_ylabel("Ww")

# single shared colorbar
# cax = fig.add_axes([0.97, 0.15, 0.02, 0.7])  # [left, bottom, width, height]
# cbar = fig.colorbar(im, cax=cax)
# cbar.set_label("metric")

plt.tight_layout()
plt.show()


#In[]
# Finer exploration heatmap
#############################################################
%matplotlib inline

from collections import defaultdict

plt.rcParams["font.family"] = "serif"

# parse
pat = re.compile(
    r"metric=([\d.]+).*Wg_(\d+)_Ww_(\d+)_DF_(\d+)_AS_(\d+)"
)

data = defaultdict(list)

with open("out_finer_42/results.txt") as f:
    for line in f:
        m = pat.search(line)
        if not m:
            continue
        metric, Wg, Ww, DF, AS = m.groups()
        metric = float(metric)
        Wg, Ww, DF, AS = map(int, (Wg, Ww, DF, AS))
        data[(AS, DF)].append((Wg, Ww, metric))

# unique axes
all_Wg = sorted({v[0] for vals in data.values() for v in vals})
all_Ww = sorted({v[1] for vals in data.values() for v in vals})

wg_idx = {v: i for i, v in enumerate(all_Wg)}
ww_idx = {v: i for i, v in enumerate(all_Ww)}

# build AS x DF matrix
AS_vals = sorted({k[0] for k in data})
DF_vals = sorted({k[1] for k in data})

as_idx = {v: i for i, v in enumerate(AS_vals)}
df_idx = {v: i for i, v in enumerate(DF_vals)}

Z = np.full((len(AS_vals), len(DF_vals)), np.nan)

for (AS, DF), vals in data.items():
    metrics = [m for _, _, m in vals]
    Z[as_idx[AS], df_idx[DF]] = np.mean(metrics)  # or metrics[0]

plt.figure(figsize=(3, 3))
im = plt.imshow(
    Z, origin="lower", aspect="auto",
    cmap="RdYlGn", vmin=0, vmax=50
)
plt.xticks(range(len(DF_vals)), DF_vals)
plt.yticks(range(len(AS_vals)), range(1,len(AS_vals)+1))
plt.xlabel("Decimation Factor (DF)")
plt.ylabel("Active stages (AS)")
plt.title("SNDR for Wg=16, Ww=6")

cax = plt.gcf().add_axes([1, 0.15, 0.03, 0.7])
plt.colorbar(im, cax=cax, label="SNDR")


plt.tight_layout()
plt.show()