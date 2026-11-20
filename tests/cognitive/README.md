# Cognitive tests

Interactive psychological tests rendered with Tk. They are self-contained:
no acquisition hardware is touched, so they run standalone and can later be
embedded in the GUI in `sw/gui`.

## Run standalone (from the repository root)

    python tests/run_test.py stroop
    python tests/run_test.py stroop --n-colors 4 --rest-time 10 --test-time 60 \
        --stimulus-time 4.0 --isi 0.5 --congruent-ratio 0.25 --seed 42
    python tests/run_test.py stroop --save outs/stroop_001.json

Or with the workspace shortcuts (see `sw/gui/README.md`):

    make stroop          # STROOP_ARGS passes extra run_test.py options
    make stroop-stress

## Event feed for the measurement GUI

Every run also publishes an append-only JSONL feed for the multi-device GUI,
which draws each event as a labeled vertical line aligned with the
HEEPidermis/BIOPAC measurements (verdicts colored — green for `correct`, red
for `wrong`/`timeout` — and every other phase white):

    sw/gui/outs/stroop/events.jsonl

Override the location with `--events PATH` or `HEEPIDERMIS_STROOP_EVENTS`.
Each line is one marker: `schema_version`, `run_id` (fresh per run/restart),
`test`, `sequence`, `label`, `monotonic_s`, `unix_s` and `details`. Labels are
`instructions`, `calibration`, `calibration end`, `rest`, `rest end`,
`test start`, `stimulus`, `correct`, `wrong`, `timeout`, `test aborted` and
`test end`. Timestamps are taken when the phase happened — a spoken answer is
stamped at the moment the word was heard, not when feedback appeared. The GUI
listener tolerates late delivery, duplicate lines, partial lines and feed
rotation, and old events never leak into a new workspace session.

## Stressful variant

    python tests/run_test.py stroop-stress --test-time 120

Same task plus a buzzer and red cross on failure, a quiet cling and green
checkmark with a smiley on success, and "HURRY UP!" on timeout. Symbols and
sounds start together. A score bar starts halfway toward a fixed target,
red below 50% and green at/above it. Response windows and feedback duration
adapt gradually to score and recent point trends; see **Adaptive stress
mode** below for the controls.

Correct answers add `max(correct_min_gain, bonus - RT_ms)`, where `bonus`
defaults to `timeout_penalty_ms` (4,000); `correct_bonus_ms` overrides it.
Wrong answers deduct `RT_ms`; timeouts deduct `timeout_penalty_ms`. Points
gained/lost appear beside the feedback symbol. The remaining session time
is shown at the top left, with a continuous background clock accelerating
from 1 Hz to 3 Hz. Use `--sounds false` to mute everything or `--tick false`
to disable only the clock.

Sounds are synthesized at runtime (stdlib only) and played with whatever
system player exists (paplay / aplay / ffplay); without one, the test runs
silently.  Escape also stops any playing sound.

Every config field is exposed as a `--flag`; `--fullscreen false` runs in a
window instead of fullscreen. `Escape` aborts and still reports the trials
collected so far.

All parameters (each with a short description) also live in the editable
HJSON file [`config.hjson`](config.hjson) in this folder, organized as a
`common` section plus one section per test. Values there are the defaults;
an explicit `--flag` overrides the file, and `--config other.hjson` uses a
different file entirely (`hjson` must be installed: `pip install hjson`).
The file supports comments, unquoted strings and optional commas, so it can
be edited freely during an experiment day; unknown parameter names are
rejected with a message listing the valid ones.

    python tests/run_test.py stroop --config my_experiment.hjson

In voice mode, `--voice-debug true` (or `voice_debug: true` in the file)
prints everything the recognizer hears — partials, finals, what reaches the
test, and what gets dropped — to the terminal.

Voice input gate: recognition is enabled only while a word is on screen and
unanswered.  Before each word the input buffer is wiped, so nothing said in
between can leak into the next trial, and the first recognized word pauses
the input until the next word appears.  The recognizer itself is rebuilt
whenever listening resumes, so a half-finished utterance from before the
pause cannot re-emerge as a phantom answer; hits faster than
`min_voice_lag` (0.05 s by default, `--min-voice-lag`) after the word
appears are additionally dropped as carried over.

Accepting a spoken answer immediately cancels that word's timeout, including
while waiting for speech to end. Each displayed stimulus gets one verdict
and one feedback sound; the next word starts a fresh timeout. Debug output
marks acceptance with `timeout cancelled`.

## Voice input (keyword spotting)

Both Stroop variants accept `--response-mode voice`: say the color out loud
instead of pressing keys. An offline keyword spotter (Vosk) listens for the
color names only; the reaction clock stops at the moment the word is heard
(recognition latency never penalizes the participant), the judgment appears
right after you finish saying the word — the mic going quiet for
`--voice-quiet-s` 0.15 s ends the word immediately, with the recognizer's own
silence endpoint and a `--voice-max-wait` 0.5 s cap as fallbacks — and
recognition is blocked whenever no
color is on screen.  The word that was heard is shown under the legend (in
its own color) and its legend swatch is highlighted from the moment it is
accepted, on both variants.  The microphone is only processed during
calibration and
while a word is on screen and unanswered: on the instructions, rest-break and
summary screens (and after each answer) incoming audio is discarded without
being recognized at all, so nothing said there can reach the test. While the
voice input loads (or fails), its status is
shown above the color legend so it never fails silently. The last answered
color — spoken or pressed — is highlighted in the legend with a thick white
halo while its check/cross is up.

