# In[1]:
# Inspection of prepared Timeseries recordings with their annotations.
# Point TARGET at a dataset folder (training, testing, validation) or at a
# single <name>.pkl; its ann_<name>.pkl is picked up from the same folder.
# Every recording is plotted in its entirety, annotations across all rows.
%matplotlib widget
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent if "__file__" in globals() else Path("sw/classification").resolve()
sys.path.insert(0, str(ROOT.parents[1]))
import matplotlib.pyplot as plt

from sw.classification.tools.timeseries import Timeseries
from sw.classification.datasets.io import load_dataset
from sw.classification.conversion.data_to_conductance_S import data_to_conductance_S
from sw.classification.features.conductance_S_to_features import conductance_S_to_features
from sw.classification.features.features import *
from sw.classification.plots import plot_recording

# TARGET          = ROOT / "datasets/training"  # Folder or a single Timeseries pickle.
# TARGET          = ROOT / "datasets/training"  # Folder or a single Timeseries pickle.
TARGET          = ROOT / "datasets/validation"  # Folder or a single Timeseries pickle.
PLOT_FEATURES   = FEATURE_NAMES                               # Feature rows to judge; () shows only conductance.
PLOT_NORMALIZED = False                         # Conductance row normalized (mean + 3 sigma, clipped to 0-1).
FEATURE_OPTIONS = dict(sample_rate_Hz=4., window_s=60., step_s=15.)

# In[2]:
# Load either the single recording or every recording of the folder.
target = Path(TARGET)
if target.suffix == ".pkl":
    data            = {target.stem: Timeseries.load(target)}
    annotations     = {}
    annotation_path = target.with_name("ann_" + target.name)
    if annotation_path.exists():
        annotations[target.stem] = Timeseries.load(annotation_path)
else:
    data, annotations = load_dataset(target)
for name in sorted(data):
    ts   = data[name]
    span = f"{ts.time[0]:.0f}-{ts.time[-1]:.0f} s" if len(ts.time) else "empty"
    ann  = annotations.get(name)
    tags = f"; {len(ann.data)} annotation events" if ann is not None else ""
    print(f"{name}: {len(ts.data)} samples, {span}{tags}")

# In[3]:
# One figure per recording, untrimmed, with the annotation bands on every row.
for name in sorted(data):
    conductance = data_to_conductance_S(data[name])
    features    = conductance_S_to_features(conductance, **FEATURE_OPTIONS) if PLOT_FEATURES else {}
    absent      = [key for key in PLOT_FEATURES if key not in features]
    if absent:
        raise ValueError(f"Features not available for plotting: {absent}")
    plot_recording(data[name], conductance, features, annotations.get(name), PLOT_FEATURES, None,
                   normalized=PLOT_NORMALIZED)
plt.show()
