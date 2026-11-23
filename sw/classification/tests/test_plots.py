"""Smoke and behavior tests for the Timeseries plotting functions."""

import unittest

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from sw.classification.tools.timeseries import Timeseries
from sw.classification.tools.ts_params import (
    TSP_CLASSES, TSP_DECISION, TSP_MAGNITUDE, TSP_SUBJECT, TSP_THRESHOLD, TSP_UNITS, TSP_WINDOW_S,
)
from sw.classification.features.conductance_S_to_features import conductance_S_to_features
from sw.classification.classification.features_to_classification import classify_features, train_svm
from sw.classification.datasets.raw_to_timeseries import annotations_from_intervals
from sw.classification.plots import (
    _annotation_spans, _merged_spans, _prediction_spans, _range_mask, plot_classification,
    plot_feature_trajectory, plot_recording, plot_stages,
)

OPTIONS = dict(sample_rate_Hz=4., window_s=30., step_s=15., threshold_noise_multiplier=.5)
CLASSES = {0: "control", 1: "stress"}


def synthetic(subject, scale):
    time = np.arange(241) / 4.
    data = np.full(len(time), 2e-6)
    if scale:
        triangle = 1 - np.abs(np.arange(10) - 4.5) / 4.5
        data[120:] += scale * np.tile(triangle, 13)[:121]
    ts = Timeseries(subject, data, time)
    ts.params[TSP_MAGNITUDE], ts.params[TSP_UNITS], ts.params[TSP_SUBJECT] = "conductance", "S", subject
    return ts


class PlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        feature_sets, annotations = {}, {}
        for subject, scale in (("a", 4e-7), ("b", 8e-7)):
            ts = synthetic(subject, scale)
            feature_sets[subject] = conductance_S_to_features(ts, **OPTIONS)
            annotations[subject] = annotations_from_intervals(ts, [(0., 30., 0), (30., 60., 1)], CLASSES)
        cls.model = train_svm(feature_sets, annotations,
                              feature_names=("peak_rate_per_min", "level_mean_S", "peak_height_mean_S"))
        cls.recording = synthetic("c", 6e-7)
        cls.features = conductance_S_to_features(cls.recording, **OPTIONS)
        cls.annotations = annotations_from_intervals(cls.recording, [(0., 30., 0), (30., 60., 1)], CLASSES)
        cls.not_stress = annotations_from_intervals(cls.recording, [(0., 60., 0)], {0: "not stress"})

    def test_range_mask(self):
        mask = _range_mask(self.recording, (10., 50.))
        self.assertTrue((self.recording.time[mask] >= 10.).all() and (self.recording.time[mask] <= 50.).all())
        self.assertLess(np.count_nonzero(mask), len(self.recording.time))
        self.assertEqual(len(_range_mask(self.recording, None)), len(self.recording.time))
        self.assertEqual(np.count_nonzero(_range_mask(self.recording, (100., 200.))), 0)

    def test_annotation_spans_respect_range_and_only_stress(self):
        self.assertEqual(_annotation_spans(self.annotations, (35., 55.)), [(35., 55., "stress")])
        self.assertEqual(_annotation_spans(self.annotations, (10., 50.)), [(30., 50., "stress")])
        self.assertEqual(_annotation_spans(self.annotations, None), [(30., 60., "stress")])
        self.assertEqual(_annotation_spans(None, (0., 60.)), [])
        self.assertEqual(_annotation_spans(self.not_stress, (0., 60.)), [])

    def test_merged_and_prediction_spans_never_overlap(self):
        self.assertEqual(_merged_spans([(30., 90.), (0., 60.), (100., 120.)]), [(0., 90.), (100., 120.)])
        self.assertEqual(_merged_spans([]), [])
        # A 60 s window on a 15 s step: stress, one not-stress gap, stress again.
        # The two per-run spans ([0, 60] and [30, 90]) overlap and must merge.
        prediction = Timeseries("p", [1., 0., 1.], [60., 75., 90.])
        prediction.params[TSP_CLASSES] = {0: "not stress", 1: "stress"}
        prediction.params[TSP_WINDOW_S] = 60.
        self.assertEqual(_prediction_spans(prediction, None), [(0., 90.)])
        # Distant stress runs stay separate.
        prediction.data = np.array([1., 0., 0., 0., 0., 1.])
        prediction.time = [60., 75., 90., 105., 120., 135.]
        self.assertEqual(_prediction_spans(prediction, None), [(0., 60.), (75., 135.)])

    def test_recording_plot_margin_row_is_the_confidence(self):
        prediction = classify_features(self.model, self.features)
        figure = plot_recording(self.recording, self.recording, self.features, self.annotations,
                                ("peak_rate_per_min", "level_mean_S"), None, prediction, False)
        self.assertEqual(len(figure.axes), 4)                   # conductance + 2 features + margin.
        margins = prediction.params[TSP_DECISION]
        threshold = prediction.params[TSP_THRESHOLD]
        line = figure.axes[-1].lines[0]
        np.testing.assert_allclose(line.get_xdata(), margins.time)
        np.testing.assert_allclose(line.get_ydata(), margins.data + threshold)   # Raw margins.
        cuts = [ln.get_ydata()[0] for ln in figure.axes[-1].lines if len(ln.get_xdata()) == 2]
        self.assertIn(threshold, cuts)                          # The threshold line is visible.
        stressed = margins.data + threshold > threshold
        self.assertTrue(stressed.any())                         # Raw margin above threshold = stress.
        np.testing.assert_array_equal(stressed, prediction.data == 1)
        plt.close(figure)
        # Without a prediction there is no margin row.
        figure = plot_recording(self.recording, self.recording, self.features, None,
                                ("peak_rate_per_min",), None, None, False)
        self.assertEqual(len(figure.axes), 2)
        plt.close(figure)

    def test_recording_stage_and_classification_plots(self):
        prediction = classify_features(self.model, self.features)
        for figure in (plot_recording(self.recording, self.recording, self.features, self.annotations,
                                      ("peak_rate_per_min", "level_mean_S"), (10., 50.)),
                       plot_stages(self.recording, self.recording, self.features, self.annotations, (10., 50.)),
                       plot_recording(self.recording, self.recording, self.features, None,
                                      ("peak_rate_per_min", "level_mean_S"), None),
                       plot_recording(self.recording, self.recording, {}, None, (), None),
                       plot_classification(self.recording, prediction, self.annotations, (10., 50.)),
                       plot_classification(self.recording, prediction, None, None)):
            plt.close(figure)

    def test_recording_plot_with_prediction_and_normalized_conductance(self):
        prediction = classify_features(self.model, self.features)
        figure = plot_recording(self.recording, self.recording, self.features, self.annotations,
                                ("peak_rate_per_min", "level_mean_S"), (10., 50.), prediction, True)
        plt.close(figure)
        figure = plot_recording(self.recording, self.recording, self.features, None,
                                ("level_mean_S",), None, prediction=None, normalized=True)
        plt.close(figure)

    def test_trajectory_shows_effective_threshold_boundary(self):
        thresholded = train_svm({s: conductance_S_to_features(synthetic(s, scale), **OPTIONS)
                                 for s, scale in (("a", 4e-7), ("b", 8e-7))},
                                {s: annotations_from_intervals(synthetic(s, scale),
                                                               [(0., 30., 0), (30., 60., 1)], CLASSES)
                                 for s, scale in (("a", 4e-7), ("b", 8e-7))},
                                feature_names=("peak_rate_per_min", "level_mean_S"),
                                decision_threshold=1.)
        figure = plot_feature_trajectory(thresholded, self.features,
                                         ("peak_rate_per_min", "level_mean_S"), None)
        self.assertIn("margin = 1", figure.axes[0].get_title())
        plt.close(figure)
        # An inference-time override reaches both panels as well.
        figure = plot_feature_trajectory(self.model, self.features,
                                         ("peak_rate_per_min", "level_mean_S"), None, 2.)
        panels = [axis for axis in figure.axes if axis.get_label() != "<colorbar>"]
        self.assertEqual(len(panels), 2)                        # 3-feature model: second pair panel.
        for axis in panels:
            self.assertIn("margin = 2", axis.get_title())
        plt.close(figure)

    def test_trajectory_support_vectors_and_annotation_markers(self):
        combined = annotations_from_intervals(self.recording, [(0., 45., 0), (45., 60., 1)], CLASSES)
        figure = plot_feature_trajectory(self.model, self.features,
                                         ("peak_rate_per_min", "level_mean_S"), None, None, combined)

        def square_markers(axis):
            """Scatter collections drawing the square annotation markers (5-vertex path)."""
            return [c for c in axis.collections
                    if getattr(c, "get_paths", None) and c.get_paths()
                    and c.get_paths()[0].vertices.shape[0] == 5]

        from matplotlib.collections import LineCollection
        panels = [axis for axis in figure.axes if axis.get_label() != "<colorbar>"]
        for axis in panels:
            collection = next(c for c in axis.collections if isinstance(c, LineCollection))
            self.assertEqual(collection.get_cmap().name, "plasma")   # Trajectory colored by time.
            self.assertGreater(len(collection.get_segments()), 0)
            # Training-window circles and support vectors are open: no filling.
            circles = [c for c in axis.collections
                       if type(c).__name__ == "PathCollection" and c.get_paths()
                       and c.get_paths()[0].vertices.shape[0] == 26]
            self.assertGreaterEqual(len(circles), 2)            # Class circles + support vectors.
            for circle in circles:
                facecolors = circle.get_facecolors()
                self.assertTrue(len(facecolors) == 0 or np.abs(facecolors).max() == 0.)
        tab_red = np.array((214, 39, 40)) / 255.
        for axis in panels:
            squares = square_markers(axis)
            self.assertEqual(len(squares), 1)
            offsets = squares[0].get_offsets()
            self.assertEqual(len(offsets), 1)                   # Half-open: only the window at
            colors = np.array(squares[0].get_facecolors())[:, :3]   # t = 45 s is annotated stress.
            self.assertAlmostEqual(np.abs(colors[0] - tab_red).max(), 0., places=6)
        plt.close(figure)
        # Annotated control windows get no marker, only stress does.
        control = annotations_from_intervals(self.recording, [(0., 60., 0)], CLASSES)
        figure = plot_feature_trajectory(self.model, self.features,
                                         ("peak_rate_per_min", "level_mean_S"), None, None, control)
        self.assertEqual(square_markers(figure.axes[0]), [])
        plt.close(figure)
        # Annotated stress windows get exactly one red marker each.
        partial = annotations_from_intervals(self.recording, [(40., 60., 1)], {1: "stress"})
        figure = plot_feature_trajectory(self.model, self.features,
                                         ("peak_rate_per_min", "level_mean_S"), None, None, partial)
        offsets = square_markers(figure.axes[0])[0].get_offsets()
        self.assertEqual(len(offsets), 1)                       # Only the annotated stress window.
        plt.close(figure)
        # Without annotations there are no markers at all.
        figure = plot_feature_trajectory(self.model, self.features,
                                         ("peak_rate_per_min", "level_mean_S"), None)
        self.assertEqual(square_markers(figure.axes[0]), [])
        plt.close(figure)

    def test_trajectory_two_features_named_axes_and_selection_errors(self):
        figure = plot_feature_trajectory(self.model, self.features,
                                         ("peak_rate_per_min", "level_mean_S"), (10., 55.))
        self.assertIn("peak_rate_per_min", figure.axes[0].get_xlabel())
        self.assertIn("level_mean_S", figure.axes[0].get_ylabel())
        # Second panel: the first feature against the remaining model feature.
        self.assertIn("peak_rate_per_min", figure.axes[1].get_xlabel())
        self.assertIn("peak_height_mean_S", figure.axes[1].get_ylabel())
        plt.close(figure)
        for names in (("level_mean_S",),
                      ("level_mean_S", "peak_height_mean_S", "peak_rate_per_min")):
            with self.assertRaisesRegex(ValueError, "exactly two features"):
                plot_feature_trajectory(self.model, self.features, names)
        with self.assertRaisesRegex(ValueError, "not available"):
            plot_feature_trajectory(self.model, self.features, ("level_mean_S", "unknown_feature"))
        with self.assertRaisesRegex(ValueError, "only use model features"):
            plot_feature_trajectory(self.model, self.features,
                                    ("level_mean_S", "peak_height_q50_S"))
        with self.assertRaisesRegex(ValueError, "No valid prediction windows"):
            plot_feature_trajectory(self.model, self.features,
                                    ("peak_rate_per_min", "level_mean_S"), (500., 600.))


if __name__ == "__main__":
    unittest.main()
