"""Import-light contract for the headphone SSST adaptation."""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
from pathlib import Path
import re

from ..common.base import TestConfig

NAME = "ssst"
TITLE = "SSST"
DESCRIPTION = "Read-aloud and pitch matching, followed by singing anticipation and karaoke."
# Stable identifiers for CLI/configs. Display names always come from the
# supplied files' names, never from a presumed ordering of songs.
SONG_OPTIONS = ("participant",) + tuple(str(i) for i in range(10))
SONGS_DIR = Path(__file__).resolve().parent / "songs"
EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac"}


def song_number(value):
    """Song identifier from a form/CLI value: "participant" or "0"–"9"."""
    match = re.fullmatch(r"([0-9])(?: — .+)?", value)
    if not match:
        raise ValueError("Choose participant or a numbered song (0–9)")
    return int(match[1])


def song_files(folder):
    """Import-light catalog used by both the GUI form and the task."""
    result = {}
    folder = Path(folder)
    for path in sorted(folder.iterdir()) if folder.is_dir() else ():
        match = re.fullmatch(r"([0-9])_(.+)--(.+)", path.stem)
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS or not match:
            continue
        number = int(match[1])
        if number in result:
            raise ValueError(f"Multiple audio clips for number {number}; keep one")
        result[number] = (match[2].replace("_", " ").title(),
                          match[3].replace("_", " ").title(), path)
    return result


def song_options(values):
    """Dropdown options for the current form values (songs_dir-aware)."""
    folder = Path(values.get("songs_dir", "")).expanduser() if values.get("songs_dir") else SONGS_DIR
    return ("participant",) + tuple(f"{number} — {title}, by {artist}"
                                    for number, (title, artist, _path) in song_files(folder).items())
PHRASES = (
    "A vacuum cleaner collects dust and small particles from floors.|"
    "The dust is collected in a bag or a removable container.|"
    "Different attachments can be used to clean different surfaces."
)


@dataclass
class Config(TestConfig):
    test_time: float = 60.0
    rest_time: float = 10.0
    instruction_time: float = 10.0
    neutral_countdown: float = 5.0
    response_time: float = 12.0
    anticipation_time: float = 10.0
    recovery_time: float = 30.0
    phrases: str = PHRASES
    tone_frequencies: str = "150 180 220 260"
    tone_duration: float = 2.0
    tone_response_time: float = 6.0
    pitch_tolerance: float = 100.0
    mic_device: int = -1
    vosk_model: str = ""
    mic_threshold: float = 0.01
    pause_time: float = 1.2
    voice_enabled: bool = True
    song: str = "participant"
    songs_dir: str = ""
    lead_in: float = 8.0
    auto_lead_in: bool = True
    lyrics_offset: float = 0.0
    negative_feedback: bool = True
    feedback_delay: float = 8.0
    feedback_interval: float = 8.0
    feedback_messages: str = "Pitch accuracy below target.|Timing inconsistent.|Performance below the reference level."

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            valid = (type(value) is bool if field.type in (bool, "bool") else
                     type(value) is int if field.type in (int, "int") else
                     type(value) in (int, float) and math.isfinite(value) if field.type in (float, "float") else
                     isinstance(value, str))
            if not valid:
                raise ValueError(f"Invalid {field.name}: {value!r}")
        for key in ("test_time", "instruction_time", "response_time",
                    "tone_duration", "tone_response_time", "pitch_tolerance", "feedback_interval"):
            if getattr(self, key) <= 0:
                raise ValueError(f"{key} must be positive")
        for key in ("rest_time", "neutral_countdown",
                    "anticipation_time", "recovery_time", "lead_in", "feedback_delay", "font_size",
                    "pause_time"):
            if getattr(self, key) < 0:
                raise ValueError(f"{key} must be nonnegative")
        if self.lead_in >= self.test_time:
            raise ValueError("lead_in must be shorter than test_time (total clip duration)")
        if self.song != "participant":
            song_number(self.song)
        if self.mic_device < -1 or not 0 < self.mic_threshold < 1:
            raise ValueError("Invalid microphone index or threshold")
        if not self.phrases.strip() or any(not p.strip() for p in self.phrases.split("|")):
            raise ValueError("Provide nonempty phrases separated by |")
        if not self.feedback_messages.strip() or any(not p.strip() for p in self.feedback_messages.split("|")):
            raise ValueError("Provide nonempty feedback messages separated by |")
        try:
            tones = [float(t) for t in self.tone_frequencies.split()]
        except ValueError as exc:
            raise ValueError("tone_frequencies must contain frequencies in Hz") from exc
        if any(not math.isfinite(t) or not 80 <= t <= 800 for t in tones):
            raise ValueError("Target tones must be between 80 and 800 Hz")


def entry(key, label):
    return {"key": key, "widget": "entry", "label": label}


FORM = (
    {"section": "Session timing (seconds)"},
    entry("instruction_time", "Instruction screens (SPACE skips them)"),
    entry("neutral_countdown", "Countdown before each read-aloud phrase"),
    entry("response_time", "Silence fallback when recognition is unavailable (s)"),
    entry("rest_time", "Quiet rest before song selection"),
    entry("anticipation_time", "Singing anticipation countdown"),
    entry("test_time", "Total song clip time"),
    entry("recovery_time", "Quiet recovery after singing"),
    {"section": "Speech and pitch"},
    entry("phrases", "English read-aloud phrases, separated by |"),
    entry("pause_time", "Silence that finishes a read-aloud trial (s; 0 = full window)"),
    entry("tone_frequencies", "Target tones in Hz, space separated (empty = skip)"),
    entry("tone_duration", "Reference tone duration (s)"),
    entry("tone_response_time", "Tone matching response window (s)"),
    entry("pitch_tolerance", "Acceptable pitch error (cents; 100 = one semitone)"),
    {"key": "voice_enabled", "widget": "checkbox", "label": "Enable microphone and offline speech recognition"},
    {"section": "Microphone", "enabled_by": {"key": "voice_enabled", "values": ("True",)}},
    entry("mic_device", "Microphone index (-1 = system default)"),
    entry("vosk_model", "Vosk model folder (empty = automatic)"),
    entry("mic_threshold", "Minimum microphone RMS (0–1)"),
    {"section": "Song and karaoke"},
    {"key": "song", "widget": "dropdown", "options": song_options,
     "label": "Song (participant = hidden number choice; otherwise forced)"},
    entry("songs_dir", "Songs folder (empty = SSST/songs)"),
    entry("lead_in", "Preparation time before the singing cue (s)"),
    {"key": "auto_lead_in", "widget": "checkbox", "label": "Sing at the first lyric timestamp (lead_in is the minimum)"},
    entry("lyrics_offset", "Lyric timing adjustment (s; positive = later)"),
    {"key": "negative_feedback", "widget": "checkbox", "label": "Scripted negative singing feedback"},
    {"section": "Scripted feedback", "enabled_by": {"key": "negative_feedback", "values": ("True",)}},
    entry("feedback_delay", "Delay from singing cue to first feedback (s)"),
    entry("feedback_interval", "Interval between feedback messages (s)"),
    entry("feedback_messages", "Scripted messages separated by |"),
    {"section": "Appearance"},
    {"key": "fullscreen", "widget": "checkbox", "label": "Fullscreen"},
    entry("font_size", "Base font size (0 = automatic)"),
    entry("background", "Background color"),
    entry("foreground", "Text color"),
)
