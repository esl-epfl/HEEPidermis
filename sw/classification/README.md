# EDA classification: conductance → Setz features → SVM

A modular pipeline for electroderal-activity classification, implementing the
16 EDA features and the linear-SVM model of:

> C. Setz, B. Arnrich, J. Schumm, R. La Marca, G. Tröster and U. Ehlert,
> "Discriminating Stress From Cognitive Load Using a Wearable EDA Device,"
> *IEEE Transactions on Information Technology in Biomedicine*, 14(2), 2010,
> [doi:10.1109/TITB.2009.2036164](https://doi.org/10.1109/TITB.2009.2036164)

Everything that flows through the pipeline is a `Timeseries` instance (the
class bundled in `tools/timeseries.py`): the raw recording, the converted
conductance, every extracted feature, the annotations, the decomposition and
the classification output. Magnitudes and units travel in the `params`
dictionary (`tools/ts_params.py`), so a stage can verify what it receives.

## Pipeline

`main.py` contains five `# In[]:` cells:

1. **Configuration** — `TRAIN` and `TEST` are independent switches; both can
   be `True`. `VALIDATION` optionally points at one recording for inference.
   Plotting options: `N_PLOT_SAMPLES`, `PLOT_FEATURES` and `PLOT_TIME_RANGE`.
2. **Training** — loads `datasets/training`, converts every recording to
   conductance in siemens, extracts features and fits the SVM.
3. **Testing** — loads `datasets/test`, runs the same stages with the fitted
   model's settings, and scores against annotations when present. Each test
   recording also gets a figure: conductance, the per-window prediction and
   the ground-truth stress spans.
4. **Sample plots** — one figure per recording for the first `N_PLOT_SAMPLES`
   recordings (by name) of the training and testing datasets. Each figure has
   the conductance row (tall) plus one short scatter row per selected
   `PLOT_FEATURES` feature, with the annotation spans drawn as background
   bands across all rows.
5. **Validation** — one recording goes through conversion, feature extraction
   and the fitted SVM; stage-by-stage and feature-space figures are saved.

Each stage lives in its own module and has a single Timeseries interface:

| Stage | Module | Input | Output |
| --- | --- | --- | --- |
| Conversion | `conversion/data_to_conductance_S.py` | Timeseries, any magnitude/units declared in `params` | Timeseries, conductance in S |
| Features | `features/conductance_S_to_features.py` | Timeseries, conductance in S | dictionary `{name: Timeseries}`, one entry per feature |
| Classification | `classification/features_to_classification.py` | feature dictionary + `ann_<name>` annotations | dictionary of Timeseries with class indices |

Annotations are Timeseries objects named `ann_<recording name>`. Their data
hold class indices per timestamp (`-1` means unannotated) and the
`Class index` parameter maps indices to class names. The agreed classification
target is `stress` versus `not stress` (everything else): every annotated
non-stress condition is class `not stress`. An annotation timestamp starts a
class that holds until the next timestamp; interval end markers restore `-1`,
giving half-open intervals.

### Plotting options

- `PLOT_FEATURES` selects which feature Timeseries are drawn in the per-recording
  sample plots and in the feature-space figure. Any extracted feature can be
  plotted per recording; the feature-space figure is restricted to the model's
  own features. With exactly two selected features it shows the direct feature
  plane (omitted model features fixed to their training medians); with more, a
  training-only PCA projection. The second panel always shows the actual
  full-dimensional SVM decision per window.
- `PLOT_TIME_RANGE = (start_s, end_s)` crops every figure to that time span of
  the recording, so startup and shutdown artifacts can be excluded from the
  plots. `None` shows the complete recordings.

To replace a stage, keep its signature: a function that consumes and produces
the Timeseries structures above. Feature names are looked up by name, never
by column position. Feature extraction accepts keyword options
(`sample_rate_Hz`, `window_s`, `step_s`, filtering and peak-detector
parameters, see `DEFAULTS` in `conductance_S_to_features.py`).

The feature names themselves are defined once in `features/features.py`: each
possible feature is a string constant there, `conductance_S_to_features`
labels its outputs with those constants, and `main.py` builds the `FEATURES`
list it passes to the extractor (and to the SVM) from those constants. The
extractor linearly interpolates invalid conductance samples between their
nearest valid neighbours (`interpolate_gaps`), so the features are continuous
across short artifacts; only large time gaps and segment boundaries still
split the processing. The SVM kernel and hyperparameters are set through
`SVM_OPTIONS` in `main.py` (`linear`, `quadratic`, `poly` or `rbf`), and
`COMPARE_KERNELS` prints a leave-one-person-out comparison of all of them.

`FEATURE_OPTIONS` also accepts `relative_windows`: the amplitude features
(`level_*`, `peak_height_*`) are then divided by the mean conductance of
their own window, so a 25% rise reads as 1.25 independently of the baseline
SCL and of the measurement set-up (`level_mean_S` becomes exactly 1 and the
units become relative); the rate features stay absolute. This is usually the
right choice when recordings from different set-ups must be comparable, and
it makes the whole-recording input `NORMALIZATION` largely redundant. Caveat:
peak detection keeps the absolute `min_peak_height_S` floor, so at very small
absolute levels the detected peak set (and the peak-rate/height features) can
differ from a scaled copy of the signal.

Per-recording input normalization is optional (`NORMALIZATION` in `main.py`):
when enabled, the conductance is first scaled by its mean + 3 sigma amplitude
— never by the maximum, which single artifacts would dominate — and clipped
to 0–1, and the features are computed from that normalized signal. This
removes the absolute set-up level and scale, so recordings become portable
between set-ups; amplitude-like features then lose their physical meaning
(e.g. `level_mean_S` becomes near-constant), so `FEATURES` must be chosen to
match. The input representation is recorded in the model (`input_units`), so
inference always follows the model, not the switch, and mismatches are
rejected. For display, the conductance row can be shown in the same
normalized units via `PLOT_NORMALIZED` in `main.py` and `viewer.py`. The SVM
feature map (`svm_trajectory.png`) shows the plane of the two features
selected in `FEATURE_MAP`, labeled with their feature names.

`SVM_OPTIONS` also accepts `decision_threshold`: the SVM margin a window must
exceed to be predicted as stress (default 0). Raising it reduces stress
over-prediction at the cost of missed stress windows; the
leave-one-person-out kernel comparison uses the same threshold, and
`decision_threshold_sweep` (same module) reports the threshold trade-off as a
table. The threshold is also an inference-time parameter
(`classify_features(model, features, decision_threshold)`): `main.py` passes
the configured threshold (`THRESHOLD`) to every inference cell, so the
operating point can be tuned without retraining — only kernel, C/gamma/degree
and `FEATURES` changes require `TRAIN = True`. The sample plots show the
per-window raw margin as the prediction confidence: its magnitude is the
distance to the decision boundary, and the drawn threshold line is the
operating point (windows above it are stress; dotted line: the fitted
margin-zero boundary). The feature map (`svm_trajectory.png`) shows one panel
per feature pair — the selected pair plus, for three-feature models, the pair
sharing the first selected feature with the remaining one — with the effective
boundary at the applied threshold (solid) against the raw margin-zero boundary
the SVM was fitted to (dotted). Training windows and support vectors are open
circles (no filling) in their class colors, the support vectors fainter and
larger; the recording trajectory is drawn in the plasma colormap by window time
with red square markers on the windows annotated as stress — a square on the
decision side opposite to the stress region is a missed stress window. Overlapping windows
(60 s window, 15 s step) are merged into disjoint predicted regions in the
plots, so a time stretch is never shaded twice. The recording view
(`plot_recording`, used for the training/test samples and for the validation
recording) shades annotated stress in red and, outside the training set,
stress-predicted windows in blue.

## Data preparation

`datasets/raw/` stores original downloads; `datasets/training`, `datasets/test`
and `datasets/validation` store only Timeseries pickles. The downloaders fetch
the PhysioNet wearable-device subset and the CASE dataset (figshare); the
converters turn raw CSVs into subject-named Timeseries files. Dataset
provenance, license and labeling: `datasets/README.md`. Algorithm and library
references: `REFERENCES.md`.

```sh
python -m sw.classification.datasets.download_physionet
python -m sw.classification.datasets.raw_to_timeseries physionet
python -m sw.classification.datasets.download_case
python -m sw.classification.datasets.raw_to_timeseries case [--arousal-threshold 5.0]
python -m sw.classification.datasets.raw_to_timeseries gui <recording.csv> --name <subject>
```

Model persistence: `save_model`/`load_model` in
`classification/features_to_classification.py` store the fitted pipeline
together with the Timeseries feature dictionaries used for training, so the
feature-space plot and the processing settings survive a reload.

## Inspecting recordings

`viewer.py` plots prepared recordings together with their annotations, in the
same notebook-cell format as `main.py`. Set `TARGET` to a dataset folder
(`datasets/training`, `datasets/test`, `datasets/validation`) or to a single
`<name>.pkl`; every selected recording is drawn in its entirety with the
annotation bands across all rows. Optionally set `PLOT_FEATURES` (for example
`MIST_LINEAR_FEATURES`) to add one row per extracted feature.

## Checks

```sh
python -m unittest discover -s sw/classification/tests -v
MPLBACKEND=Agg python sw/classification/main.py
```

The tests cover unit conversion, the feature statistics of the paper, gap
handling, annotation semantics, training-fold isolation in leave-one-person-out
and model persistence.
