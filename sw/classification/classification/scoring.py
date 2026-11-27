"""SzCORE performance scoring: sample-based and event-based metrics.

Implements the scoring methodology of the SzCORE framework for validating
biomedical event-detection algorithms (ESL, EPFL,
https://eslweb.epfl.ch/epilepsybenchmarks/framework/, published in Epilepsia,
doi:10.1111/epi.18113). The reference implementation is the esl-epfl
`timescoring` package (MIT), whose semantics this module follows; it is
re-implemented here because that package requires Python >= 3.10 while the
project environments are Python 3.8.

Two complementary scorings turn a window-level prediction and the interval
annotations into counts of TP/FP/FN:

- Sample based scoring compares binary labels on a regular grid (1 Hz by
  default): every labeled second is a TP (both stress), FP (only hypothesis)
  or FN (only reference) sample. This is the fine-grained, ML-oriented view.
- Event based scoring works on contiguous events (maximal stress spans). A
  reference event is a TP when the hypothesis overlaps it at all (any overlap,
  optionally weighted by `min_overlap`), with tolerances around its start
  (30 s) and end (60 s) so that slightly offset detections still count.
  Hypothesis events separated by less than 90 s are merged first and events
  longer than 5 min are split, for both reference and hypothesis.

Both report sensitivity TP/(TP+FN), precision TP/(TP+FP), their F1 and the
false alarms per day FP/24h. Following SzCORE, accuracy and specificity are
deliberately not computed: non-event time dominates them and they poorly
reflect practical usefulness.

Differences with respect to the window-based metrics of evaluate_test:
scoring happens on the time axis, not per window (overlapping windows do not
multiply votes), and unannotated time (class -1) is treated as background, so
a stress prediction during unannotated time counts as a false alarm. The
SzCORE datasets are fully annotated; ours are not, hence this documented
difference with evaluate_test, which drops such windows instead.
"""

import numpy as np

from ..tools.ts_params import TSP_CLASSES, TSP_END_S, TSP_WINDOW_S

SAMPLE_RATE_HZ = 1.0  # SzCORE recommendation: labels every second.


class EventParameters:
    """Tolerances of event-based scoring; defaults follow the SzCORE framework."""

    def __init__(self, tolerance_start_s=30., tolerance_end_s=60., min_overlap=0.,
                 max_event_duration_s=300., min_duration_between_events_s=90.):
        self.tolerance_start_s = tolerance_start_s
        self.tolerance_end_s = tolerance_end_s
        self.min_overlap = min_overlap
        self.max_event_duration_s = max_event_duration_s
        self.min_duration_between_events_s = min_duration_between_events_s


def _class_index(classes, positive):
    for index, name in classes.items():
        if name == positive:
            return int(index)
    raise ValueError(f"Class '{positive}' is not among the annotated classes {dict(classes)}.")


def _merge_spans(spans, min_gap_s):
    """Union of sorted (start, end) spans, merging overlaps, touches and gaps
    shorter than min_gap_s (a min_gap_s of 0 only unions adjacent spans)."""
    merged = []
    for start, end in sorted(spans):
        if merged and (start <= merged[-1][1] or start - merged[-1][1] < min_gap_s):
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _split_spans(spans, max_duration_s):
    """Cut spans longer than max_duration_s into successive chunks."""
    result = []
    for start, end in spans:
        while end - start > max_duration_s:
            result.append((start, start + max_duration_s))
            start += max_duration_s
        result.append((start, end))
    return result


def _overlap(first, second):
    """Duration shared by two (start, end) spans."""
    return max(0., min(first[1], second[1]) - max(first[0], second[0]))


def prediction_stress_spans(prediction, positive="stress"):
    """Disjoint (start, end) spans of stress-predicted time.

    Prediction timestamps are window ends, so a stress-predicted window covers
    (end - window_s, end). Because windows are longer than the step between
    them (e.g. 60 s window, 15 s step), the spans of consecutive stress
    predictions overlap; their union is returned, so predicted regions never
    overlap each other. This is the same construction the plots draw.
    """
    if prediction is None or not len(prediction.time):
        return []
    index = _class_index(prediction.params[TSP_CLASSES], positive)
    window_s = prediction.params.get(TSP_WINDOW_S)
    if not window_s:
        raise ValueError("The prediction Timeseries must carry its window duration.")
    spans = [(end - window_s, end) for end, value in zip(prediction.time, prediction.data)
             if np.isfinite(value) and int(value) == index]
    return _merge_spans(spans, 0.)


def annotation_stress_spans(annotations, positive="stress"):
    """(start, end) spans of the annotated stress intervals (half-open)."""
    if annotations is None or not len(annotations.time):
        return []
    index = _class_index(annotations.params[TSP_CLASSES], positive)
    total = annotations.params.get(TSP_END_S, annotations.time[-1])
    spans = []
    for n, (start, value) in enumerate(zip(annotations.time, annotations.data)):
        end = annotations.time[n + 1] if n + 1 < len(annotations.time) else total
        if value == index:
            spans.append((float(start), float(end)))
    return _merge_spans(spans, 0.)


def _scoring_duration(annotations, hypothesis):
    """Recording duration: annotation coverage, extended to the last prediction."""
    reference_end = annotations.params.get(TSP_END_S, annotations.time[-1] if len(annotations.time) else 0.)
    return max(reference_end, max((end for _, end in hypothesis), default=0.))


