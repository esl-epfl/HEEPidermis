# AGENTS.md — Standard for tests in `sw/gui/tests/`

This folder holds the interactive tests offered by the measurement GUI's
**Tests** device. The GUI discovers every subfolder here automatically: adding
a test never requires a GUI change. Follow this standard exactly.

## Folder layout

```
sw/gui/tests/
├── AGENTS.md          ← this standard
├── run_test.py        ← CLI runner (one subcommand per implemented test)
├── common/            ← shared scaffolding (do not edit per test)
│   ├── base.py        ← CognitiveTest / TestConfig, the mark() API
│   ├── events.py      ← EventPublisher (JSONL feed + terminal echo)
│   ├── settings.py    ← HJSON settings loader (config_kwargs)
│   ├── audio.py       ← synthesized feedback sounds (no assets)
│   └── voice.py       ← Vosk keyword spotting (lazy imports)
├── Stroop/            ← one folder = one test (capitalized name)
│   ├── __init__.py    ← re-exports the spec contract
│   ├── spec.py        ← THE GUI CONTRACT (see below)
│   ├── test.py        ← exposes Test (what the GUI/runner launch)
│   ├── configs/       ← defaults.hjson + user-saved configurations
│   └── ...            ← implementation modules, unit tests
├── MIST/              ← placeholder folder (README only): shows in the GUI,
└── SSST/                selection reports "No such test was found"
```

Discovery rules (`gui/tests/__init__.py`):

* Every subdirectory not named `common` or `__pycache__` (and not starting
  with `.` or `_`) is offered in the GUI dropdown, sorted by folder name.
* A folder **with** `spec.py` is implemented: the GUI reads its contract
  below. A folder **without** `spec.py` is a placeholder: it appears in the
  dropdown but its form shows "No such test was found." and Open does nothing.
* `NAME` in `spec.py` is the CLI/test key (lowercase, e.g. `stroop`); the
  folder name is the human title. Keep keys unique across the folder.

## `spec.py` — the contract (must stay import-light)

`spec.py` may import only stdlib + `common.base` (`TestConfig`). No Tk, no
audio, no optional dependencies: the GUI imports it on every start.

| Export        | Meaning                                                          |
|---------------|------------------------------------------------------------------|
| `NAME`        | Unique test key for CLI flags and event feeds (`"stroop"`).      |
| `TITLE`       | Dropdown label (`"Stroop"`).                                     |
| `DESCRIPTION` | One or two sentences shown in the GUI.                           |
| `Config`      | The dataclass the GUI/runner instantiate (fields = parameters).  |
| `FORM`        | Tuple of field specs rendering the configuration menu.           |
| `COLOR_POOL`  | *(optional)* palette constants shared by form and task.          |

`Config` field rules: only `int` / `float` / `bool` / `str` (plus `None`
defaults for optional values). Every field becomes a `--flag` of
`run_test.py` (`n_colors` → `--n-colors`) and is settable from
`configs/defaults.hjson`. Put full validation in `__post_init__` — the CLI,
the GUI form and the HJSON files all funnel through it.

`FORM` entry kinds (rendered top to bottom):

```python
{"section": "Pressure and adaptation"}     # visual separator (no key/widget)
{"key": "test_time",  "widget": "slider",   "min": 10, "max": 600, "step": 5,
 "label": "Test duration (s)"}
{"key": "response_mode", "widget": "dropdown", "options": ("keys", "voice"),
 "label": "Response mode"}
{"key": "sounds",     "widget": "checkbox", "label": "Feedback sounds"}
{"key": "score_target", "widget": "entry",  "label": "Score target"}   # free text
```

* `slider` → Spinbox; value must satisfy the field type (int/float).
* `dropdown` → readonly Combobox over `options` (strings).
* `checkbox` → Checkbutton mapping onto a bool field; **the Checkbutton
  carries its own `label`** so the box and its description always share one
  line (do not render a separate label above it).
* `entry` → free text; an **empty** entry keeps the default. Tuples (e.g. a
  color pool) are space-separated names; `None` fields show empty.
* A `{"section": ...}` separator may carry an **`"enabled_by"`** rule, e.g.
  `{"section": "Voice", "enabled_by": {"key": "response_mode",
  "values": ("voice",)}}`: the panel greys the section's widgets out (and
  re-enables them live) unless the controlling key holds one of the listed
  values. Use it for options that only exist in some modes — settings that
  are silently ignored otherwise (like a voice calibration in keys mode).
