# In[1]:
# Configuration: training/testing switches, sample plots and processing options.
# Optional notebook backend:
%matplotlib widget
from pathlib import Path
import sys
import json
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent if "__file__" in globals() else Path("sw/classification").resolve()
sys.path.insert(0, str(ROOT.parents[1]))
from sw.classification.tools.timeseries import Timeseries
from sw.classification.datasets.io import load_dataset
from sw.classification.conversion.data_to_conductance_S import data_to_conductance_S, normalized_conductance
from sw.classification.features.conductance_S_to_features import (
    conductance_S_to_features, extraction_options,
)
from sw.classification.features.features import *
from sw.classification.classification.features_to_classification import (
    train_svm, classify_features, evaluate_test, leave_one_person_out,
    load_model, save_model,
)
from sw.classification.plots import (
    plot_classification, plot_feature_trajectory, plot_recording,
)

TRAIN               = False
TEST                = False
VALIDATION          = ROOT / "datasets/test/sub_26.pkl"  # None disables single-recording inference.
FEATURE_OPTIONS     = dict(sample_rate_Hz=4., window_s=60., step_s=15.)
# Features to extract and to feed the SVM: a list of the string constants
# defined in features/features.py. Add or remove names to taste
FEATURES            = FEATURE_SELECTION_JUAN
SVM_OPTIONS         = dict(kernel="linear", decision_threshold=3)
# kernel: linear, quadratic, poly or rbf. decision_threshold is the margin a
# window must exceed to be predicted as stress; raise it (e.g. 0.5, 1, ...)
# when the SVM over-predicts stress. Also C, gamma, degree.
THRESHOLD           = SVM_OPTIONS.get("decision_threshold", 0.)
# The threshold is applied at inference from this switch (classify_features
# override), so the operating point can be tuned without retraining — set
# TRAIN = False and re-run. Kernel, C/gamma/degree and FEATURES changes, in
# contrast, only take effect after retraining (TRAIN = True).
COMPARE_KERNELS     = True                   # Print leave-one-person-out scores for every kernel.
# Feature-extraction input: when True, the conductance is first normalized per
# recording (mean + 3 sigma amplitude, clipped to 0-1) and the features are
# computed from that, so absolute set-up levels disappear. Amplitude-like
# features then lose their physical meaning (e.g. level_mean_S) — choose
# FEATURES to match. Inference follows the model, not this switch.
NORMALIZATION       = True
# The two FEATURES drawn in the SVM feature map (svm_trajectory.png).
FEATURE_MAP         = (PEAK_RATE_PER_MIN, PEAK_HEIGHT_Q25_S)
PLOT_NORMALIZED     = True                # Conductance row normalized (mean + 3 sigma, clipped to 0-1).
N_PLOT_SAMPLES      = 4                     # Recordings per dataset shown in the sample plots.
PLOT_FEATURES       = tuple(FEATURES)       # Feature Timeseries drawn in the sample and feature-space plots.
PLOT_TIME_RANGE     = None                 # (start_s, end_s) per recording; None shows everything.
OUTPUT              = ROOT / "outputs"
MODEL               = OUTPUT / "svm.joblib"
OUTPUT.mkdir(exist_ok=True)
if len(set(FEATURE_MAP)) != 2 or not set(FEATURE_MAP) <= set(FEATURES):
    raise ValueError("FEATURE_MAP must select exactly two names from FEATURES.")


def conductance_input(normalize, conductance):
    """Feature-extraction input: normalized conductance when selected."""
    return normalized_conductance(conductance) if normalize else conductance

# In[2]:
# Train from Timeseries recordings and paired ann_<name> annotations.
if TRAIN:
    data, annotations = load_dataset(ROOT / "datasets/training")
    features = {name: conductance_S_to_features(conductance_input(NORMALIZATION, data_to_conductance_S(ts)),
                                                feature_names=FEATURES, **FEATURE_OPTIONS)
                for name, ts in data.items()}
    model = train_svm(features, annotations, feature_names=FEATURES, **SVM_OPTIONS)
    save_model(model, MODEL)
    if COMPARE_KERNELS:
        # Leave-one-person-out behaviour of every available kernel, with the
        # same feature selection and threshold as the configured model.
        for kernel in ("linear", "quadratic", "poly", "rbf"):
            scores = leave_one_person_out(features, annotations, feature_names=FEATURES,
                                          **{**SVM_OPTIONS, "kernel": kernel})
            print(f"{kernel:>10}: accuracy={scores.get('accuracy', float('nan')):.3f} "
                  f"balanced={scores.get('balanced_accuracy', float('nan')):.3f} "
                  f"windows={scores.get('scored_windows', 0)}")
elif TEST or VALIDATION is not None:
    model = load_model(MODEL)

