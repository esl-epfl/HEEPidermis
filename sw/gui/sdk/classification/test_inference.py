"""End-to-end checks for the Classification device's live inference (inference.py).

Trains a small model exactly like the pipeline tests do, then runs
``compute_outputs`` the way the GUI worker does: a synthetic workspace series
in microsiemens in, device signals / operating point / feature plane out.
"""

import sys
import unittest
from pathlib import Path

# The module is importable both as gui.sdk.classification.inference (the GUI
# runs from sw/) and via direct sw.classification imports (repo root on path).
REPO = Path(__file__).resolve().parents[4]
for path in (str(REPO / "sw"), str(REPO)):
    if path not in sys.path:
        sys.path.insert(0, path)

import numpy as np

from gui.sdk.classification import inference
from gui.sdk.classification.panel import validate_configuration

from sw.classification.classification.features_to_classification import (
    classify_features, plane_margins, train_svm)
from sw.classification.datasets.raw_to_timeseries import annotations_from_intervals
from sw.classification.features.conductance_S_to_features import conductance_S_to_features
from sw.classification.tools.timeseries import Timeseries
from sw.classification.tools.ts_params import (
    TSP_DECISION, TSP_MAGNITUDE, TSP_SUBJECT, TSP_UNITS,
)

OPTIONS = dict(sample_rate_Hz=4., window_s=30., step_s=15., threshold_noise_multiplier=.5)
CLASSES = {0: "control", 1: "stress"}
NAMES = ("peak_rate_per_min", "level_mean_S")


def training_series(subject, scale):
    """60 s of 2 µS conductance with a soft response after 30 s, in siemens."""
    time = np.arange(241) / 4.
    data = np.full(len(time), 2e-6)
    if scale:
        triangle = 1 - np.abs(np.arange(10) - 4.5) / 4.5
        data[120:] += scale * np.tile(triangle, 13)[:121]
    ts = Timeseries(subject, data, time)
    ts.params[TSP_MAGNITUDE], ts.params[TSP_UNITS] = "conductance", "S"
    ts.params[TSP_SUBJECT] = subject
    return ts


def live_series_uS(base_uS, response_uS, samples=244):
    """Workspace-style series (times in s, values in µS) with a step response."""
    times = np.arange(samples) / 4.
    values = np.full(samples, base_uS)
    values[120:] += response_uS
    return list(times), list(values)


class InferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        feature_sets, annotations = {}, {}
        for subject, scale in (("a", 4e-7), ("b", 8e-7)):
            ts = training_series(subject, scale)
            feature_sets[subject] = conductance_S_to_features(ts, **OPTIONS)
            annotations[subject] = annotations_from_intervals(ts, [(0., 30., 0), (30., 60., 1)],
                                                              CLASSES)
        cls.model = train_svm(feature_sets, annotations, feature_names=NAMES)
        cls.times, cls.values = live_series_uS(2., .8)

    def test_compute_outputs_features_and_units(self):
        outputs = inference.compute_outputs(self.model, 0., self.times, self.values, "µS")
        self.assertEqual(outputs["windows_valid"], 3)   # Windows ending at 30, 45, 60 s.
        self.assertEqual(outputs["window_times"], [30., 45., 60.])
        self.assertEqual(set(outputs["features"]), set(NAMES))
        self.assertEqual(outputs["features"]["peak_rate_per_min"]["unit"], "peaks/min")
        self.assertEqual(outputs["features"]["level_mean_S"]["unit"], "µS")
        # Feature values match a direct pipeline run of the same conversion chain.
        conductance = inference.classification_modules().data_to_conductance_S(
            inference.input_timeseries("live", self.times, self.values, "µS"))
        direct = conductance_S_to_features(conductance, feature_names=NAMES, **OPTIONS)
        np.testing.assert_allclose(outputs["features"]["level_mean_S"]["values"],
                                   np.asarray(direct["level_mean_S"].data) * 1e6)
        np.testing.assert_allclose(outputs["features"]["peak_rate_per_min"]["values"],
                                   direct["peak_rate_per_min"].data)

    def test_margin_is_raw_and_threshold_signal_is_constant(self):
        for threshold in (0., 3.):
            outputs = inference.compute_outputs(self.model, threshold, self.times,
                                                self.values, "µS")
            conductance = inference.classification_modules().data_to_conductance_S(
                inference.input_timeseries("live", self.times, self.values, "µS"))
            direct = classify_features(
                self.model, conductance_S_to_features(conductance, feature_names=NAMES,
                                                      **OPTIONS), 0.)
            # classify_features stores margin - threshold; the device restores
            # the raw decision function, independent of the inference threshold.
            np.testing.assert_allclose(outputs["margin"]["values"],
                                       direct.params[TSP_DECISION].data)
            self.assertEqual(outputs["margin_threshold"]["values"],
                             [threshold] * len(outputs["window_times"]))
            # The decision is 0/1 exactly where the raw margin crosses the
            # inference-time threshold.
            raw = np.asarray(outputs["margin"]["values"])
            expected = np.where(np.isfinite(raw), (raw > threshold).astype(float), np.nan)
            np.testing.assert_allclose(outputs["decision"]["values"], expected)

    def test_threshold_override_shifts_decision_not_margin(self):
        base = inference.compute_outputs(self.model, 0., self.times, self.values, "µS")
        shifted = inference.compute_outputs(self.model, 3., self.times, self.values, "µS")
        # The exposed margin stays raw; the operating point moves via the
        # constant margin-threshold signal and the POSITIVE cut.
        np.testing.assert_allclose(shifted["margin"]["values"], base["margin"]["values"])
        raw = np.asarray(base["margin"]["values"])
        self.assertEqual(np.count_nonzero(np.asarray(shifted["decision"]["values"]) == 1.),
                         np.count_nonzero(raw > 3.))
        self.assertEqual(np.count_nonzero(np.asarray(base["decision"]["values"]) == 1.),
                         np.count_nonzero(raw > 0.))

    def test_point_and_plane_use_live_slice(self):
        outputs = inference.compute_outputs(self.model, 0., self.times, self.values, "µS")
        point = outputs["point_raw"]
        self.assertIsNotNone(point)
        self.assertEqual(set(point), set(NAMES))
        # The operating point is the last valid window's raw feature values.
        conductance = inference.classification_modules().data_to_conductance_S(
            inference.input_timeseries("live", self.times, self.values, "µS"))
        direct = conductance_S_to_features(conductance, feature_names=NAMES, **OPTIONS)
        for name in NAMES:
            self.assertAlmostEqual(point[name], float(direct[name].data[-1]), places=12)
        plane = outputs["plane"]
        self.assertEqual(plane["pair"], NAMES)
        self.assertEqual(plane["margins"].shape, (120, 120))
        # The drawn plane is exactly the slice through the live operating
        # point; the stored grid is in display units, margins in raw units.
        (scale_u, _), (scale_v, _) = plane["scales"][0], plane["scales"][1]
        expected = plane_margins(self.model, plane["pair"], plane["uu"] / scale_u,
                                 plane["vv"] / scale_v, at=point)
        np.testing.assert_allclose(plane["margins"], expected)
        self.assertEqual(len(plane["point_xy"]), 2)
        # Display-space coordinates: raw point scaled by the display transforms.
        self.assertAlmostEqual(plane["point_xy"][0], point[NAMES[0]], places=9)
        self.assertAlmostEqual(plane["point_xy"][1], point[NAMES[1]] * 1e6, places=6)

    def test_windowing_overrides_change_windows_and_features(self):
        base = inference.compute_outputs(self.model, 0., self.times, self.values, "µS")
        self.assertEqual(base["window_times"], [30., 45., 60.])   # Trained options.
        overrides = dict(sample_rate_Hz=4., window_s=20., step_s=10.,
                         threshold_noise_multiplier=.5)
        outputs = inference.compute_outputs(self.model, 0., self.times, self.values, "µS",
                                            window_s=20., step_s=10.)
        self.assertEqual(outputs["window_times"], [20., 30., 40., 50., 60.])
        conductance = inference.classification_modules().data_to_conductance_S(
            inference.input_timeseries("live", self.times, self.values, "µS"))
        direct = conductance_S_to_features(conductance, feature_names=NAMES, **overrides)
        np.testing.assert_allclose(outputs["features"]["level_mean_S"]["values"],
                                   np.asarray(direct["level_mean_S"].data) * 1e6)
        # The resampling-rate override re-extracts on the new grid.
        resampled = inference.compute_outputs(self.model, 0., self.times, self.values,
                                              "µS", sample_rate_Hz=8.)
        direct = conductance_S_to_features(
            conductance, feature_names=NAMES, sample_rate_Hz=8., window_s=30., step_s=15.,
            threshold_noise_multiplier=.5)
        np.testing.assert_allclose(resampled["features"]["level_mean_S"]["values"],
                                   np.asarray(direct["level_mean_S"].data) * 1e6)
        # Overrides never leak into the model's stored training options.
        self.assertEqual(self.model["feature_options"]["window_s"], 30.)
        self.assertEqual(self.model["feature_options"]["step_s"], 15.)
        self.assertEqual(self.model["feature_options"]["sample_rate_Hz"], 4.)

    def test_positive_spans_run_merge_and_window_lead(self):
        # Window ends 60/75/90 s; 60 s windows lead each run by one window and
        # the two runs overlap, so they merge into one span.
        times = np.array([60., 75., 90.])
        self.assertEqual(inference.positive_spans(times, np.array([1., -1., 1.]), 60.),
                         [(0., 90.)])
        self.assertEqual(inference.positive_spans(times, np.array([-1., -1., -1.]), 60.), [])
        self.assertEqual(inference.positive_spans(times, np.array([np.nan, 1., 1.]), 60.),
                         [(15., 90.)])
        # A start before zero clips at 0.
        self.assertEqual(inference.positive_spans(np.array([10.]), np.array([1.]), 60.),
                         [(0., 10.)])
        # The threshold parameter moves the POSITIVE cut; the single POSITIVE
        # window ending at 75 s spans (75 - 60, 75).
        self.assertEqual(inference.positive_spans(times, np.array([1., 3., 1.]), 60., 2.),
                         [(15., 75.)])

    def test_input_timeseries_units_and_rejections(self):
        series = inference.input_timeseries("x", [0., 1.], [2., 3.], "µS")
        self.assertEqual(series.params[TSP_MAGNITUDE], "conductance")
        self.assertEqual(series.params[TSP_UNITS], "µS")
        series = inference.input_timeseries("x", [0.], [500.], "kohm")
        self.assertEqual(series.params[TSP_MAGNITUDE], "resistance")
        with self.assertRaisesRegex(ValueError, "Unsupported source unit"):
            inference.input_timeseries("x", [0.], [1.], "mV")
        with self.assertRaisesRegex(ValueError, "Unsupported source unit"):
            inference.compute_outputs(self.model, 0., self.times, self.values, "V")

    def test_load_model_rejects_foreign_files(self):
        with self.assertRaises(Exception):
            inference.load_model("/dev/null")


class ConfigurationTests(unittest.TestCase):
    """validate_configuration guards the saved windowing parameters too."""

    def test_accepts_windowing_options(self):
        values = validate_configuration({"threshold": .5, "window_s": 30,
                                         "step_s": 15, "sample_rate_Hz": 4.})
        self.assertEqual(values["window_s"], 30)
        self.assertEqual(values["sample_rate_Hz"], 4.)

    def test_rejects_invalid_windowing_options(self):
        for key, value in (("window_s", 0), ("step_s", -1), ("window_s", None),
                           ("step_s", "15 s"), ("sample_rate_Hz", "fast")):
            with self.assertRaisesRegex(ValueError, key):
                validate_configuration({key: value})
        self.assertEqual(validate_configuration({}), {})   # Legacy bundles stay valid.


if __name__ == "__main__":
    unittest.main()
