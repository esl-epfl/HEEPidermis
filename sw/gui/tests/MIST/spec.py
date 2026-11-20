"""Import-light configuration contract for the MIST implementation."""

from dataclasses import dataclass, fields
import math

from ..common.base import TestConfig

NAME = "mist"
TITLE = "MIST"
DESCRIPTION = "Mental arithmetic with calibrated time pressure and performance feedback."


@dataclass
class Config(TestConfig):
    practice_time: float = 120.0
    block_order: str = "control stress"
    repetitions: int = 1
    difficulty: int = 3
    seed: int = 0
    response_mode: str = "keys"
    response_time: float = 4.0
    response_min: float = 0.6
    response_max: float = 15.0
    adaptive_timing: bool = True
    time_factor: float = 0.9
    recovery_factor: float = 1.1
    adaptation_streak: int = 3
    feedback_time: float = 0.8
    isi: float = 0.3
    benchmark: float = 0.85
    show_comparison: bool = True
    sounds: bool = True
    tick: bool = True
    voice_calibration: bool = True
    calibration_time: float = 5.0
    mic_device: int = -1
    vosk_model: str = ""
    min_voice_lag: float = 0.2
    voice_grace: float = 0.3
    voice_debug: bool = False
    speaker_filter: bool = True
    speaker_threshold: float = 0.3

    def __post_init__(self):
        # Validate types as well as ranges: HJSON values reach this directly.
        for field in fields(self):
            value = getattr(self, field.name)
            if field.type in (bool, "bool"):
                valid = isinstance(value, bool)
            elif field.type in (int, "int"):
                valid = type(value) is int
            elif field.type in (float, "float"):
                valid = type(value) in (int, float) and math.isfinite(value)
            else:
                valid = isinstance(value, str)
            if not valid:
                raise ValueError(f"Invalid {field.name}: {value!r}")
        for key in ("test_time", "response_time", "response_min", "response_max",
                    "feedback_time", "calibration_time"):
            if getattr(self, key) <= 0:
                raise ValueError(f"{key} must be positive")
        for key in ("rest_time", "practice_time", "isi", "min_voice_lag", "voice_grace"):
            if getattr(self, key) < 0:
                raise ValueError(f"{key} must be nonnegative")
        if not 0 < self.speaker_threshold <= 1:
            raise ValueError("speaker_threshold must be between 0 (exclusive) and 1")
        if not self.response_min <= self.response_time <= self.response_max:
            raise ValueError("Response times must satisfy min <= initial <= max")
        if self.response_mode not in ("keys", "voice"):
            raise ValueError("response_mode must be keys or voice")
        if self.response_mode == "voice" and self.min_voice_lag >= self.response_min:
            raise ValueError("response_min must exceed min_voice_lag in voice mode")
        if not self.block_order.split() or any(
                mode not in ("control", "stress") for mode in self.block_order.split()):
            raise ValueError("block_order must contain control and/or stress, separated by spaces")
        if not 1 <= self.difficulty <= 5:
            raise ValueError("difficulty must be between 1 and 5")
        if self.repetitions < 1 or self.adaptation_streak < 1:
            raise ValueError("repetitions and adaptation_streak must be positive")
        if not 0 < self.time_factor < 1 or self.recovery_factor <= 1:
            raise ValueError("time_factor must be between 0 and 1; recovery_factor must exceed 1")
        if not 0 < self.benchmark <= 1:
            raise ValueError("benchmark must be between 0 (exclusive) and 1")
        if self.font_size < 0 or self.mic_device < -1:
            raise ValueError("font_size must be nonnegative; mic_device must be >= -1")


FORM = (
    {"section": "Session and blocks"},
    {"key": "test_time", "widget": "entry", "label": "Duration of EACH control / stress block (s)"},
    {"key": "practice_time", "widget": "entry", "label": "Untimed practice / pace calibration (s; 0 = skip)"},
    {"key": "rest_time", "widget": "entry", "label": "Rest before each block (s)"},
    {"key": "block_order", "widget": "entry", "label": "Block order (e.g. control stress or stress)"},
    {"key": "repetitions", "widget": "slider", "min": 1, "max": 20, "step": 1,
     "label": "Repetitions of the block order"},
    {"key": "fullscreen", "widget": "checkbox", "label": "Fullscreen"},
    {"key": "font_size", "widget": "entry", "label": "Base font size (0 = automatic)"},
    {"key": "background", "widget": "entry", "label": "Window background (#rrggbb)"},
    {"key": "foreground", "widget": "entry", "label": "Text color (#rrggbb)"},
    {"section": "Arithmetic and responses"},
    {"key": "difficulty", "widget": "slider", "min": 1, "max": 5, "step": 1,
     "label": "Difficulty (1–2: +/−, 3–4: ×, 5: ×/÷)"},
    {"key": "seed", "widget": "entry", "label": "Random seed (0 = random)"},
    {"key": "response_mode", "widget": "dropdown", "options": ("keys", "voice"),
     "label": "Response mode (answers are always 0–9)"},
    {"key": "feedback_time", "widget": "entry", "label": "Feedback duration (s)"},
    {"key": "isi", "widget": "entry", "label": "Gap between problems (s)"},
    {"section": "Stress pressure"},
    {"key": "response_time", "widget": "entry", "label": "Initial response limit without practice (s)"},
    {"key": "response_min", "widget": "entry", "label": "Shortest response limit (s)"},
    {"key": "response_max", "widget": "entry", "label": "Longest response limit (s)"},
    {"key": "adaptive_timing", "widget": "checkbox", "label": "Adapt response limits after streaks"},
    {"key": "time_factor", "widget": "entry", "label": "Practice / correct-streak time multiplier (<1)"},
    {"key": "recovery_factor", "widget": "entry", "label": "Wrong / timeout streak multiplier (>1)"},
    {"key": "adaptation_streak", "widget": "entry", "label": "Consecutive outcomes before adaptation"},
    {"key": "benchmark", "widget": "entry", "label": "Expected accuracy benchmark (0–1)"},
    {"key": "show_comparison", "widget": "checkbox", "label": "Show performance against benchmark in stress blocks"},
    {"key": "sounds", "widget": "checkbox", "label": "Stress feedback sounds (loud buzzer / quiet cling)"},
    {"key": "tick", "widget": "checkbox", "label": "Accelerating clock in stress blocks (requires sounds)"},
    {"section": "Voice (response mode: voice)",
     "enabled_by": {"key": "response_mode", "values": ("voice",)}},
    {"key": "voice_calibration", "widget": "checkbox", "label": "Voice calibration: say zero through nine before practice"},
    {"key": "calibration_time", "widget": "entry", "label": "Seconds to recognize each calibration digit"},
    {"key": "mic_device", "widget": "entry", "label": "Microphone index (-1 = system default)"},
    {"key": "vosk_model", "widget": "entry", "label": "Vosk model path (empty = automatic)"},
    {"key": "min_voice_lag", "widget": "entry", "label": "Reject speech earlier than this after onset (s)"},
    {"key": "voice_grace", "widget": "entry", "label": "Recognition delivery grace after deadline (s)"},
    {"key": "voice_debug", "widget": "checkbox", "label": "Print recognition diagnostics"},
    {"key": "speaker_filter", "widget": "checkbox",
     "label": "Learn the participant's voice at calibration; reject other voices"},
    {"key": "speaker_threshold", "widget": "entry",
     "label": "Voice-match threshold (cosine 0–1; lower = stricter)"},
)
