"""SVM training and inference with Timeseries feature and annotation interfaces.

Algorithms: Setz et al. (2010), doi:10.1109/TITB.2009.2036164;
Cortes & Vapnik (1995), doi:10.1007/BF00994018. SVC, scaling and imputation
are provided by scikit-learn (Pedregosa et al., 2011). See ../REFERENCES.md.
"""

from pathlib import Path

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from ..features.features import MIST_LINEAR_FEATURES
from ..tools.timeseries import Timeseries
from ..tools.ts_params import (
    TSP_CLASSES, TSP_DECISION, TSP_END_S, TSP_MAGNITUDE, TSP_PROCESSING, TSP_SUBJECT,
    TSP_THRESHOLD, TSP_UNITS, TSP_VALID, TSP_WINDOW_S,
)


def feature_matrix(features, names):
    """Construct the temporary numerical matrix required by scikit-learn."""
    if not names or len(set(names)) != len(names):
        raise ValueError("Feature names must be nonempty and unique.")
    first = features[names[0]]
    for name in names:
        feature = features[name]
        if not np.array_equal(first.time, feature.time) or feature.params[TSP_WINDOW_S] != first.params[TSP_WINDOW_S]:
            raise ValueError("All feature Timeseries must describe identical windows.")
    valid = first.params[TSP_VALID]
    if not np.array_equal(valid.time, first.time):
        raise ValueError("Feature validity timestamps do not match.")
    return np.column_stack([features[name].data for name in names]), valid.data == 1


def window_annotations(annotations, feature):
    """Label only windows fully covered by a single known class, without transitions."""
    labels = np.full(len(feature.time), -1.)
    if annotations is not None and len(annotations.time):
        width = feature.params[TSP_WINDOW_S]
        total = annotations.params.get(TSP_END_S, annotations.time[-1])
        for n, end in enumerate(feature.time):
            left = np.searchsorted(annotations.time, end - width, side="right") - 1
            if left < 0 or end > total:
                continue
            label = annotations.data[left]
            step_end = annotations.time[left + 1] if left + 1 < len(annotations.time) else total
            if label >= 0 and end <= step_end:
                labels[n] = label
    target = Timeseries("ann_" + feature.name, labels, feature.time)
    target.params.update({TSP_SUBJECT: feature.params[TSP_SUBJECT], TSP_WINDOW_S: feature.params[TSP_WINDOW_S],
                          TSP_CLASSES: annotations.params[TSP_CLASSES] if annotations is not None else {}})
    target.params[TSP_MAGNITUDE], target.params[TSP_UNITS] = "annotation", "class_index"
    return target


def _pipeline(kernel="linear", C=1., gamma="scale", degree=3):
    if kernel not in ("linear", "quadratic", "poly", "rbf"):
        raise ValueError("Unknown SVM kernel.")
    return make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                         SVC(kernel="poly" if kernel == "quadratic" else kernel,
                             degree=2 if kernel == "quadratic" else degree,
                             C=C, gamma=gamma, coef0=1., class_weight="balanced"))


def _training_rows(feature_sets, annotations, names):
    matrices, labels, groups = [], [], []
    for name, features in feature_sets.items():
        if name not in annotations:
            raise ValueError(f"Missing training annotations: ann_{name}")
        first = features[names[0]]
        target = window_annotations(annotations[name], first)
        x, usable = feature_matrix(features, names)
        selected = usable & (target.data >= 0)
        matrices.append(x[selected])
        labels.extend(target.params[TSP_CLASSES][int(value)] for value in target.data[selected])
        groups.extend([first.params[TSP_SUBJECT]] * np.count_nonzero(selected))
    if not labels or len(set(labels)) != 2:
        raise ValueError("Training requires usable annotated windows from two classes.")
    x = np.vstack(matrices)
    if not np.isfinite(x).any():
        raise ValueError("All selected features are missing in the training windows.")
    return x, np.array(labels), np.array(groups)


def train_svm(feature_sets, annotations, feature_names=MIST_LINEAR_FEATURES, **svm_options):
    """Fit an SVM; recordings/features/annotations remain Timeseries in the model.

    svm_options go to the SVM (kernel, C, gamma, degree), except
    decision_threshold: the margin a window must exceed to be predicted as
    stress. It is stored in the model and applied by classify_features, so
    inference and saved models all respect it; raising it reduces stress
    over-prediction at the cost of missed stress windows.
    """
    threshold = svm_options.pop("decision_threshold", 0.)
    if not np.isfinite(threshold):
        raise ValueError("decision_threshold must be finite.")
    available = next(iter(feature_sets.values()))
    unknown = [name for name in feature_names if name not in available]
    if unknown:
        raise ValueError(f"Unknown feature names {unknown}; available: {sorted(available)}")
    x, labels, subjects = _training_rows(feature_sets, annotations, feature_names)
    first = next(iter(feature_sets.values()))[feature_names[0]]
    processing = first.params[TSP_PROCESSING]
    for features in feature_sets.values():
        if features[feature_names[0]].params[TSP_PROCESSING] != processing:
            raise ValueError("Training recordings use different feature-processing parameters.")
    pipeline = _pipeline(**svm_options).fit(x, labels)
    return {"pipeline": pipeline, "feature_names": tuple(feature_names), "feature_options": processing,
            "decision_threshold": threshold,
            "classes": dict(enumerate(pipeline.classes_)), "subjects": sorted(set(subjects)),
            "training_features": feature_sets, "training_annotations": annotations,
            "algorithm_reference": "Setz et al. (2010), doi:10.1109/TITB.2009.2036164"}