**Check your microphone first:**

    python tests/run_test.py voice-test

A listen-only screen: mic level bar, live partial recognition text, and the
log of recognized words with timestamps (`C` clears it). If your words show
up there, the real tests will hear them too.

Voice mode starts with a calibration screen — say each color once, watch the
✓ marks and the mic level bar (`S` skips it, `+` / `-` change how many
colors are listened for). When a test run ends, a stats screen appears:
SPACE restarts the whole test from the beginning, ESC quits. If voice is
unavailable, the test falls back to the keyboard with a note on screen.

Optional dependencies (`pip install -r tests/cognitive/requirements-voice.txt`):

    vosk>=0.3.45
    sounddevice>=0.4.6

The small English Vosk model (~40 MB) is downloaded automatically on first
use into `tests/cognitive/models/` (gitignored). Point `--vosk-model` at a
local model directory to skip the download, and `--mic-device <index>` to
pick a specific input device (see `python -c "import sounddevice;
print(sounddevice.query_devices())"`). Extra flags: `--voice-calibration
false`, and the keyboard always keeps working as a fallback.

## Adaptive stress mode

The stress variant shows points toward a fixed `score_target` (50,000 by
default), starting at `score_start` (25,000). The bar has a 50% marker:
red below halfway, green at or above it. Scores remain genuine, can go
negative or exceed the target, and are not clamped; only the drawn fill is
limited to the bar's width.

With `adaptive_timing: true`, a smoothed score gently adjusts the **next**
word's response window. Pressure is mild within 50% +/- `score_band`
(default 30–70%) and increases toward 80–90%. Low scores gradually ease the
pace. A smoothed point-gain/loss trend anticipates overshoot and recovery,
so a declining score can ease pressure before reaching the bottom. This aims
for a challenging middle region rather than guaranteeing a particular score.

- `stimulus_time` is the initial deadline, bounded by `stimulus_min` and
  `stimulus_max` (0.75–4 seconds). `stimulus_step` caps changes at 3% per trial.
- `feedback_time` starts at 0.5 seconds, bounded by `feedback_min` and
  `feedback_max` (0.35–0.65 seconds); `feedback_step` caps changes at 10 ms.
- `adaptation_smoothing` controls how quickly the controller follows score
  changes (0.25 by default). `adaptation_lookahead` projects the recent point
  trend 15 trials ahead to damp oscillation (0 disables anticipation).
  Each trial's feedback duration is frozen when
  its word appears, so neither recognition nor drawing can change it mid-trial.
- The between-word gap follows the same adjustment between `isi_min` and
  `isi_start`. The independent `isi_decay` is used only with adaptation off.
- Symbols and sounds start together. The next word waits for the preceding
  feedback sound to finish, even if the symbol has already disappeared.

All knobs are in `config.hjson` and available as CLI flags, for example:

    python tests/run_test.py stroop-stress --score-target 50000 --stimulus-step 0.02

`--adaptive-timing false` uses a fixed response window and fixed feedback
duration. Debug output (`voice_debug`) includes score percentage and timing
changes. Trial events save `stimulus_time_s`, `feedback_time_s`, `isi_s` and
their `next_...` values alongside the score and controller pressure. ISI is
the scheduled gap; sound playback can extend the actual interval. Accepted
answers still immediately cancel the per-word timeout.

## Adding a new test

1. Create `tests/cognitive/<name>.py` with a `<Name>Config(TestConfig)`
   dataclass and a `<Name>Test(CognitiveTest)` class (see `stroop.py`).
2. Register it in the `TESTS` dict in `tests/cognitive/__init__.py`.

It then works with `tests/run_test.py <name> ...` automatically (the CLI is
generated from the config dataclass) and is ready for GUI integration.

## GUI integration contract (for later, in `sw/gui`)

* **Parameters**: a plain dataclass of `int/float/bool/str` fields
  (`TestConfig` subclasses) — trivially mapped to a settings form, and
  JSON-round-trippable via `to_dict()` / `from_dict()`.
* **Embedding**: `test.run(parent=widget, ...)` renders in a `Toplevel` on
  top of the GUI instead of creating its own `Tk`; pass `on_finish=result_callback`
  and optionally `on_event=event_callback` for live trial streaming.
* **Markers**: `test.mark(label, at=..., **details)` publishes a timestamped
  timeline event (`TestResult.timeline`) and, during `run()`, appends it to
  the JSONL feed consumed by the `sw/gui` Stroop device. `on_marker` allows a
  live sink; a failing sink never breaks the test.
* **Results**: `TestResult` (test name, config, per-trial events, summary,
  timing) is JSON-serializable via `to_dict()` / `to_json()` / `save(path)`,
  so it can be stored next to a session CSV in `sw/gui/outs/` and rendered
  in a report tab, following the same pattern as the device panels.
* **Registry**: `tests.cognitive.TESTS` maps names to test classes so the
  GUI can offer every test without importing each one.
