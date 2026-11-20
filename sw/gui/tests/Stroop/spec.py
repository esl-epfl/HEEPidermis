"""Stroop test contract: metadata, configuration dataclasses, and the form.

This module is the integration contract with the GUI: the Tests panel reads
``NAME``, ``TITLE``, ``DESCRIPTION``, ``Config`` and ``FORM`` from here to
build its configuration menu, and turns the form values into command-line
flags for ``run_test.py``.  It must stay import-light (no test window, no
audio, no optional dependencies) — see ``sw/gui/tests/AGENTS.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..common.base import TestConfig


NAME = "stroop"
TITLE = "Stroop"
DESCRIPTION = ("Color-word interference under adaptive time pressure: press the "
               "number key of the ink color. Verdicts feed the plots as "
               "green (correct) / red (wrong, timeout) / white (phases).")

# Color pool, in default preference order; ``n_colors`` takes the first N.
COLOR_POOL = {
    "red": "#e04040",
    "green": "#40c060",
    "blue": "#4070e8",
    "yellow": "#e8c830",
    "purple": "#a050d0",
    "orange": "#f08020",
    "cyan": "#30b8c8",
    "pink": "#f070b0",
    "brown": "#9c6b30",
    "gray": "#a0a0a0",
}


@dataclass
class StroopConfig(TestConfig):
    """Parameters of one Stroop run.

    ``n_colors``       how many colors from the pool are used (2..10).
    ``rest_time``      countdown seconds before the test phase starts.
    ``test_time``      duration of the test phase in seconds.
    ``stimulus_time``  max seconds a word stays on screen; 0 = until response.
    ``congruent_ratio`` probability that the word matches its ink color.
    ``seed``           RNG seed for reproducible trial sequences; 0 = random.
    ``color_pool``     subset of COLOR_POOL names to draw colors from.
    ``response_mode``  "keys" (number keys) or "voice" (keyword spotting).
    ``voice_calibration`` say each color once before the test (voice mode).
    ``mic_device``     sounddevice input device index; -1 = system default.
    ``vosk_model``     path to a local Vosk model; empty = auto-managed.
    """

    n_colors: int = 9
    stimulus_time: float = 2.0
    isi: float = 0.5
    congruent_ratio: float = 0.25
    seed: int = 0
    color_pool: tuple = tuple(COLOR_POOL)
    # Optional name -> hex overrides on top of the built-in palette (set
    # automatically when color_pool is written as {name: hex} in the
    # configuration file).
    color_hexes: dict | None = None
    show_legend: bool = True
    response_mode: str = "keys"
    voice_calibration: bool = True
    mic_device: int = -1
    vosk_model: str = ""
    # After a spoken answer is recognized, the judgment waits until the
    # speaker is done: either the recognizer's silence endpoint, the mic
    # going quiet for `voice_quiet_s` (fast path), or this cap.
    voice_quiet_s: float = 0.25
    voice_max_wait: float = 0.5
    # A spoken answer can never arrive this soon after the word appears;
    # faster hits are carried over from before the stimulus and are dropped.
    min_voice_lag: float = 0.2
    # Print everything recognized (and what reaches the test) to the
    # terminal.  Silence with --voice-debug false.
    voice_debug: bool = True

    def __post_init__(self):
        self.n_colors = max(2, min(len(self.color_pool), int(self.n_colors)))
        self.congruent_ratio = min(1.0, max(0.0, float(self.congruent_ratio)))
        self.response_mode = "voice" if self.response_mode == "voice" else "keys"


@dataclass
class StroopStressConfig(StroopConfig):
    """Stroop parameters plus the pressure knobs."""

    # Score starts here; a correct answer adds `bonus - RT_ms`, floored at
    # `correct_min_gain`.  The bonus defaults to the timeout penalty, so the
    # scale always matches the timeout: a just-in-time answer pays ~0, a
    # fast one pays well, and the X points at stake are always the X a
    # timeout would deduct.  Set `correct_bonus_ms` to override the base.
    score_start: int = 25000
    score_target: int = 50000
    correct_bonus_ms: int | None = None
    correct_min_gain: int = 0
    # Deducted when the answer takes longer than `stimulus_time` (4000 ms).
    timeout_penalty_ms: int = 4000
    # Gap follows adaptive pressure; isi_decay is only used in fixed mode.
    isi_start: float = 0.1
    isi_min: float = 0.05
    isi_decay: float = 0.6
    # Initial duration of the check/cross; adaptation affects future trials.
    feedback_time: float = 0.5
    # Adaptive timing targets the 50% +/- score_band region, not a hard cap.
    adaptive_timing: bool = True
    score_band: float = 0.2
    adaptation_smoothing: float = 0.25
    # Anticipate recent point gains/losses to avoid delayed overcorrection.
    adaptation_lookahead: float = 15.0
    stimulus_min: float = 0.75
    stimulus_max: float = 4.0
    # Maximum fractional deadline change per trial (0.03 = 3%).
    stimulus_step: float = 0.03
    feedback_min: float = 0.35
    feedback_max: float = 0.65
    # Maximum feedback change per trial, in seconds (0.01 = 10 ms).
    feedback_step: float = 0.01
    # Play the buzzer/cling feedback sounds and the background clock tick
    # (which accelerates 1 Hz -> 3 Hz with the score).
    sounds: bool = True
    tick: bool = True

    def __post_init__(self):
        super().__post_init__()
        self.isi_start = max(0.0, float(self.isi_start))
        self.isi_min = max(0.0, min(self.isi_start, float(self.isi_min)))
        self.isi_decay = min(1.0, max(0.1, float(self.isi_decay)))
        self.feedback_time = max(0.0, float(self.feedback_time))
        if self.score_target <= 0:
            raise ValueError("score_target must be positive")
        for name in ("score_band", "adaptation_smoothing", "adaptation_lookahead", "stimulus_min",
                     "stimulus_max", "stimulus_step", "feedback_min",
                     "feedback_max", "feedback_step"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            setattr(self, name, value)
        if not 0 < self.score_band < 0.4:
            raise ValueError("score_band must be between 0 and 0.4")
        if not 0 < self.adaptation_smoothing <= 1:
            raise ValueError("adaptation_smoothing must be in (0, 1]")
        if self.adaptation_lookahead < 0:
            raise ValueError("adaptation_lookahead must be nonnegative")
        if not 0 < self.stimulus_min <= self.stimulus_max:
            raise ValueError("stimulus bounds must satisfy 0 < min <= max")
        if not 0 < self.feedback_min <= self.feedback_max:
            raise ValueError("feedback bounds must satisfy 0 < min <= max")
        if not 0 <= self.stimulus_step <= 0.1 or not 0 <= self.feedback_step <= 0.05:
            raise ValueError("timing steps must be gradual (stimulus <= 0.1, feedback <= 0.05)")
        if self.adaptive_timing and self.stimulus_time <= 0:
            raise ValueError("adaptive timing requires a positive stimulus_time")
        if (self.adaptive_timing and self.response_mode == "voice"
                and self.stimulus_min <= self.min_voice_lag):
            raise ValueError("stimulus_min must exceed min_voice_lag in voice mode")


# The GUI configuration menu, in display order.  Every entry maps onto a
# field of ``Config``; every configurable field of ``Config`` is listed here
# (the GUI can load and save the complete configuration).  ``{"section": ...}``
# entries are visual separators.  See AGENTS.md for the accepted widget kinds.
FORM = (
    {"section": "Session"},
    {"key": "test_time", "widget": "slider", "min": 10, "max": 600, "step": 5,
     "label": "Test duration (s)"},
    {"key": "rest_time", "widget": "slider", "min": 0, "max": 60, "step": 1,
     "label": "Rest / countdown (s)"},
    {"key": "fullscreen", "widget": "checkbox", "label": "Fullscreen"},
    {"key": "font_size", "widget": "slider", "min": 0, "max": 48, "step": 1,
     "label": "Base font size (0 = from window height)"},
    {"key": "background", "widget": "entry", "label": "Window background (#rrggbb)"},
    {"key": "foreground", "widget": "entry", "label": "Text color (#rrggbb)"},

    {"section": "Task"},
    {"key": "n_colors", "widget": "slider", "min": 2, "max": 10, "step": 1,
     "label": "Colors in play"},
    {"key": "stimulus_time", "widget": "slider", "min": 0.5, "max": 5, "step": 0.25,
     "label": "Initial response window (s)"},
    {"key": "isi", "widget": "slider", "min": 0.0, "max": 2.0, "step": 0.05,
     "label": "Fixation cross between words, non-adaptive (s)"},
    {"key": "congruent_ratio", "widget": "slider", "min": 0.0, "max": 1.0, "step": 0.05,
     "label": "Congruent ratio"},
    {"key": "color_pool", "widget": "entry",
     "label": "Colors in play (space-separated palette names)"},
    {"key": "seed", "widget": "entry", "label": "RNG seed (0 = random)"},
    {"key": "response_mode", "widget": "dropdown", "options": ("keys", "voice"),
     "label": "Response mode"},
    {"key": "show_legend", "widget": "checkbox", "label": "Show key → color legend"},

    {"section": "Pressure and adaptation"},
    {"key": "score_start", "widget": "entry", "label": "Starting score"},
    {"key": "score_target", "widget": "entry", "label": "Score target"},
    {"key": "correct_bonus_ms", "widget": "entry",
     "label": "Correct answer base bonus in ms (empty = timeout penalty)"},
    {"key": "correct_min_gain", "widget": "entry",
     "label": "Minimum points per correct answer"},
    {"key": "timeout_penalty_ms", "widget": "entry",
     "label": "Points deducted on a timeout"},
    {"key": "adaptive_timing", "widget": "checkbox",
     "label": "Adaptive difficulty (pressure follows the score)"},
    {"key": "isi_start", "widget": "slider", "min": 0.0, "max": 2.0, "step": 0.05,
     "label": "Initial gap between words (s)"},
    {"key": "isi_min", "widget": "slider", "min": 0.0, "max": 2.0, "step": 0.05,
     "label": "Minimum gap between words (s)"},
    {"key": "isi_decay", "widget": "slider", "min": 0.1, "max": 1.0, "step": 0.05,
     "label": "Gap decay per trial, fixed mode only"},
    {"key": "feedback_time", "widget": "slider", "min": 0.2, "max": 1.5, "step": 0.05,
     "label": "Feedback duration (s)"},
    {"key": "score_band", "widget": "slider", "min": 0.05, "max": 0.35, "step": 0.05,
     "label": "Preferred score band (+/- around 50%)"},
    {"key": "adaptation_smoothing", "widget": "slider", "min": 0.05, "max": 1.0, "step": 0.05,
     "label": "Adaptation smoothing (higher follows faster)"},
    {"key": "adaptation_lookahead", "widget": "slider", "min": 0, "max": 60, "step": 1,
     "label": "Adaptation lookahead (trials)"},
    {"key": "stimulus_min", "widget": "slider", "min": 0.25, "max": 4.0, "step": 0.25,
     "label": "Shortest response window (s)"},
    {"key": "stimulus_max", "widget": "slider", "min": 0.5, "max": 8.0, "step": 0.5,
     "label": "Longest response window (s)"},
    {"key": "stimulus_step", "widget": "slider", "min": 0.0, "max": 0.1, "step": 0.01,
     "label": "Max response window change per trial"},
    {"key": "feedback_min", "widget": "slider", "min": 0.1, "max": 1.0, "step": 0.05,
     "label": "Shortest feedback duration (s)"},
    {"key": "feedback_max", "widget": "slider", "min": 0.1, "max": 1.5, "step": 0.05,
     "label": "Longest feedback duration (s)"},
    {"key": "feedback_step", "widget": "slider", "min": 0.0, "max": 0.05, "step": 0.005,
     "label": "Max feedback change per trial (s)"},
    {"key": "sounds", "widget": "checkbox", "label": "Feedback sounds"},
    {"key": "tick", "widget": "checkbox", "label": "Background clock tick"},

    {"section": "Voice (response mode: voice)",
     "enabled_by": {"key": "response_mode", "values": ("voice",)}},
    {"key": "voice_calibration", "widget": "checkbox",
     "label": "Voice calibration (say each color once first)"},
    {"key": "mic_device", "widget": "entry",
     "label": "Microphone device index (-1 = system default)"},
    {"key": "vosk_model", "widget": "entry",
     "label": "Vosk model path (empty = auto-managed)"},
    {"key": "voice_quiet_s", "widget": "slider", "min": 0.05, "max": 1.0, "step": 0.05,
     "label": "Silence that ends a spoken word (s)"},
    {"key": "voice_max_wait", "widget": "slider", "min": 0.1, "max": 2.0, "step": 0.1,
     "label": "Max wait for speech to end after a word (s)"},
    {"key": "min_voice_lag", "widget": "slider", "min": 0.05, "max": 1.0, "step": 0.05,
     "label": "Minimum lag for a valid answer (s)"},
    {"key": "voice_debug", "widget": "checkbox",
     "label": "Print everything recognized to the terminal"},
)

# The configuration class the GUI and the runner instantiate for this test.
Config = StroopStressConfig
