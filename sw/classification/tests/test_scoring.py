"""SzCORE scoring tests: sample-based and event-based metrics on synthetic cases.

Expected counts are derived by hand for short recordings; see
classification/scoring.py for the methodology and its parameters.
"""

import unittest

import numpy as np

from sw.classification.classification.scoring import (
    EventParameters, annotation_stress_spans, evaluate_szcore, event_scoring,
    prediction_stress_spans, sample_scoring,
)
from sw.classification.datasets.raw_to_timeseries import annotations_from_intervals
from sw.classification.tools.timeseries import Timeseries
from sw.classification.tools.ts_params import TSP_CLASSES, TSP_SUBJECT, TSP_VALID, TSP_WINDOW_S

CLASSES = {0: "not stress", 1: "stress"}


def prediction(name, window_s, stress_ends, ends=None, classes=CLASSES):
    """A classification Timeseries: stress at the given window-end times, else not stress."""
    ends = sorted(set(ends or [60., 120., 180., 240., 300., 360.]) | set(stress_ends))
    data = [1. if end in stress_ends else 0. for end in ends]
    valid = Timeseries(name, np.ones(len(ends)), ends)
    result = Timeseries(name, data, ends)
    result.params.update({TSP_SUBJECT: name, TSP_WINDOW_S: window_s, TSP_CLASSES: dict(classes),
                          TSP_VALID: valid})
    return result


def annotations(recording, intervals):
    return annotations_from_intervals(recording, intervals, CLASSES)


def recording(name, end_s):
    """A stand-in conductance Timeseries giving the annotations their subject and extent."""
    result = Timeseries(name, np.zeros(2), [0., end_s])
    result.params[TSP_SUBJECT] = name
    return result


class SpanTests(unittest.TestCase):
    def test_prediction_spans_merge_overlapping_windows(self):
        # 60 s windows ending 150..330 cover (90, 330) as one disjoint region.
        spans = prediction_stress_spans(prediction("s", 60., [150., 210., 270., 330.]))
        self.assertEqual(spans, [(90., 330.)])

    def test_annotation_spans_follow_interval_starts(self):
        # The stress interval holds from its start to the end marker (added by
        # annotations_from_intervals), which restores unannotated time.
        spans = annotation_stress_spans(annotations(recording("s", 300.), [(0., 60., 0), (60., 120., 1)]))
        self.assertEqual(spans, [(60., 120.)])


class SampleScoringTests(unittest.TestCase):
    def test_partial_match_counts(self):
        # Reference stress (60, 240); hypothesis (90, 330): 150 shared seconds,
        # 30 missed at the start and 90 false after the reference ends.
        subject = prediction("s", 60., [150., 210., 270., 330.])
        scores = sample_scoring(subject, annotations(recording("s", 300.),
                                                     [(0., 60., 0), (60., 240., 1)]))
        self.assertEqual((scores["tp"], scores["fp"], scores["fn"]), (150, 90, 30))
        self.assertAlmostEqual(scores["sensitivity"], 150 / 180)
        self.assertAlmostEqual(scores["precision"], 150 / 240)
        self.assertAlmostEqual(scores["f1"], 2 * 150 / (150 + 90 + 180))
        # 90 false seconds over the 330 s scoring duration.
        self.assertAlmostEqual(scores["false_alarms_per_day"], 90 * 86400 / 330)

    def test_perfect_match(self):
        subject = prediction("s", 60., [120., 180., 240.])  # covers exactly (60, 240)
        scores = sample_scoring(subject, annotations(recording("s", 300.),
                                                     [(0., 60., 0), (60., 240., 1)]))
        self.assertEqual((scores["tp"], scores["fp"], scores["fn"]), (180, 0, 0))
        self.assertEqual(scores["sensitivity"], 1.)
        self.assertEqual(scores["precision"], 1.)
        self.assertEqual(scores["false_alarms_per_day"], 0.)

    def test_unannotated_time_is_background(self):
        # Predicted stress during the unannotated (120, 180) stretch is false, not skipped.
        subject = prediction("s", 60., [120., 180., 240., 300.])  # covers (60, 300)
        scores = sample_scoring(subject, annotations(recording("s", 300.),
                                                     [(0., 60., 0), (60., 120., 1), (180., 300., 0)]))
        self.assertEqual((scores["tp"], scores["fp"], scores["fn"]), (60, 180, 0))


