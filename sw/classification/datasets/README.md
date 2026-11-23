# Datasets

- `raw/` — original downloads, kept unmodified (git-ignored).
- `training/`, `test/`, `validation/` — prepared recordings and annotations as
  Timeseries pickles: `<name>.pkl` holds the data and `ann_<name>.pkl` the
  corresponding annotations, as produced by `raw_to_timeseries.py`.

Regenerate the public-data recordings with:

```sh
python -m sw.classification.datasets.download_physionet
python -m sw.classification.datasets.raw_to_timeseries physionet

python -m sw.classification.datasets.download_case
python -m sw.classification.datasets.raw_to_timeseries case [--arousal-threshold 5.0]
```

Prepare a GUI recording (for example for validation) with:

```sh
python -m sw.classification.datasets.raw_to_timeseries gui <recording.csv> --name <subject>
```

The `--name` value becomes the Timeseries name and the subject identifier in
`params`; annotations are stored as `ann_<name>.pkl`.

The agreed classification target is `stress` versus `not stress` (everything
else): MIST `control` intervals are labeled `not stress` and `stress` intervals
keep their class; unlabeled time remains `-1`.

## PhysioNet wearable-device-dataset subset

Source: Andrea Hongn, Facundo Bosch, Lara Prado and Paula Bonomini (2025),
*Wearable Device Dataset from Induced Stress and Structured Exercise Sessions
(version 1.0.1)*, PhysioNet, doi:[10.13026/he0v-tf17](https://doi.org/10.13026/he0v-tf17).
Related publication: *Scientific Data* 12 (2025),
doi:[10.1038/s41597-025-04845-9](https://doi.org/10.1038/s41597-025-04845-9).
License: Open Data Commons Attribution License v1.0; the license, README,
data-constraints notes and self-reported stress levels are downloaded together
with the signals and kept in `raw/physionet/`.

The subset contains the electrodermal activity and protocol tags of 14
protocol-v2 subjects. Training subjects: f01–f06, f08 and f09. Test subjects:
f10, f11, f15 and f16; f12 and f13 were dropped from the test set for heavy
artifact contamination. Subjects f07 and f14 are excluded following the
published data-constraints notes. Tag interpretation follows the authors'
`Wearable_Dataset.ipynb`: the first tag pair is the pre-task baseline
(class `not stress`) and the four subsequent pairs are the stress-induction
tasks (class `stress`); intermediate rest periods are left unannotated. These
are task-condition labels, not thresholds applied to the self-reported scores.
The classification target is `stress` versus `not stress` (everything else).

Note that this dataset distinguishes baseline from induced stress. It does not
contain the mild cognitive-load control condition of the original paper, so
models trained on it target stress versus not stress.

## CASE dataset

Source: Castellini, Schwenker, Sharma, van den Broek and Albu-Schaeffer (2019),
*CASE_Dataset-full*, Figshare, doi:[10.6084/m9.figshare.8869157](https://doi.org/10.6084/m9.figshare.8869157)
(the `CASE_full.zip` download). Data descriptor: K. Sharma, E. L. van den
Broek, A. Albu-Schaeffer, F. Schwenker and C. Castellini (2019), "A dataset of
continuous affect annotations and physiological signals for emotion analysis,"
*Scientific Data* 6, 196, doi:[10.1038/s41597-019-0209-0](https://doi.org/10.1038/s41597-019-0209-0).
License: CC0. The full download
(~4.55 GB, 30 participants) is kept in `raw/case/`; only the GSR (originally
1 kHz, µS) is used, decimated to 4 Hz, with the video ID retained as a
`video` channel.

CASE is an emotion-elicitation dataset: each participant watched eight videos
meant to evoke amusing, boring, relaxing or scary states, interleaved with
blue screens, while continuously reporting valence and arousal on a joystick
(scale 0.5–9.5). The agreed mapping to the binary target combines the
elicited emotion with self-report agreement (`Combined`):

- scary videos count as `stress`, boring and relaxing videos as `not stress`,
  but only when the segment-mean arousal agrees with the intended emotion
  (≥ 5.0, the scale midpoint, for stress; below it for not stress). The
  threshold is `--arousal-threshold` in `raw_to_timeseries.py case`;
- amusing videos are ambiguous and stay unannotated, as do the start/end
  videos and the blue screens.

Subjects 1–24 form the training set and subjects 25–30 the test set. With the
default threshold the resulting annotated time is roughly 136 min stress and
178 min not stress in training, 34 min stress and 48 min not stress in test.
These are emotion-elicitation labels: "stress" here means sustained
negative-arousal film content, which is closer to the acute-emotion framing
of Setz et al. than to the cognitive-load tasks of the PhysioNet subset.

## GUI recordings

CSV files exported by the HEEPidermis workspace GUI are converted by
`raw_to_timeseries.py gui`. The recording name is the subject identifier (for
example `Anna`), optionally with a session suffix. Labels are taken only from
explicit MIST `control start/end` and `stress start/end` events embedded in the
recording; the file name is never interpreted as a label. Recordings without
these events are prepared without annotations and can still be used for
inference.