# In[3]:
# Test on separate subjects; annotations are optional and used only for scoring.
if TEST:
    data, annotations   = load_dataset(ROOT / "datasets/test")
    # Inference follows the model's input representation (input_units).
    normalized_input    = model["feature_options"].get("input_units") == "normalized"
    signals             = {name: conductance_input(normalized_input, data_to_conductance_S(ts))
                           for name, ts in data.items()}
    features            = {name: conductance_S_to_features(signals[name],
                                                           **extraction_options(model["feature_options"]))
                           for name in data}
    predictions         = {name: classify_features(model, features[name], THRESHOLD) for name in data}
    scores              = evaluate_test(model, predictions, annotations)
    (OUTPUT / "metrics.json").write_text(json.dumps(scores, indent=2) + "\n")
    (OUTPUT / "test").mkdir(exist_ok=True)
    for name, prediction in predictions.items():
        prediction.dump(f"{OUTPUT / 'test'}/")
        figure              = plot_classification(signals[name], prediction, annotations.get(name),
                                                  PLOT_TIME_RANGE)
        figure.savefig(OUTPUT / "test" / f"{name}.png", dpi=160)
    print(scores)
    plt.show()

# In[4]:
# Sample plots: one figure per selected recording, with rows for the
# conductance (PLOT_NORMALIZED or microsiemens) and each PLOT_FEATURES
# feature, annotations across all rows. Test recordings additionally show
# their stress predictions as blue bands. Takes the first recordings (by
# name) of the training and test datasets.
if "model" not in globals():
    raise ValueError("Enable TRAIN or TEST (or set VALIDATION) to fit or load a model first.")
(OUTPUT / "samples").mkdir(exist_ok=True)
normalized_input    = model["feature_options"].get("input_units") == "normalized"
for directory in ("datasets/training", "datasets/test"):
    data, annotations   = load_dataset(ROOT / directory)
    for name in sorted(data)[:N_PLOT_SAMPLES]:
        conductance     = data_to_conductance_S(data[name])
        features        = conductance_S_to_features(conductance_input(normalized_input, conductance),
                                                    **extraction_options(model["feature_options"]))
        absent          = [key for key in PLOT_FEATURES if key not in features]
        if absent:
            raise ValueError(f"Features not available for plotting: {absent}")
        prediction      = None
        if Path(directory).name != "training":
            prediction  = classify_features(model, features, THRESHOLD)
        figure          = plot_recording(data[name], conductance, features, annotations.get(name),
                                         PLOT_FEATURES, PLOT_TIME_RANGE, prediction, PLOT_NORMALIZED)
        figure.savefig(OUTPUT / "samples" / f"{Path(directory).name}_{name}.png", dpi=160)
print(f"Sample plots saved to {OUTPUT / 'samples'}")
plt.show()

# In[5]:
# Validate a specific recording through the same conversion, features and fitted SVM.
%matplotlib widget

if VALIDATION is not None and not Path(VALIDATION).exists():
    print(f"Validation recording not found: {VALIDATION}\n"
          "Prepare it with: python -m sw.classification.datasets.raw_to_timeseries gui <csv> --name <subject>")
    VALIDATION = None
if VALIDATION is not None:
    data                = Timeseries.load(VALIDATION)
    # Inference follows the model's input representation (input_units).
    normalized_input    = model["feature_options"].get("input_units") == "normalized"
    conductance         = data_to_conductance_S(data)
    features            = conductance_S_to_features(conductance_input(normalized_input, conductance),
                                                    **extraction_options(model["feature_options"]))
    prediction          = classify_features(model, features, THRESHOLD)
    annotation_path     = VALIDATION.with_name("ann_" + VALIDATION.name)
    annotations         = Timeseries.load(annotation_path) if annotation_path.exists() else None
    # Same view as the train/test sample plots: conductance, features,
    # ground truth (when prepared) and the prediction bands. The feature-map
    # trajectory line is colored by the ground-truth annotation when present.
    plot_recording(data, conductance, features, annotations, PLOT_FEATURES, PLOT_TIME_RANGE,
                   prediction, PLOT_NORMALIZED).savefig(OUTPUT / "validation_recording.png", dpi=160)
    plot_feature_trajectory(model, features, FEATURE_MAP, PLOT_TIME_RANGE, THRESHOLD,
                            annotations).savefig(OUTPUT / "svm_trajectory.png", dpi=160)
    (OUTPUT / "validation").mkdir(exist_ok=True)
    conductance.dump(f"{OUTPUT / 'validation'}/", "conductance")
    prediction.dump(f"{OUTPUT / 'validation'}/", "prediction")
    for name, feature in features.items():
        feature.dump(f"{OUTPUT / 'validation'}/", name)
    plt.show()