class EventScoringTests(unittest.TestCase):
    intervals = [(0., 60., 0), (60., 180., 1)]  # stress (60, 180), unannotated afterwards

    def test_reference_event_detected(self):
        subject = prediction("s", 60., [180.])  # covers (120, 180), inside the event
        scores = event_scoring(subject, annotations(recording("s", 360.), self.intervals))
        self.assertEqual((scores["tp"], scores["fp"], scores["reference_events"]), (1, 0, 1))
        self.assertEqual(scores["sensitivity"], 1.)
        self.assertEqual(scores["precision"], 1.)

    def test_start_and_end_tolerances(self):
        # 15 s early is within toleranceStart (30 s); 20 s late is within toleranceEnd (60 s).
        early = prediction("s", 60., [150.])  # covers (90, 150), starts 30 s before the event
        scores = event_scoring(early, annotations(recording("s", 360.), self.intervals))
        self.assertEqual((scores["tp"], scores["fp"]), (1, 0))
        late = prediction("s", 60., [240.])  # covers (180, 240), ends 60 s after the event
        scores = event_scoring(late, annotations(recording("s", 360.), self.intervals))
        self.assertEqual((scores["tp"], scores["fp"]), (1, 0))
        # Starting exactly at the end of toleranceEnd overlaps nothing: false positive.
        beyond = prediction("s", 60., [300.])  # covers (240, 300)
        scores = event_scoring(beyond, annotations(recording("s", 360.), self.intervals))
        self.assertEqual((scores["tp"], scores["fp"]), (0, 1))

    def test_close_events_merge_into_one_detection(self):
        # Two stress runs separated by a 60 s gap merge (< 90 s); the merged
        # event overlaps the tolerated reference window, so nothing is a false
        # alarm. (60 s windows on 60 s ends only produce gap multiples of 60.)
        merged = prediction("s", 60., [150., 210., 330., 360.])
        scores = event_scoring(merged, annotations(recording("s", 360.), self.intervals))
        self.assertEqual((scores["tp"], scores["fp"]), (1, 0))
        # With a 120 s gap the second run stays separate and starts beyond the
        # reference tolerance (event end 180 + 60 s), so it counts as a false alarm.
        separate = prediction("s", 60., [150., 330., 360.])
        scores = event_scoring(separate, annotations(recording("s", 360.), self.intervals))
        self.assertEqual((scores["tp"], scores["fp"]), (1, 1))

    def test_long_events_split(self):
        # A 700 s reference event splits into 300 + 300 + 100; a hypothesis
        # covering all of it detects three events.
        intervals = [(0., 100., 0), (100., 800., 1)]
        ends = [160. + 60. * n for n in range(11)] + [800.]  # covers (100, 800)
        subject = prediction("s", 60., ends, ends=ends)
        scores = event_scoring(subject, annotations(recording("s", 900.), intervals))
        self.assertEqual(scores["reference_events"], 3)
        self.assertEqual((scores["tp"], scores["fp"]), (3, 0))
        self.assertEqual(scores["sensitivity"], 1.)

    def test_custom_parameters(self):
        # Tightening the tolerances makes a 40 s-late detection a false alarm.
        subject = prediction("s", 60., [240.])  # covers (180, 240), event ends at 180
        tight = EventParameters(tolerance_start_s=0., tolerance_end_s=0.,
                                min_duration_between_events_s=90.)
        scores = event_scoring(subject, annotations(recording("s", 360.), self.intervals), parameters=tight)
        self.assertEqual((scores["tp"], scores["fp"]), (0, 1))


class EvaluationTests(unittest.TestCase):
    def test_aggregation_over_recordings(self):
        # Recording A detects the first half of its event; recording B detects
        # the second half and adds 60 false seconds past the event. Missing
        # annotations skip recording C.
        predictions = {"a": prediction("a", 60., [120., 180.]),
                       "b": prediction("b", 60., [180., 240., 300.]),
                       "c": prediction("c", 60., [120.])}
        annotated = {"a": annotations(recording("a", 360.), [(0., 60., 0), (60., 240., 1)]),
                     "b": annotations(recording("b", 360.), [(0., 60., 0), (60., 240., 1)])}
        scores = evaluate_szcore(predictions, annotated)
        self.assertEqual(scores["recordings"], 2)
        self.assertEqual((scores["sample"]["tp"], scores["sample"]["fp"], scores["sample"]["fn"]),
                         (240, 60, 120))
        self.assertAlmostEqual(scores["sample"]["sensitivity"], 240 / 360)
        self.assertAlmostEqual(scores["sample"]["precision"], 240 / 300)
        # Both recordings' events are overlapped by their hypothesis: one detection each.
        self.assertEqual((scores["event"]["tp"], scores["event"]["fp"]), (2, 0))
        self.assertEqual(scores["event"]["sensitivity"], 1.)


if __name__ == "__main__":
    unittest.main()
