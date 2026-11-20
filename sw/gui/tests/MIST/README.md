# MIST — Montreal Imaging Stress Task

Mental arithmetic with individualized time pressure and performance feedback,
styled like the Stroop test. Select **Tests → MIST** in the measurement GUI,
or run from the repository root:

```bash
python3 sw/gui/tests/run_test.py mist
python3 sw/gui/tests/run_test.py mist --response-mode voice
python3 sw/gui/tests/run_test.py mist --config my-study.hjson --save outs/mist.json
# Short stress-only session, without practice:
python3 sw/gui/tests/run_test.py mist --practice-time 0 --rest-time 0 --block-order stress --test-time 30
```

## Task and controls

Every problem has an integer answer **0–9**. Press its digit (number row or
keypad), or say its English name in **voice** mode. No Enter is needed. The
keyboard remains available in voice mode. Multiplication/division precede
addition/subtraction; parentheses make grouping explicit. Five difficulty
levels range from one-digit addition/subtraction to multiplication and exact
division, with up to four operands and displayed numbers no larger than 99.

**SPACE** begins the session and also silently skips the current section
(voice check, practice, rest, or a whole block — including mid-problem).
**ESC** ends it and preserves collected results.
The summary stays visible until ESC, or SPACE starts a fresh run.

Default sequence (380 seconds, plus instructions and optional voice check):

1. **Voice check**, in voice mode when `voice_calibration` is enabled: say
   zero through nine. Each digit has `calibration_time` seconds once the mic
   is ready. **SPACE** skips the check. Loading/errors are displayed, with
   keyboard fallback available. This checks recognition; it does not train
   Vosk. It also enrolls the participant's voice for noise rejection
   (see voice isolation below).
2. **Practice**, 120 s: no per-answer deadline, clock sound, or performance
   comparison. Correct-answer response times set the initial stress limit to
   `time_factor × mean correct RT` (default 90%), within configured bounds.
   **SPACE** also ends practice early at any time (including mid-problem) and
   calibrates from the answers collected so far. If practice is skipped or
   has no correct answers, `response_time` is used.
3. **Rest**, 10 s: fixation cross, no problems or audio.
4. **Control**, 120 s: arithmetic and visual feedback without per-answer
   deadlines, comparison, or audio pressure.
5. **Rest**, 10 s, then **stress**, 120 s: shrinking response-time bar, block
   countdown, accuracy versus the configured benchmark, accelerating clock,
   loud “meeeh” on wrong answers/timeouts, quiet cling and green check/smiley
   on correct answers.

`block_order` accepts space-separated `control` / `stress` names in any order;
`repetitions` repeats that sequence. Each block lasts `test_time` seconds;
practice and rests are additional. `practice_time: 0` skips practice.

In stress blocks, `adaptation_streak` correct answers (default 3) tighten the
next limit to `time_factor × mean RT of that streak`, never increasing it.
The same number of consecutive wrong answers/timeouts increases the limit
by `recovery_factor` (default 1.1). Limits remain between `response_min` and
`response_max`; a current trial's deadline never changes. `adaptive_timing`
disables streak adjustments, while practice calibration still applies.

## Configuration and shared code

Every field in [`configs/defaults.hjson`](configs/defaults.hjson) is editable
in the GUI and exposed as a CLI flag. **Load… / Save as…** use file dialogs;
workspace configuration/CSV saves retain the selected test, file path and
all form values. GUI voice options activate when **Response mode = voice**.
The same `spec.py` contract and configuration precedence as Stroop apply.

Both tests reuse `common/base.py`, `events.py`, `settings.py`, `audio.py`,
and `voice.py`. Their check/cross/smiley artwork is shared in
`common/visuals.py`. No MIST implementation imports Stroop internals.

Voice uses the existing optional `vosk` + `sounddevice` dependencies and
English model (auto-managed unless `vosk_model` is set). Sound uses the same
system player selection as Stroop. `sounds: false` silences stress audio;
`tick: false` disables only the clock. Actual loudness follows system volume.

**Voice isolation from background noise**: when `speaker_filter` is on, the
microphone check doubles as voice enrollment — the test averages the speaker
vectors (Vosk x-vectors, model downloaded automatically once) of the digits
it recognized from the participant. During the task, a recognized digit is
only accepted if its voice similarity to that profile (cosine) is at least
`speaker_threshold`; anything else — another person, a TV, noise that happens
to decode as a digit — is dropped. Without the speaker model, an older vosk,
a skipped calibration, or no recognized calibration words, recognition simply
behaves as before. The keyboard fallback is never filtered. Lower
`speaker_threshold` to 0.2 in very noisy rooms; raise it towards 0.5 if
stray voices still get through.

## Events and timing

Standard schema-v2 colored markers are printed to the terminal and written
to `sw/gui/outs/tests/events.jsonl` (or `--events PATH`). Run and section
start/stop markers are yellow and name the test and the part (`MIST start`,
`MIST end`, `MIST aborted`, `calibration start/end`, `rest start/end`,
`practice/control/stress start/end`); other phase markers are white;
correct is green; wrong/timeout is red. Markers include:

- Instructions; voice calibration start/stimulus/result/end; voice errors.
- `MIST start`/`MIST end`/`MIST aborted`; practice/control/stress start/end;
  rest start/end.
- Pace calibration and every adaptive deadline change.
- `stimulus`, `correct`, `wrong`, `timeout`, feedback start/end.
- `trial interrupted` for unfinished problems at a block boundary or abort.
  These are excluded from accuracy rather than counted as wrong answers.

Trial details include block, condition, difficulty, expression, correct
answer, response, input source, onset, deadline, RT, outcome, and next time
limit. Voice verdict timestamps use the recognizer's audio-capture timestamp;
`voice_grace` allows delivery after a deadline but never accepts speech
captured after it. `min_voice_lag` rejects carry-over speech. Onsets are Tk
software drawing timestamps, not hardware display-synchronization signals.

`--save` includes all trials, interrupted trials, markers, configuration and
separate practice/control/stress summaries. Rest has phase markers but no
scored trials. Summary time excludes waiting on instructions/voice check and
the final results screen.

## Protocol reference

Based on [Dedovic et al. (2005), *The Montreal Imaging Stress Task*](https://pmc.ncbi.nlm.nih.gov/articles/PMC1197276/).
This repository implements a desktop adaptation: direct digit/voice answers,
fixed selectable arithmetic difficulty, practice-calibrated adaptive timing,
and Stroop-style sounds/visuals. The comparison bar is an **experimenter-set
expected accuracy**, not a measured population average. Control blocks use
the same arithmetic difficulty but are not trial-frequency-yoked to stress.
Scanner triggering and investigator-delivered social evaluation from the
published protocol are not automated here.

Run checks from `sw/gui`:

```bash
PYTHONPATH=..:../.. python3 -m unittest gui.tests.MIST.test_mist
```
