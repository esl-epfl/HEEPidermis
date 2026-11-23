"""Numerical, annotation and data-separation checks for the Timeseries pipeline."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from sw.classification.tools.timeseries import Timeseries
from sw.classification.tools.ts_params import (
    TSP_CLASSES, TSP_CHANNELS, TSP_DECISION, TSP_END_S, TSP_FEATURE, TSP_MAGNITUDE,
    TSP_PROCESSING, TSP_SUBJECT, TSP_THRESHOLD, TSP_UNITS, TSP_VALID, TSP_WINDOW_S,
)
from sw.classification.conversion.data_to_conductance_S import (
    data_to_conductance_S, normalized_conductance,
)
from sw.classification.features.conductance_S_to_features import (
    FEATURE_NAMES, baseline_relative, conductance_S_to_features,
    decompose_conductance, summarize_window,
)
from sw.classification.features.features import (
    INSTANT_RATE_Q95_HZ, LEVEL_MAX_S, LEVEL_MEAN_S, LEVEL_MIN_S,
    LEVEL_SLOPE_S_PER_S, MIST_LINEAR_FEATURES, PEAK_HEIGHT_MEAN_S,
    PEAK_RATE_PER_MIN,
)
from sw.classification.classification.features_to_classification import (
    _pipeline, classify_features, evaluate_test, leave_one_person_out,
    load_model, save_model, train_svm, window_annotations,
)
from sw.classification.datasets.io import load_dataset
from sw.classification.datasets.raw_to_timeseries import (
    annotations_from_intervals, case_to_timeseries, gui_to_timeseries,
    physionet_to_timeseries, save_recording,
)

ANN_PARAMS = {TSP_SUBJECT: "s", TSP_END_S: 0.}
CLASSES = {0: "control", 1: "stress"}


def series(name, time, data, magnitude, units, params=None):
    """Fixture: a Timeseries with magnitude/units metadata, like the pipeline builds them."""
    ts = Timeseries(name, data, time)
    ts.params.update(params or {})
    ts.params[TSP_MAGNITUDE], ts.params[TSP_UNITS] = magnitude, units
    ts.params.setdefault(TSP_SUBJECT, name)
    return ts


def feature_set(subject, amplitude, rate):
    """Feature dictionary of two Timeseries with three windows."""
    time = (np.arange(len(amplitude)) + 1) * 60.
    params = {TSP_SUBJECT: subject, TSP_WINDOW_S: 60., TSP_PROCESSING: {"sample_rate_Hz": 4.}}
    valid = series(subject, time, np.ones(len(time)), "validity", "1")

    def feature(name, values):
        return series(subject, time, values, "feature", "1",
                           {**params, TSP_FEATURE: name, TSP_VALID: valid})

    return {"amplitude": feature("amplitude", amplitude), "rate": feature("rate", rate)}


def annotations(subject, intervals, classes=CLASSES):
    data = series(subject, [0., max(end for _, end, _ in intervals) + 1.],
                       [0., 0.], "conductance", "S")
    return annotations_from_intervals(data, intervals, classes)


class ConversionTests(unittest.TestCase):
    def test_unit_equivalence(self):
        time = np.arange(20) / 10 + 100_000
        g = series("g", time, np.full(20, 2.), "conductance", "uS")
        r = series("r", time, np.full(20, 500.), "resistance", "kohm")
        v = series("v", time, np.full(20, .24), "voltage", "V",
                        {TSP_CHANNELS: {"current": series("c", time, np.full(20, .48), "current", "uA")}})
        expected = data_to_conductance_S(g)
        np.testing.assert_allclose(expected.data, 2e-6)
        np.testing.assert_allclose(data_to_conductance_S(r).data, expected.data)
        np.testing.assert_allclose(data_to_conductance_S(v).data, expected.data)
        self.assertEqual(expected.params[TSP_MAGNITUDE], "conductance")
        self.assertEqual(expected.params[TSP_UNITS], "S")
        self.assertEqual(expected.params[TSP_SUBJECT], "g")

    def test_offset_reversed_drops_and_validity(self):
        current = series("c", np.arange(4.), np.full(4, .52e-6), "current", "A")
        data = series("v", np.arange(4.), [.28, .02, -.1, .24], "voltage", "V",
                           {TSP_CHANNELS: {"current": current, "offset": series(
                               "o", np.arange(4.), [.02, .02, .02, 0.], "voltage", "V")}})
        result = data_to_conductance_S(data).data
        np.testing.assert_allclose(result[0], 2e-6)
        self.assertTrue(np.isnan(result[1:3]).all())
        data.params[TSP_CHANNELS]["valid"] = series(
            "f", np.arange(4.), [1, 1, 1, 0], "validity", "1")
        self.assertTrue(np.isnan(data_to_conductance_S(data).data[-1]))

    def test_normalized_conductance_mean_plus_three_sigma_clipped(self):
        time = np.arange(21.)
        values = np.full(21, 1e-6)
        values[10] = 100e-6  # Outlier far above the mean + 3 sigma of the recording.
        values[11] = np.nan
        ts = series("s", time, values, "conductance", "S")
        normalized = normalized_conductance(ts)
        self.assertTrue(np.isnan(normalized.data[11]))           # NaN stays NaN.
        self.assertEqual(normalized.data[10], 1.)                # Outlier clipped to the ceiling.
        self.assertTrue((normalized.data[:10] < 1.).all())       # Regular values stay below it.
        self.assertTrue(((normalized.data[:10] > 0.) & (normalized.data[:10] <= 1.)).all())
        self.assertEqual((normalized.params[TSP_MAGNITUDE], normalized.params[TSP_UNITS]),
                         ("conductance", "normalized"))
        with self.assertRaisesRegex(ValueError, "valid sample"):
            normalized_conductance(series("s", np.array([0.]), np.array([np.nan]), "conductance", "S"))



class FeatureTests(unittest.TestCase):
    @staticmethod
    def known_decomposition():
        time = np.arange(60.)
        level = series("s", time, 2e-6 + time * 1e-8, "conductance", "S")
        zero = series("s", time, np.zeros(60), "conductance", "S")
        peaks = series("s", [10., 20., 30., 40.], [1e-7, 2e-7, 3e-7, 4e-7], "peak_height", "S")
        peak_blocks = series("s", [10., 20., 30., 40.], [0., 0., 1., 2.], "processing_block", "index")
        blocks = series("s", time, np.zeros(60), "processing_block", "index")
        return {"level": level, "tonic": level, "phasic": zero, "threshold": zero,
                "blocks": blocks, "peaks": peaks, "peak_blocks": peak_blocks}

    def test_paper_feature_statistics_and_rate_units(self):
        values, count, coverage = summarize_window(self.known_decomposition(), 0., 60.)
        self.assertEqual(set(values), set(FEATURE_NAMES))
        self.assertEqual(len(values), 16)
        self.assertEqual((count, coverage), (4, 1.))
        self.assertAlmostEqual(values["level_slope_S_per_s"], 1e-8)
        self.assertAlmostEqual(values["peak_height_q50_S"], 2.5e-7)
        self.assertAlmostEqual(values["peak_rate_per_min"], 4.)
        self.assertAlmostEqual(values["instant_rate_q95_Hz"], .1)

    def test_no_cross_block_rates_and_empty_distributions(self):
        d = self.known_decomposition()
        self.assertTrue(np.isnan(summarize_window(d, 25., 50.)[0]["instant_rate_q50_Hz"]))
        window = summarize_window(d, 0., 5.)[0]
        self.assertEqual(window["peak_rate_per_min"], 0.)
        self.assertTrue(np.isnan(window["peak_height_mean_S"]))

    def test_gaps_interpolated_by_default_and_time_gaps_split_blocks(self):
        time = np.arange(0., 150., .1)
        values = np.full(len(time), 2e-6)
        values[(time >= 60) & (time < 80)] = np.nan      # Invalid run.
        values[time >= 80] = 5e-6
        on = decompose_conductance(series("g", time, values, "conductance", "S"))
        gap = (on["level"].time >= 60) & (on["level"].time < 80)
        self.assertTrue(np.isfinite(on["level"].data[gap]).all())
        self.assertEqual(len(np.unique(on["blocks"].data[on["blocks"].data >= 0])), 1)
        np.testing.assert_allclose(on["tonic"].data + on["phasic"].data, on["level"].data)
        # Interpolation off: invalid samples stay unfilled and unblocked.
        off = decompose_conductance(series("g", time, values, "conductance", "S"), interpolate_gaps=False)
        self.assertTrue(np.isnan(off["level"].data[gap]).all())
        self.assertTrue((off["blocks"].data[gap] == -1).all())
        # Missing timestamps always split blocks and stay unfilled.
        hole = np.r_[time[:600], time[1000:]]
        data_values = np.full(len(hole), 2e-6)
        data_values[hole >= 100] = 5e-6
        d = decompose_conductance(series("g", hole, data_values, "conductance", "S"))
        missing = (d["level"].time > 59.8) & (d["level"].time < 100.)
        self.assertTrue(np.isnan(d["level"].data[missing]).all())
        self.assertTrue(np.isfinite(d["level"].data[~missing]).all())
        self.assertEqual(len(np.unique(d["blocks"].data[d["blocks"].data >= 0])), 2)
        self.assertEqual(len(d["peaks"].data), 0)

    def test_feature_timeseries_and_window_labels(self):
        time = np.arange(241) / 4.
        features = conductance_S_to_features(
            series("s", time, np.full(len(time), 2e-6), "conductance", "S", {TSP_SUBJECT: "s"}),
            sample_rate_Hz=4., window_s=30., step_s=15.)
        self.assertEqual(set(features), set(FEATURE_NAMES))
        first = features["level_mean_S"]
        self.assertEqual(first.name, "s")
        self.assertEqual(first.params[TSP_WINDOW_S], 30.)
        np.testing.assert_allclose(first.time, [30., 45., 60.])
        np.testing.assert_allclose(first.params[TSP_VALID].data, [1, 1, 1])
        self.assertEqual(first.params[TSP_FEATURE], "level_mean_S")
        ann = annotations("s", [(0., 30., 0), (30., 60., 1)])
        np.testing.assert_allclose(window_annotations(ann, first).data, [0., -1., 1.])

    def test_svm_feature_selection_names_existing_features(self):
        self.assertTrue(set(MIST_LINEAR_FEATURES) <= set(FEATURE_NAMES))

    def test_relative_window_features_are_level_independent(self):
        values, count, coverage = summarize_window(self.known_decomposition(), 0., 60., relative=True)
        reference = 2e-6 + 29.5e-8  # Mean of 2e-6 + t*1e-8 over the 60 s window.
        self.assertAlmostEqual(values[LEVEL_MEAN_S], 1.)
        self.assertAlmostEqual(values[LEVEL_MAX_S], (2e-6 + 59e-8) / reference)
        self.assertAlmostEqual(values[LEVEL_SLOPE_S_PER_S], 1e-8 / reference)
        self.assertAlmostEqual(values[PEAK_HEIGHT_MEAN_S], 2.5e-7 / reference)
        self.assertAlmostEqual(values[PEAK_RATE_PER_MIN], 4.)       # Rates stay absolute.
        self.assertAlmostEqual(values[INSTANT_RATE_Q95_HZ], .1)
        # Without the flag everything stays physical.
        absolute, _, _ = summarize_window(self.known_decomposition(), 0., 60.)
        self.assertAlmostEqual(absolute[LEVEL_MEAN_S], 2.295e-6)

    def test_relative_features_independent_of_absolute_level(self):
        time = np.arange(241) / 4.

        def signal(scale):
            data = np.full(len(time), 2e-6 * scale)
            triangle = 1 - np.abs(np.arange(10) - 4.5) / 4.5
            data[120:] += scale * 4e-7 * np.tile(triangle, 13)[:121]
            return series("s", time, data, "conductance", "S", {TSP_SUBJECT: "s"})

        options = dict(sample_rate_Hz=4., window_s=30., step_s=15., relative_windows=True)
        small = conductance_S_to_features(signal(1.), **options)
        large = conductance_S_to_features(signal(5.), **options)
        self.assertEqual(small[LEVEL_MEAN_S].params[TSP_UNITS], "1")
        self.assertEqual(small[PEAK_RATE_PER_MIN].params[TSP_UNITS], "peaks/min")
        self.assertTrue(small[LEVEL_MEAN_S].params[TSP_PROCESSING]["relative_windows"])
        for name in (LEVEL_MEAN_S, LEVEL_MAX_S, LEVEL_MIN_S, LEVEL_SLOPE_S_PER_S):
            np.testing.assert_allclose(small[name].data, large[name].data, rtol=1e-9)
        # Peak detection keeps an absolute height floor, so peak counts may
        # differ between absolute scales; the level features must not.

    def test_baseline_relative_levels_and_zero_divisor(self):
        time = [30.]
        features = {name: series("s", time, [2.], "feature", "S" if "level" in name or name == "peak_height_mean_S" else "1")
                    for name in FEATURE_NAMES}
        baseline = {name: series("b", time, [4.], "feature", "1") for name in FEATURE_NAMES}
        baseline["peak_rate_per_min"] = series("b", time, [0.], "feature", "1")
        relative = baseline_relative(features, baseline)
        self.assertEqual(relative["level_mean_S"].data[0], -2.)
        self.assertEqual(relative["level_max_S"].data[0], -2.)  # Levels subtract the baseline mean.
        self.assertEqual(relative["peak_height_mean_S"].data[0], .5)
        self.assertTrue(np.isnan(relative["peak_rate_per_min"].data[0]))


class ClassificationTests(unittest.TestCase):
    def training_data(self):
        subjects = {"a": (0., 1.), "b": (10., 11.), "c": (100., 101.)}
        feature_sets = {s: feature_set(s, np.array(v), np.array([1., 2.])) for s, v in subjects.items()}
        annotated = {s: annotations(s, [(0., 60., 0), (60., 120., 1)]) for s in subjects}
        return feature_sets, annotated

    def test_each_lopo_scaler_sees_training_subjects_only(self):
        feature_sets, annotated = self.training_data()
        pipelines = []

        def capture(**options):
            pipeline = _pipeline(**options)
            pipelines.append(pipeline)
            return pipeline

        with patch("sw.classification.classification.features_to_classification._pipeline", capture):
            leave_one_person_out(feature_sets, annotated, feature_names=("amplitude", "rate"))
        self.assertEqual(len(pipelines), 3)
        # The last fold holds out subject c, so the mean must exclude it.
        scaler = pipelines[-1].named_steps["standardscaler"]
        self.assertAlmostEqual(scaler.mean_[0], 5.5)
        self.assertEqual(scaler.n_samples_seen_, 4)

    def test_train_classify_roundtrip_and_rejected_windows(self):
        feature_sets, annotated = self.training_data()
        names = ("amplitude", "rate")
        with self.assertRaisesRegex(ValueError, "Unknown feature names"):
            train_svm(feature_sets, annotated, feature_names=("amplitude", "nonexistent"))
        model = train_svm(feature_sets, annotated, feature_names=names)
        expected = classify_features(model, feature_sets["c"])
        np.testing.assert_allclose(expected.data, [0., 1.])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "svm.joblib"
            save_model(model, path)
            loaded = load_model(path)
        reordered = {name: {key: features[key] for key in reversed(list(features))}
                     for name, features in feature_sets.items()}
        result = classify_features(loaded, reordered["c"])
        np.testing.assert_allclose(result.data, expected.data)
        np.testing.assert_allclose(result.time, expected.time)
        decision = result.params["SVM decision"]
        self.assertEqual(len(decision.data), len(expected.data))

    def test_rejected_windows_and_unlabeled_scoring(self):
        feature_sets, annotated = self.training_data()
        model = train_svm(feature_sets, annotated, feature_names=("amplitude", "rate"))
        features = feature_set("d", np.array([5., 50.]), np.array([1., 2.]))
        features["amplitude"].params[TSP_VALID].data[0] = 0
        features["rate"].params[TSP_VALID].data[0] = 0
        prediction = classify_features(model, features)
        self.assertTrue(np.isnan(prediction.data[0]))
        self.assertEqual(prediction.data[1], 1.)
        self.assertEqual(evaluate_test(model, {"d": prediction}, {}), {"scored_windows": 0})
        scores = evaluate_test(model, {"d": prediction}, {"d": annotations("d", [(0., 60., 0), (60., 120., 1)])})
        self.assertEqual(scores["scored_windows"], 1)
        self.assertAlmostEqual(scores["accuracy"], 1.)
        overlap = classify_features(model, feature_set("a", np.array([5., 50.]), np.array([1., 2.])))
        with self.assertRaisesRegex(ValueError, "overlap"):
            evaluate_test(model, {"a": overlap}, {})

    def test_decision_threshold_requires_larger_stress_margins(self):
        feature_sets, annotated = self.training_data()
        names = ("amplitude", "rate")
        base = train_svm(feature_sets, annotated, feature_names=names)
        strict = train_svm(feature_sets, annotated, feature_names=names, decision_threshold=100.)
        base_prediction = classify_features(base, feature_sets["c"])
        strict_prediction = classify_features(strict, feature_sets["c"])
        self.assertTrue((base_prediction.data == 1).any())      # Some stress windows without the shift.
        self.assertTrue((strict_prediction.data == 0).all())    # A huge margin requirement: no stress.
        np.testing.assert_allclose(strict_prediction.params[TSP_DECISION].data,
                                   base_prediction.params[TSP_DECISION].data - 100.)
        # The applied threshold travels with the prediction for plotting.
        self.assertEqual(strict_prediction.params[TSP_THRESHOLD], 100.)
        self.assertEqual(base_prediction.params[TSP_THRESHOLD], 0.)
        # The threshold can also be overridden at inference time, without refitting.
        overridden = classify_features(base, feature_sets["c"], decision_threshold=100.)
        np.testing.assert_allclose(overridden.data, strict_prediction.data)
        np.testing.assert_allclose(overridden.params[TSP_DECISION].data,
                                   strict_prediction.params[TSP_DECISION].data)
        kept = classify_features(base, feature_sets["c"])
        np.testing.assert_allclose(kept.params[TSP_DECISION].data,
                                   base_prediction.params[TSP_DECISION].data)
        # leave_one_person_out accepts the same option.
        scores = leave_one_person_out(feature_sets, annotated, feature_names=names, decision_threshold=100.)
        self.assertEqual(scores["scored_windows"], 6)

    def test_input_units_recorded_and_mismatched_inference_rejected(self):
        def synthetic(subject, scale):
            time = np.arange(241) / 4.
            data = np.full(len(time), 2e-6)
            if scale:
                triangle = 1 - np.abs(np.arange(10) - 4.5) / 4.5
                data[120:] += scale * np.tile(triangle, 13)[:121]
            return series(subject, time, data, "conductance", "S", {TSP_SUBJECT: subject})

        names = ("level_mean_S", "peak_rate_per_min")
        options = dict(sample_rate_Hz=4., window_s=30., step_s=15., threshold_noise_multiplier=.5)
        annotated, normalized = {}, {}
        for subject, scale in (("a", 4e-7), ("b", 8e-7)):
            ts = synthetic(subject, scale)
            annotated[subject] = annotations_from_intervals(ts, [(0., 30., 0), (30., 60., 1)], CLASSES)
            normalized[subject] = conductance_S_to_features(normalized_conductance(ts), **options)
        processing = normalized["a"]["level_mean_S"].params[TSP_PROCESSING]
        self.assertEqual(processing["input_units"], "normalized")
        self.assertNotIn("input_units", options)
        model = train_svm(normalized, annotated, feature_names=names)
        same = classify_features(model, conductance_S_to_features(
            normalized_conductance(synthetic("c", 6e-7)), **options))
        self.assertIn(int(same.data[-1]), (0, 1))
        with self.assertRaisesRegex(ValueError, "differ from the fitted model"):
            classify_features(model, conductance_S_to_features(synthetic("c", 6e-7), **options))

    def test_end_to_end_synthetic_subjects(self):
        def synthetic(subject, scale):
            time = np.arange(241) / 4.
            data = np.full(len(time), 2e-6)
            if scale:
                triangle = 1 - np.abs(np.arange(10) - 4.5) / 4.5
                data[120:] += scale * np.tile(triangle, 13)[:121]
            return series(subject, time, data, "conductance", "S", {TSP_SUBJECT: subject})

        names = ("peak_rate_per_min", "level_mean_S")
        options = dict(sample_rate_Hz=4., window_s=30., step_s=15., threshold_noise_multiplier=.5)
        feature_sets, annotated = {}, {}
        for subject, scale in (("a", 4e-7), ("b", 8e-7)):
            ts = synthetic(subject, scale)
            feature_sets[subject] = conductance_S_to_features(ts, **options)
            annotated[subject] = annotations_from_intervals(
                ts, [(0., 30., 0), (30., 60., 1)], CLASSES)
        model = train_svm(feature_sets, annotated, feature_names=names)
        self.assertEqual(sorted(model["classes"].values()), ["control", "stress"])
        scores = leave_one_person_out(feature_sets, annotated, feature_names=names)
        self.assertEqual(scores["scored_windows"], 4)
        self.assertAlmostEqual(scores["accuracy"], 1.)
        validation = conductance_S_to_features(synthetic("c", 6e-7), **options)
        prediction = classify_features(model, validation)
        self.assertEqual(prediction.params[TSP_CLASSES][int(prediction.data[-1])], "stress")
        rbf = train_svm(feature_sets, annotated, feature_names=names, kernel="rbf")
        self.assertEqual(classify_features(rbf, validation).params[TSP_CLASSES][int(prediction.data[-1])], "stress")


class DatasetTests(unittest.TestCase):
    def gui_csv(self, folder):
        import csv
        header = ["workspace_kind", "workspace_device", "time_s", "vco_p_uV", "vco_n_uV",
                  "delta_v_uV", "injected_current_uA", "voltage_offset_mV", "valid",
                  "motion_artifact", "current_settling", "buffer_loss", "segment",
                  "differential_mode", "sample_rate_Hz", "source", "test", "event_label", "run_id"]
        rows = [dict.fromkeys(header, "")]
        for i in range(4):
            rows.append(dict(workspace_kind="sample", workspace_device="heepidermis",
                             time_s=str(i), vco_p_uV="700000", vco_n_uV="500000",
                             delta_v_uV="300000", injected_current_uA=".6",
                             voltage_offset_mV="0.", valid="1", motion_artifact=str(int(i == 2)),
                             segment="1", differential_mode="Hardware P-N",
                             sample_rate_Hz="10.", source="raw"))
        rows.append(dict(workspace_kind="sample", workspace_device="tests", time_s="0.",
                         test="mist", event_label="control start", run_id="r1"))
        rows.append(dict(workspace_kind="sample", workspace_device="tests", time_s="2.",
                         test="mist", event_label="control end", run_id="r1"))
        path = folder / "Anna.csv"
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_gui_to_timeseries_names_channels_and_annotations(self):
        with tempfile.TemporaryDirectory() as folder:
            data, ann = gui_to_timeseries(self.gui_csv(Path(folder)), "Anna")
        self.assertEqual((data.name, ann.name), ("Anna", "ann_Anna"))
        self.assertEqual(data.params[TSP_MAGNITUDE], "voltage")
        self.assertEqual(data.params[TSP_SUBJECT], "Anna")
        for channel in ("P", "N", "current", "offset", "valid", "segment", "P_minus_N"):
            self.assertIn(channel, data.params[TSP_CHANNELS])
        self.assertTrue(np.isnan(data.params[TSP_CHANNELS]["valid"].data[2]) or
                        data.params[TSP_CHANNELS]["valid"].data[2] == 0)
        self.assertEqual(ann.params[TSP_CLASSES], {0: "not stress", 1: "stress"})
        np.testing.assert_allclose(ann.data, [0., -1.])

    def test_case_to_timeseries_labels_and_arousal_agreement(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            # 10 s physiological CSV at 1 kHz: scary video for 0-5 s, boring for 5-10 s.
            gsr = 5. + .1 * np.sin(np.arange(10000) / 50.)
            physio = [f"{ms},0,0,{gsr[ms]:.3f},0,0,0,0,0,{7 if ms < 5000 else 3}"
                      for ms in range(10000)]
            (folder / "sub_1.csv").write_text(
                "daqtime,ecg,bvp,gsr,rsp,skt,emg_zygo,emg_coru,emg_trap,video\n" + "\n".join(physio) + "\n")
            # Joystick annotations at 20 Hz; arousal agrees with the elicitation.
            arousal = [8. if ms < 5000 else 2. for ms in range(0, 10000, 50)]
            (folder / "ann_sub_1.csv").write_text(
                "jstime,valence,arousal,video\n" +
                "\n".join(f"{ms},5,{value}" for ms, value in zip(range(0, 10000, 50), arousal)) + "\n")
            data, ann = case_to_timeseries(folder / "sub_1.csv", folder / "ann_sub_1.csv", "sub_01")
            self.assertEqual((data.name, data.params[TSP_UNITS], data.params[TSP_MAGNITUDE]),
                             ("sub_01", "uS", "conductance"))
            self.assertEqual(len(data.time), 40)                    # 1 kHz decimated to 4 Hz.
            np.testing.assert_allclose(np.diff(data.time), .25)
            video = data.params[TSP_CHANNELS]["video"]
            self.assertTrue((video.data[:20] == 7).all() and (video.data[20:] == 3).all())
            self.assertEqual(ann.name, "ann_sub_01")
            np.testing.assert_allclose(ann.time, [0., 5., 10.])
            np.testing.assert_allclose(ann.data, [1., 0., -1.])     # stress holds to 5 s, then not stress.
            self.assertEqual(ann.params["Arousal threshold (0.5-9.5)"], 5.0)

            # Disagreeing arousal drops the label: scary with low arousal is unannotated.
            arousal = [2.] * len(range(0, 10000, 50))
            (folder / "ann_sub_1.csv").write_text(
                "jstime,valence,arousal,video\n" +
                "\n".join(f"{ms},5,{value}" for ms, value in zip(range(0, 10000, 50), arousal)) + "\n")
            _, ann = case_to_timeseries(folder / "sub_1.csv", folder / "ann_sub_1.csv", "sub_01")
            np.testing.assert_allclose(ann.time, [5., 10.])         # Only the boring segment labeled.
            np.testing.assert_allclose(ann.data, [0., -1.])

    def test_physionet_to_timeseries_and_dataset_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subject = root / "raw/STRESS/f01"
            subject.mkdir(parents=True)
            origin = "2013-06-12 16:18:58"
            tags = ["2013-06-12 16:18:58"] + [(f"2013-06-12 16:{m:02d}:00") for m in range(20, 28)]
            (subject / "tags.csv").write_text("\n".join(tags) + "\n")
            with (subject / "EDA.csv").open("w") as stream:
                stream.write(f"{origin}\n4.\n")
                stream.write("\n".join("2." for _ in range(40)) + "\n")
            data, ann = physionet_to_timeseries(subject)
            self.assertEqual((data.name, data.params[TSP_UNITS]), ("f01", "uS"))
            self.assertEqual(ann.name, "ann_f01")
            self.assertEqual(ann.params[TSP_CLASSES], {0: "not stress", 1: "stress"})
            self.assertIn("License", data.params)
            save_recording(data, ann, root / "training")
            recordings, annotations = load_dataset(root / "training")
            self.assertEqual(set(recordings), {"f01"})
            np.testing.assert_allclose(Timeseries.load(root / "training/f01.pkl").data, data.data)
            self.assertEqual(annotations["f01"].name, "ann_f01")

    def test_dataset_rejects_orphans_and_missing_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            series("ann_y", [0., 1.], [0., 0.], "annotation", "class_index",
                   {TSP_SUBJECT: "y", TSP_CLASSES: CLASSES}).dump(f"{root}/")
            with self.assertRaisesRegex(ValueError, "matching recordings"):
                load_dataset(root)
            data = series("y", [0., 1.], [0., 0.], "conductance", "S", {TSP_SUBJECT: "y"})
            save_recording(data, series("ann_y", [0., 1.], [0., 0.], "annotation", "class_index",
                                        {TSP_SUBJECT: "y"}), root)
            with self.assertRaisesRegex(ValueError, "class index"):
                load_dataset(root)
            orphan = Timeseries.load(root / "y.pkl")
            del orphan.params[TSP_SUBJECT]
            orphan.dump(f"{root}/", "z")
            series("ann_z", [0., 1.], [0., 0.], "annotation", "class_index",
                   {TSP_SUBJECT: "z", TSP_CLASSES: CLASSES}).dump(f"{root}/")
            with self.assertRaisesRegex(ValueError, "Missing"):
                load_dataset(root)


if __name__ == "__main__":
    unittest.main()