def _metrics_from_counts(tp, fp, reference_events, duration_s):
    """SzCORE metrics: sensitivity, precision, F1 and false alarms per 24 h.

    F1 follows the reference implementation: 2*TP / (2*TP + FP + (ref - TP)),
    i.e. the harmonic mean of sensitivity and precision.
    """
    sensitivity = tp / reference_events if reference_events else float("nan")
    precision = tp / (tp + fp) if tp + fp else float("nan")
    f1 = 2 * tp / (2 * tp + fp + (reference_events - tp)) if reference_events + fp else float("nan")
    false_alarms_per_day = fp / (duration_s / 86400.) if duration_s > 0 else float("nan")
    return {"sensitivity": sensitivity, "precision": precision, "f1": f1,
            "false_alarms_per_day": false_alarms_per_day}


def _sample_counts(reference, hypothesis, rate_hz, duration_s):
    """TP/FP/FN sample counts on a regular grid of 1/rate_hz seconds."""
    samples = int(round(duration_s * rate_hz))
    reference_mask = np.zeros(samples, dtype=bool)
    hypothesis_mask = np.zeros(samples, dtype=bool)
    for spans, mask in ((reference, reference_mask), (hypothesis, hypothesis_mask)):
        for start, end in spans:
            mask[int(round(start * rate_hz)):int(round(end * rate_hz))] = True
    tp = int(np.sum(reference_mask & hypothesis_mask))
    fp = int(np.sum(~reference_mask & hypothesis_mask))
    fn = int(np.sum(reference_mask & ~hypothesis_mask))
    return tp, fp, fn


def sample_scoring(prediction, annotations, positive="stress", rate_hz=SAMPLE_RATE_HZ):
    """SzCORE sample-based scoring of one recording.

    Stress labels are sampled at `rate_hz` (1 Hz by default) from the
    annotated stress intervals and the predicted stress spans, and compared
    sample by sample. Unannotated time is background, so predictions there
    count as false positives (see module docstring).
    """
    reference = annotation_stress_spans(annotations, positive)
    hypothesis = prediction_stress_spans(prediction, positive)
    duration_s = _scoring_duration(annotations, hypothesis)
    tp, fp, fn = _sample_counts(reference, hypothesis, rate_hz, duration_s)
    scores = _metrics_from_counts(tp, fp, tp + fn, duration_s)
    return {"tp": tp, "fp": fp, "fn": fn, "duration_s": duration_s, "rate_hz": rate_hz, **scores}


def event_scoring(prediction, annotations, positive="stress", parameters=None):
    """SzCORE event-based scoring of one recording.

    Reference and hypothesis stress spans are merged (gaps below
    min_duration_between_events_s) and split (durations above
    max_event_duration_s); every reference event then overlapped by the
    hypothesis — within tolerance_start_s/tolerance_end_s of its bounds and
    beyond min_overlap relative coverage — is a true positive. Hypothesis
    events touching no detected reference event are false positives.
    """
    parameters = parameters or EventParameters()
    reference = annotation_stress_spans(annotations, positive)
    hypothesis = prediction_stress_spans(prediction, positive)
    duration_s = _scoring_duration(annotations, hypothesis)
    reference = _split_spans(_merge_spans(reference, parameters.min_duration_between_events_s),
                             parameters.max_event_duration_s)
    hypothesis = _split_spans(_merge_spans(hypothesis, parameters.min_duration_between_events_s),
                              parameters.max_event_duration_s)
    extended = [(max(0., start - parameters.tolerance_start_s),
                 min(duration_s, end + parameters.tolerance_end_s)) for start, end in reference]
    detected = []
    for span in extended:
        coverage = sum(_overlap(span, event) for event in hypothesis)
        if coverage / (span[1] - span[0]) > parameters.min_overlap + 1e-6:
            detected.append(span)
    tp = len(detected)
    fp = sum(1 for event in hypothesis if all(_overlap(event, span) == 0. for span in detected))
    scores = _metrics_from_counts(tp, fp, len(reference), duration_s)
    return {"tp": tp, "fp": fp, "reference_events": len(reference),
            "missed_events": len(reference) - tp, "duration_s": duration_s, **scores}


def evaluate_szcore(predictions, annotations, positive="stress", rate_hz=SAMPLE_RATE_HZ,
                    parameters=None):
    """SzCORE scores aggregated over the test recordings.

    Recordings without annotations are skipped, as in evaluate_test. Counts
    are summed across recordings before computing the metrics, so sensitivity
    weights every recording by its amount of annotated stress and the false
    alarm rate by its duration. Returns {"sample": ..., "event": ...} with the
    counts of sample_scoring/event_scoring and a "recordings" total.
    """
    totals = {"sample": {"tp": 0, "fp": 0, "fn": 0},
              "event": {"tp": 0, "fp": 0, "reference_events": 0}}
    duration_s, scored = 0., 0
    for name, prediction in predictions.items():
        recording_annotations = annotations.get(name)
        if recording_annotations is None:
            continue
        sample = sample_scoring(prediction, recording_annotations, positive, rate_hz)
        event = event_scoring(prediction, recording_annotations, positive, parameters)
        scored += 1
        duration_s += sample["duration_s"]
        for key in ("tp", "fp", "fn"):
            totals["sample"][key] += sample[key]
        for key in ("tp", "fp", "reference_events"):
            totals["event"][key] += event[key]
    sample = _metrics_from_counts(totals["sample"]["tp"], totals["sample"]["fp"],
                                  totals["sample"]["tp"] + totals["sample"]["fn"], duration_s)
    event = _metrics_from_counts(totals["event"]["tp"], totals["event"]["fp"],
                                 totals["event"]["reference_events"], duration_s)
    return {"recordings": scored, "duration_s": duration_s,
            "sample": {**totals["sample"], **sample},
            "event": {**totals["event"], "missed_events":
                      totals["event"]["reference_events"] - totals["event"]["tp"], **event}}