def classify_features(model, features, decision_threshold=None):
    """Return a class-index Timeseries with the decision-margin Timeseries in params.

    decision_threshold overrides the threshold stored in the model (None keeps
    it), so the operating point can be tuned at inference time without
    refitting; the plotted margins are raw margin minus threshold, so their
    sign is the predicted class and zero is the operating point.
    """
    first = features[model["feature_names"][0]]
    if first.params[TSP_PROCESSING] != model["feature_options"]:
        raise ValueError("Inference feature settings differ from the fitted model.")
    threshold = model.get("decision_threshold", 0.) if decision_threshold is None else decision_threshold
    x, valid = feature_matrix(features, model["feature_names"])
    prediction, decision = np.full(len(first.time), np.nan), np.full(len(first.time), np.nan)
    if valid.any():
        lookup = {label: index for index, label in model["classes"].items()}
        classes = model["pipeline"].classes_
        # decision_threshold: the raw margin must exceed it to be classified as
        # the positive class (stress); it shifts the effective decision boundary.
        margins = model["pipeline"].decision_function(x[valid]) - threshold
        decision[valid] = margins
        prediction[valid] = [lookup[classes[1]] if value > 0 else lookup[classes[0]] for value in margins]
    params = {TSP_SUBJECT: first.params[TSP_SUBJECT], TSP_WINDOW_S: first.params[TSP_WINDOW_S],
              TSP_CLASSES: model["classes"], TSP_VALID: first.params[TSP_VALID],
              TSP_THRESHOLD: threshold}
    margins = Timeseries(first.name, decision, first.time)
    margins.params[TSP_MAGNITUDE], margins.params[TSP_UNITS] = "svm_margin", "1"
    params[TSP_DECISION] = margins
    result = Timeseries(first.name, prediction, first.time)
    result.params.update(params)
    result.params[TSP_MAGNITUDE], result.params[TSP_UNITS] = "classification", "class_index"
    return result


def _metrics(truth, predictions, classes):
    if not truth:
        return {"scored_windows": 0}
    return {"scored_windows": len(truth), "accuracy": float(accuracy_score(truth, predictions)),
            "balanced_accuracy": float(balanced_accuracy_score(truth, predictions)),
            "classes": list(classes), "confusion_matrix": confusion_matrix(truth, predictions, labels=classes).tolist()}


def _scored_labels(predictions, annotations):
    if annotations is None:
        return [], []
    target = window_annotations(annotations, predictions)
    selected = (target.data >= 0) & np.isfinite(predictions.data)
    truth = [target.params[TSP_CLASSES][int(value)] for value in target.data[selected]]
    predicted = [predictions.params[TSP_CLASSES][int(value)] for value in predictions.data[selected]]
    if set(truth) - set(predictions.params[TSP_CLASSES].values()):
        raise ValueError("Annotation classes differ from the fitted classification target.")
    return truth, predicted


def evaluate_test(model, predictions, annotations):
    """Score disjoint test subjects when annotations exist; unlabeled recordings are retained."""
    truth, predicted = [], []
    for name, series in predictions.items():
        if series.params[TSP_SUBJECT] in model["subjects"]:
            raise ValueError(f"Training/test subject overlap: {series.params[TSP_SUBJECT]}")
        actual, inferred = _scored_labels(series, annotations.get(name))
        truth.extend(actual)
        predicted.extend(inferred)
    return _metrics(truth, predicted, list(model["classes"].values()))


def leave_one_person_out(feature_sets, annotations, feature_names=MIST_LINEAR_FEATURES, **svm_options):
    """Subject-held-out evaluation with training-fold-only scaling and imputation.

    Accepts the same svm_options as train_svm, including decision_threshold.
    """
    threshold = svm_options.pop("decision_threshold", 0.)
    if not np.isfinite(threshold):
        raise ValueError("decision_threshold must be finite.")
    x, y, subjects = _training_rows(feature_sets, annotations, feature_names)
    if len(set(subjects)) < 2:
        raise ValueError("LOPO requires at least two subjects.")
    predictions = np.empty(len(y), dtype=object)
    for train, test in LeaveOneGroupOut().split(x, y, subjects):
        if len(set(y[train])) != 2:
            raise ValueError("A LOPO training fold contains only one class.")
        pipeline = _pipeline(**svm_options).fit(x[train], y[train])
        margins = pipeline.decision_function(x[test]) - threshold
        predictions[test] = np.where(margins > 0, pipeline.classes_[1], pipeline.classes_[0])
    return _metrics(y.tolist(), predictions.tolist(), sorted(set(y)))


def decision_threshold_sweep(feature_sets, annotations, feature_names=MIST_LINEAR_FEATURES,
                             thresholds=(0., .5, 1., 1.5, 2.), **svm_options):
    """Leave-one-person-out metrics for each candidate decision_threshold.

    The other svm_options (kernel, C, ...) are shared by every fit, so the
    sweep isolates the effect of the threshold alone. Returns
    {threshold: metrics}; since threshold shifts trade missed stress against
    false alarms, compare balanced_accuracy rather than accuracy.
    """
    svm_options.pop("decision_threshold", None)
    return {threshold: leave_one_person_out(feature_sets, annotations, feature_names=feature_names,
                                            decision_threshold=threshold, **svm_options)
            for threshold in thresholds}


def save_model(model, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_model(path):
    model = joblib.load(path)
    if not isinstance(model, dict) or "feature_options" not in model:
        raise ValueError("The model must be trained with the Timeseries pipeline.")
    return model
