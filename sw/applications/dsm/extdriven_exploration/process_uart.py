#In[]:
# Init

%matplotlib inline
import numpy as np
import matplotlib.pyplot as plt
import re
import os



#In[]:
# Check the results
#############################################################

def finalize_block(header, xs, ys, rawname=False, plot=False, outpath="./"):

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

    ys = np.where(ys >= 0x800000, ys - 0x1000000, ys)

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
    fname = re.sub(r"[^\w\-_.]", "_", header) + ".csv"
    np.savetxt(outpath+fname, np.column_stack((xs, ys)), delimiter=', ', fmt='%1.12f\t%d')

#In[]
# Take from the uart
#############################################################

xs, ys = [], []
header = None

plot = False

from datetime import datetime
timestamp = datetime.now().strftime("%H_%M_%S_%d_%m_%y")

outpath = f"./tests_NABLE_{timestamp}/"

from pathlib import Path
Path(outpath).mkdir(parents=True, exist_ok=True)

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
        if not line or line.startswith("#"):
            continue

        if line.startswith("SES") or line.startswith("CIC"):
            finalize_block(header, xs, ys, plot=plot, outpath=outpath)
            header = line
            xs, ys = [], []
        else:
            try:
              a, b = line.split()
              xs.append(float(a))
              ys.append(float(b))
            except:
                print(line)

finalize_block(header, xs, ys,plot=plot, outpath=outpath )