* **`FORM` must list every configurable field of `Config`** (optionally
  grouped with `{"section": ...}` separators), because the GUI can save and
  restore the complete configuration. Truly internal helpers (e.g. derived
  override dicts) may be omitted.
* The panel starts the form from the test's own defaults
  (`configs/defaults.hjson`) and sends only values that differ from them, as
  `--flag value`. Anything not in `FORM` is still reachable through the
  panel's "Launch options" free-text field (raw extra CLI flags).

## `test.py` — the runnable entry point

`test.py` exposes `Test`, the class launched by the GUI and `run_test.py`
(normally re-exported from an implementation module):

```python
from .stress import StroopStressTest as Test
```

Implementation notes:

* Derive from `common.base.CognitiveTest`; one instance = one run window.
* Call `self.mark(label, at=..., color=..., **details)` for every phase or
  outcome (see event standard below). Timestamps come from `time.monotonic()`;
  pass `at=` for events that happened earlier (e.g. stimulus onset).
* `run()` blocks until the window closes; `Escape` aborts but still returns
  the trials collected so far.
* Keep optional dependencies (sounddevice, vosk) imported **lazily**, inside
  the code paths that need them.

## Event standard (schema version 2)

Every test publishes one JSON line per event through
`common.events.EventPublisher`. Each line is **both** appended to the JSONL
feed (default `sw/gui/outs/tests/events.jsonl`; override with
`--events PATH` or the `HEEPIDERMIS_TESTS_EVENTS` env var) **and** printed to
the terminal, so a run is observable without the GUI.

```json
{"schema_version": 2, "run_id": "stroop-2026...", "test": "stroop",
 "sequence": 7, "label": "correct", "color": "green",
 "monotonic_s": 12345.678, "unix_s": 1764000000.123,
 "details": {"trial": 3, "rt": 0.812, "ink": "red"}}
```

* `label` — free-form, ≤ 64 chars (e.g. `test start`, `correct`, `stimulus`).
* `color` — part of the format: a palette name (`white green red yellow blue
  orange purple cyan`) or a `#rrggbb` hex string. Convention: **verdicts are
  green (correct) / red (wrong or timeout); every informational phase is
  white**. Unknown names fall back to white in the GUI.
* `sequence` — 0-based within `run_id`; the GUI deduplicates on
  (`run_id`, `sequence`).
* `monotonic_s` / `unix_s` — both required; the GUI renders relative to its
  workspace clock and ignores runs older than the workspace.
* Schema version 1 feeds (no `color`) still load and render as white.

The GUI turns these into colored labeled markers on every plot and stores
them in the workspace CSV (device key `tests`, `event_label`/`event_color`
columns).

## `configs/` — configuration files

Ship one `configs/defaults.hjson` per test folder, one section named after
`NAME` (plus an optional `common` section). Every parameter with a short
comment; HJSON allows comments and trailing commas. Precedence: built-in
`Config` defaults < HJSON file < explicit `--flag`.

The GUI's Tests panel uses file explorers for user configurations (same
pattern as the workspace's "Load configuration…" / "Save configuration…"):

* **Load…** opens a file explorer starting in the test's `configs/` folder
  and accepts any `.hjson` on disk.  It resets the form to the test's
  defaults and then applies the file's values, so a file only needs to list
  the parameters it changes, and reports how many values changed next to the
  row.  Hand-written files are accepted in any of these layouts:

  ```hjson
  stroop: { test_time: 30 }          // standard: section named after NAME
  myVariant: { test_time: 30 }       // a single section with any name
  test_time: 30                      // bare keys, no section at all
  ```

  (with an optional `common` section like `defaults.hjson`).
* **Save as…** opens a save dialog (which asks before overwriting) and
  writes the current form values via
  `common.settings.save_settings(path, NAME, values)`.
* The panel's configuration snapshot includes the loaded configuration file
  (`config_file`) next to the form `values`, and both are saved with the
  workspace configuration and workspace CSVs and restored on load.

The CLI runner takes `--config FILE` (default `configs/defaults.hjson`);
because the form's starting point is that same file, GUI launches and CLI
launches behave identically.

## Checklist for a new test

1. `mkdir sw/gui/tests/<Name>`; write `spec.py` (contract above).
2. Implement the task; expose it as `Test` in `test.py`.
3. Add `configs/defaults.hjson` (and a `README.md` for users).
4. Emit colored `mark()` events for phases and verdicts.
5. Add unit tests in the folder (`test_*.py`); they run under
   `python3 -m unittest discover` from `sw/gui` (import as
   `gui.tests.<Name>...`).
6. Nothing else: the GUI dropdown, form, configuration load/save, CSV format
   and markers are generic.
