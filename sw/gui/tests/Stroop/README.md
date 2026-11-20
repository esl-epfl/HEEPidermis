# Stroop test (adaptive time pressure)

Color-word interference under stress: a color word (e.g. `red`) appears in an
ink color that may or may not match it; press the number key of the **ink**
color as fast as possible. The GUI's **Tests** device runs this test; it can
also run standalone.

The configuration dataclasses and the GUI form live in [`spec.py`](spec.py)
(the GUI contract — see `../AGENTS.md`); [`stroop.py`](stroop.py) is the plain
task core and [`stress.py`](stress.py) adds the score, sounds and adaptive
pressure. [`test.py`](test.py) exposes the runnable `Test`.

## Run (from the repository root)

    python3 sw/gui/tests/run_test.py stroop --n-colors 4 --rest-time 10 --test-time 60
    python3 sw/gui/tests/run_test.py stroop --save outs/stroop_001.json

Or with the workspace shortcut (see `sw/gui/README.md`):

    make stroop          # TEST_ARGS passes extra run_test.py options

`SPACE` begins each phase; number keys `1..n` answer; `Escape` aborts and
still reports the trials collected so far.

## Pressure and scoring

* Wrong answer → loud "meeeeh" buzzer and a big red cross; right answer →
  short cling, green checkmark and a smiley; timeout → "HURRY UP!".
* A score bar fills toward a fixed target (`score_target`, 50,000), red below
  halfway and green above. Correct answers add `max(correct_min_gain,
  bonus - RT_ms)` where `bonus` defaults to `timeout_penalty_ms` (4,000);
  `correct_bonus_ms` overrides it. Wrong answers deduct `RT_ms`; timeouts
  deduct `timeout_penalty_ms`.
* With `adaptive_timing: true` (the default), a smoothed score gently adjusts
  the next response window (`stimulus_min`..`stimulus_max`, ≤ 3% per trial)
  and feedback duration (`feedback_min`..`feedback_max`), aiming for the
  50% ± `score_band` region rather than a hard cap. `--adaptive-timing false`
  fixes both (the gap then decays by `isi_decay`).
* The remaining test time runs big in the top-left corner over an
  accelerating clock tick. `--sounds false` mutes everything, `--tick false`
  only the clock. Sounds are synthesized at runtime (stdlib only); without a
  system player the test runs silently.

## Configuration

Every config field is a `--flag` (`n_colors` → `--n-colors`); all parameters
with short descriptions also live in the editable HJSON file
[`configs/defaults.hjson`](configs/defaults.hjson) (section `stroop`; `hjson`
must be installed: `pip install hjson`). Values there are the defaults; an
explicit `--flag` overrides the file, and `--config other.hjson` uses a
different file. In the GUI, the Tests panel generates the whole form from
[`spec.py`](spec.py) — every parameter is modifiable there, and the
**Configuration** row uses file explorers: **Load…** applies any `.hjson`
on disk (the explorer starts in [`configs/`](configs)), **Save as…** writes
the current form values to a file you pick. Anything not in the form goes
through the panel's "Launch options" field as raw flags.

## Event feed

Every run publishes the standard colored event feed (see `../AGENTS.md`) to
`sw/gui/outs/tests/events.jsonl` (`--events PATH` or
`HEEPIDERMIS_TESTS_EVENTS` to override) and echoes each event to the
terminal. Labels: `instructions`, `calibration`, `calibration end`, `rest`,
`rest end`, `test start`, `stimulus`, `correct` (green), `wrong` (red),
`timeout` (red), `test aborted` (red) and `test end` — the GUI draws them as
colored markers aligned with the HEEPidermis/BIOPAC measurements.

## Voice input (keyword spotting)

`--response-mode voice` replaces the keyboard with an offline Vosk keyword
spotter: say the color out loud; the reaction clock stops at the moment the
word is heard, and the judgment lands right after you finish saying it (the
mic going quiet for `--voice-quiet-s` ends the word; `--voice-max-wait` caps
the wait). The mic is only processed while a word is on screen and
unanswered, so nothing said between words can leak into a trial; hits faster
than `min_voice_lag` after a word appears are dropped as carried over.
Voice mode starts with a calibration screen (say each color once; `S` skips).
If voice is unavailable the test falls back to the keyboard with a note.

Optional dependencies (`pip install vosk sounddevice`, formerly
`requirements-voice.txt`): the small English Vosk model (~40 MB) downloads
automatically on first use into `models/` (gitignored). `--vosk-model` points
at a local model, `--mic-device <index>` picks an input device.

## Unit tests

    cd sw/gui && PYTHONPATH=..:../.. python3 -m unittest tests.Stroop.test_adaptive tests.Stroop.test_timing
