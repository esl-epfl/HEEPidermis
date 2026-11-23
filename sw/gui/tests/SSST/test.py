"""MIST-family SSST: read-aloud, pitch matching, anticipation and karaoke."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import math
from pathlib import Path
import re
import statistics
import time
from tkinter import font as tkfont

from ..common.base import CognitiveTest
from ..common.media import MediaPlayer
from ..common.visuals import GREEN, RED, draw_instructions_panel, draw_verdict
from ..common.vocal import VocalMonitor, phrase_score, pitch_cents
from .assets import (CONTOUR_FRAME, SONGS_DIR, discover_songs, lyric_at, read_lyrics,
                     read_segment, segment_lyrics, song_contour)
from .spec import Config, song_number

# Points (same scale as the other games' scores), anchored at a 50% word
# match: every percentage point above 50% adds 10 points, every point below
# deducts 10 — a perfect phrase earns +500, total silence loses −500.
HALF_MARK = 0.5
VERDICT_TIME = 1.5           # Full-screen check/cross shown after each scored trial
MELODY_TOLERANCE_CENTS = 250  # "More or less nailing it" for the singing bars.
UPCOMING_LINES = 4            # Faded lyric preview below the current line.
UPCOMING_COLORS = ("#9a9aa2", "#7c7c84", "#62626a", "#4e4e55")
INSTRUCTIONS = ("reading instructions", "tone instructions", "singing instructions")


def match_points(score):
    """10 points per percentage point of word match above/below 50%."""
    return round((score - HALF_MARK) * 100) * 10


@dataclass(frozen=True)
class Phase:
    name: str
    duration: float | None
    text: str = ""
    target: float = 0.0
    trial: int = 0


def phases(config):
    result = [Phase("reading instructions", config.instruction_time)]
    for i, text in enumerate(config.phrases.split("|"), 1):
        text = text.strip()
        if config.neutral_countdown:
            result.append(Phase("neutral countdown", config.neutral_countdown, trial=i))
        # Reading has no timeout: a recognized pause ends the trial.
        result.append(Phase("read", None, text, trial=i))
        result.append(Phase("read verdict", VERDICT_TIME, trial=i))
    if config.tone_frequencies.split():
        result.append(Phase("tone instructions", config.instruction_time))
    for i, frequency in enumerate(config.tone_frequencies.split(), 1):
        result.extend((Phase("tone listen", config.tone_duration, target=float(frequency), trial=i),
                       Phase("tone matching", config.tone_response_time, target=float(frequency), trial=i),
                       Phase("tone verdict", VERDICT_TIME, trial=i)))
    if config.rest_time:
        result.append(Phase("rest", config.rest_time))
    if config.song == "participant":
        result.append(Phase("number selection", None))
    result.extend((Phase("singing instructions", config.instruction_time),
                   Phase("anticipation", config.anticipation_time),
                   Phase("preparation", config.lead_in),
                   Phase("singing", config.test_time - config.lead_in)))
    if config.recovery_time:
        result.append(Phase("recovery", config.recovery_time))
    return result


class Test(CognitiveTest):
    NAME = "ssst"
    ConfigClass = Config

    def __init__(self, config=None, **overrides):
        super().__init__(config, **overrides)
        self.state = "instructions"
        self._phases = phases(self.config)
        self._index = -1
        self._phase = None
        self._deadline = None
        self._phase_started = 0.0
        self._media = MediaPlayer()
        self._voice = None
        self._selected = None
        self._song = None
        self._lyrics = []
        self._lyric_index = -2
        self._clip_started = None
        self._clip_offset = 0.0
        self._segment_start = 0.0
        self._lead_in = self.config.lead_in
        self._last_feedback = ""
        self._feedback_count = 0
        self._next_feedback = 0.0
        self._scripted_shown = False
        self._issue = ""
        self._notices = set()
        self._level = 0.0
        self._pitch = None
        self._pitch_samples = []
        self._final_text = []
        self._partial = ""
        self._last_result = ""
        self._audio_notice = ""
        self._tone_reference_ok = False
        self._points = 0
        self._verdict = None          # (correct, delta) shown after each scored trial
        self._voice_seen = False      # Any voice activity in the current trial
        self._last_voice_at = None    # For the pause that finishes a read-aloud trial
        self._sing_samples = []       # (clip_s, Hz) of the live singing pitch
        self._contour = []            # Approximate song pitch, one value per 100 ms
        self._lyric_match = None      # Live recognized-lyric similarity
        self._line_deltas = {}        # Per-karaoke-line points, right of the line
        self._active_line = None      # (index, text) of the line being sung
        self._line_start = 0          # Recognition finals where the line began
        self._song_folder = Path(self.config.songs_dir).expanduser() if self.config.songs_dir else SONGS_DIR

    def _begin(self):
        self._root.title("Voice recognition and matching")
        self.mark("instructions", config=self.config.to_dict())
        if self.config.voice_enabled:
            self._voice = VocalMonitor(self.config.mic_device, self.config.vosk_model, self.config.mic_threshold)
            self._voice.start()
        self._draw()
        self.after(50, self._tick)

    def _handle_key(self, keysym):
        if keysym == "space":
            if self.state == "instructions":
                self.mark("SSST start", color="yellow", config=self.config.to_dict())
                if self._voice and not self._voice.ready and not self._voice.error:
                    self.state = "microphone loading"
                else:
                    self._advance()
            elif self.state == "microphone loading":
                self.mark("microphone wait skipped")
                self._advance()
            elif self.state == "number selection":
                # Silent skip convention: choose the first available numbered
                # clip (or 0 if the library has not yet been populated).
                try:
                    options = sorted(discover_songs(self._song_folder))
                except ValueError:
                    options = []
                self._choose(options[0] if options else 0, skipped=True)
            elif self.state == "media error":
                self._skip_song()
            elif self.state == "summary":
                self._restart()  # Offered on the end screen: run it again.
            else:
                self._advance(skipped=True)
        elif self.state == "number selection" and len(keysym) == 1 and keysym.isdigit():
            if int(keysym) in self._choices():
                self._choose(int(keysym))
        elif self.state == "media error" and keysym == "Return":
            self._retry_song()
        self._draw()

    def _choices(self):
        """All numbers 0–9; availability only affects how they are drawn."""
        return list(range(10))

    def _choose(self, number, skipped=False):
        self._selected = number
        self.mark("song selected", number=number, selection_mode="participant", skipped=skipped)
        self._advance(skipped=skipped)

    def _load_song(self):
        if self._selected is None:
            self._selected = song_number(self.config.song)
            self.mark("song selected", number=self._selected, selection_mode="forced")
        songs = discover_songs(self._song_folder)
        if self._selected not in songs:
            raise ValueError(f"Missing clip {self._selected}_name--artist in {self._song_folder}")
        self._song = songs[self._selected]
        self._segment_start = read_segment(self._song_folder / f"{self._selected}_segment.txt")
        self._lyrics = segment_lyrics(read_lyrics(self._song_folder / f"{self._selected}_lyrics.txt"),
                                      self._segment_start, self.config.test_time)
        self._lead_in = self.config.lead_in
        first_lyric = next((stamp for stamp, text in self._lyrics if text), None)
        if self.config.auto_lead_in and first_lyric is not None:
            # The cue is the later of the configured preparation and the first
            # lyric: a clip that opens with vocals keeps its minimum build-up.
            first_lyric = max(0.0, first_lyric + self.config.lyrics_offset)
            if first_lyric >= self.config.test_time:
                raise ValueError("First lyric is beyond the clip duration; use excerpt-relative timestamps")
            self._lead_in = max(self._lead_in, first_lyric)
        self._contour = song_contour(self._song.path, self._segment_start, self.config.test_time)
        self.mark("song ready", number=self._selected, title=self._song.title,
                  artist=self._song.artist, path=str(self._song.path), lyric_lines=len(self._lyrics),
                  lead_in_s=self._lead_in, clip_duration_s=self.config.test_time,
                  source_start_s=self._segment_start, lyrics_offset_s=self.config.lyrics_offset,
                  melody_frames=len(self._contour))

    def _media_error(self, message):
        self._end_phase(aborted=True)
        self._media.stop()
        if self._voice:
            self._voice.listen("off")
        self._issue = message
        self.state = "media error"
        self._deadline = None
        self.mark("audio unavailable", error=message, number=self._selected)

    def _retry_song(self):
        try:
            self._load_song()
        except (ValueError, OSError) as exc:
            self._issue = str(exc)
            return
        # Re-enter instructions and anticipation, never resume halfway into a
        # failed stress trial. Each attempt remains explicit in the timeline.
        self._index = next(i for i, phase in enumerate(self._phases) if phase.name == "singing instructions")
        self._enter(self._phases[self._index])

    def _skip_song(self):
        self.mark("song skipped", number=self._selected, reason=self._issue)
        self._index = next(i for i, phase in enumerate(self._phases) if phase.name == "singing")
        self._advance(skipped=True)

    def _score_line(self, index, text, recognized):
        """Score one karaoke line against the words recognized during it."""
        available = bool(self._voice and self._voice.ready and not self._voice.recognition_error)
        score = phrase_score(text, recognized) if available and text else None
        if score is None:
            return
        delta = match_points(score)
        self._points += delta
        self._line_deltas[index] = delta
        self.record(kind="singing line", line=index, expected=text, recognized=recognized,
                    score=score, points=delta)
        self.mark("correct" if score >= HALF_MARK else "wrong",
                  color="green" if score >= HALF_MARK else "red",
                  phase="singing", line=index, score=score, points=delta, recognized=recognized)

    def _end_phase(self, skipped=False, aborted=False):
        if self._phase is None:
            return
        phase = self._phase
        self._poll_voice()
        if phase.name == "read":
            text = " ".join(self._final_text + ([self._partial] if self._partial else []))
            available = bool(self._voice and self._voice.ready and not self._voice.recognition_error)
            score = phrase_score(phase.text, text) if available and not skipped and not aborted else None
            delta = None
            if score is not None:
                delta = match_points(score)
                self._points += delta
                self._verdict = (score >= HALF_MARK, delta)
            self.record(kind="speech", phase=phase.name, trial=phase.trial, expected=phase.text,
                        recognized=text, score=score, points=delta, available=available,
                        skipped=skipped, aborted=aborted)
            self._last_result = (f"Word match: {score:.0%} · {text or 'No words detected'}"
                                 if score is not None else "Speech trial not scored")
            if score is not None:
                self.mark("correct" if score >= HALF_MARK else "wrong",
                          color="green" if score >= HALF_MARK else "red",
                          phase=phase.name, trial=phase.trial, score=score, points=delta,
                          recognized=text)
        elif phase.name == "tone matching":
            frames = len(self._pitch_samples)
            voiced = [p for p in self._pitch_samples if p[1] is not None]
            errors = [abs(pitch_cents(pitch, phase.target)) for _, pitch in voiced]
            inside = sum(error <= self.config.pitch_tolerance for error in errors)
            # Every captured 100 ms frame inside tolerance earns +100 points,
            # every frame outside (voiced off-pitch or unvoiced) loses 100.
            ms_inside, ms_outside = inside * 100, (frames - inside) * 100
            score = inside / frames if frames and self._tone_reference_ok and not skipped and not aborted else None
            delta = (ms_inside - ms_outside) if score is not None else None
            if delta is not None:
                self._points += delta
                self._verdict = (delta > 0, delta)
            self.record(kind="pitch", trial=phase.trial, target_hz=phase.target, score=score,
                        points=delta, reference_available=self._tone_reference_ok,
                        median_error_cents=statistics.median(errors) if errors else None,
                        voiced_frames=len(voiced), total_frames=frames,
                        ms_inside=ms_inside if score is not None else None,
                        ms_outside=ms_outside if score is not None else None,
                        samples=self._pitch_samples, skipped=skipped, aborted=aborted)
            self._last_result = (f"{ms_inside:,} ms in tune · {delta:+,} points"
                                 if delta is not None else "No stable pitch score")
            if score is not None:
                self.mark("correct" if delta > 0 else "wrong", color="green" if delta > 0 else "red",
                          phase=phase.name, trial=phase.trial, score=score, points=delta,
                          target_hz=phase.target)
        elif phase.name == "singing":
            if not skipped and not aborted and self._active_line is not None:
                # The line still in progress when the clip ends is scored now.
                recognized = " ".join(self._final_text[self._line_start:]
                                      + ([self._partial] if self._partial else []))
                self._score_line(*self._active_line, recognized)
            total = sum(self._line_deltas.values())
            available = bool(self._voice and self._voice.ready and not self._voice.recognition_error)
            self.record(kind="singing lyrics", points=total, lines=len(self._line_deltas),
                        available=available, skipped=skipped, aborted=aborted)
            self._last_result = f"Singing total: {total:+,} points"
        self.mark(f"SSST {phase.name} end", color="yellow", trial=phase.trial,
                  skipped=skipped, aborted=aborted)
        self._phase = None

    def _advance(self, skipped=False):
        previous = self._phase.name if self._phase else ""
        if previous == "tone listen":
            self._tone_reference_ok = not skipped and not self._media.failed
        self._end_phase(skipped=skipped)
        preserve_clip = previous == "preparation" and not skipped
        if not preserve_clip:
            self._media.stop()
        if self._voice:
            self._voice.listen("off")
        while True:
            self._index += 1
            if self._index >= len(self._phases):
                self._show_summary()
                return
            phase = self._phases[self._index]
            # A verdict phase with nothing to show (skipped/unscored trial)
            # is itself skipped silently.
            if phase.name in ("read verdict", "tone verdict") and self._verdict is None:
                continue
            break
        if phase.name == "singing instructions":
            try:
                self._load_song()
            except (ValueError, OSError) as exc:
                self._media_error(str(exc))
                return
        self._enter(phase, preserve_clip=preserve_clip)

    def _enter(self, phase, preserve_clip=False):
        self._phase, self.state = phase, phase.name
        now = time.monotonic()
        self._phase_started = now
        self._deadline = now + phase.duration if phase.duration is not None else None
        self._final_text, self._partial, self._pitch_samples = [], "", []
        self._pitch, self._level = None, 0.0
        self._last_feedback = ""
        self._audio_notice = ""
        self._voice_seen, self._last_voice_at = False, None
        if phase.name in ("read", "tone matching"):
            self._verdict = None  # Fresh check/cross for every scored trial.
        if phase.name == "read" and not (self._voice and self._voice.ready):
            # Recognition unavailable: keep the response window as a fallback
            # (normally reading ends on the recognized pause, without timeout).
            self._deadline = now + self.config.response_time
        self.mark(f"SSST {phase.name} start", color="yellow", trial=phase.trial, target_hz=phase.target or None)
        if phase.name == "read":
            if self._voice:
                self._voice.listen("speech")
            self.mark("stimulus", phase=phase.name, text=phase.text, trial=phase.trial)
        elif phase.name == "tone matching":
            if self._voice:
                self._voice.listen("pitch")
            self.mark("stimulus", phase=phase.name, target_hz=phase.target, trial=phase.trial)
        elif phase.name == "tone listen":
            self._tone_reference_ok = False
            if not self._media.tone(phase.target, phase.duration):
                self._notice("tone playback unavailable", self._media.error)
        elif phase.name == "preparation":
            if not self._media.play(self._song.path, self._segment_start,
                                    duration=self.config.test_time):
                self._media_error(self._media.error)
                return
            self._clip_started, self._clip_offset = self._media.started, 0.0
            self._deadline = self._clip_started + self._lead_in
            self._lyric_match = None
            self.mark("song playback", path=str(self._song.path), offset_s=0.0,
                      source_start_s=self._segment_start)
        elif phase.name == "singing":
            if self._voice:
                self._voice.listen("sing")
            self._sing_samples = []
            self._lyric_match = None
            if not preserve_clip:
                if not self._media.play(self._song.path, self._segment_start + self._lead_in,
                                        duration=self.config.test_time - self._lead_in):
                    self._media_error(self._media.error)
                    return
                self._clip_started, self._clip_offset = self._media.started, self._lead_in
                self.mark("song playback", path=str(self._song.path), offset_s=self._clip_offset,
                          source_start_s=self._segment_start + self._clip_offset)
            self._deadline = self._clip_started + self.config.test_time - self._clip_offset
            self._feedback_count = 0
            self._next_feedback = now + self.config.feedback_delay
            self._lyric_index = -2
            self._line_deltas, self._active_line, self._line_start = {}, None, len(self._final_text)
            self._update_song(now)  # Adopt the line in progress; nothing to score yet.
        self._draw()

    def _notice(self, label, message):
        if "playback" in label:
            self._audio_notice = message
        key = (label, message)
        if key not in self._notices:
            self._notices.add(key)
            self.mark(label, error=message)

    def _poll_voice(self):
        if not self._voice:
            return
        for result in self._voice.drain():
            if result["at"] < self._phase_started or (self._deadline is not None and result["at"] > self._deadline):
                continue
            self._level, self._pitch = result["level"], result["pitch"]
            if self.state == "read" and result["level"] >= self.config.mic_threshold:
                self._voice_seen = True
                self._last_voice_at = result["at"]
            if self.state == "tone matching":
                self._pitch_samples.append((round(result["at"] - self._phase_started, 3), result["pitch"]))
            elif self.state == "singing" and result["pitch"] is not None:
                elapsed = result["at"] - self._clip_started + self._clip_offset
                self._sing_samples.append((round(elapsed, 3), result["pitch"]))
                # Only the recent window is drawn; prune the rest.
                if len(self._sing_samples) > 400:
                    del self._sing_samples[:-200]
            if result["final"]:
                if result["text"]:
                    self._final_text.append(result["text"])
                self._partial = ""
            else:
                self._partial = result["text"]
            if result["overflow"]:
                self._notice("microphone overflow", "Audio input overflow; some samples were lost")

    def _tick(self):
        if self.result is not None:
            return
        now = time.monotonic()
        self._poll_voice()
        if self._voice:
            if self._voice.error:
                self._notice("voice unavailable", self._voice.error)
            if self._voice.recognition_error:
                self._notice("recognition unavailable", self._voice.recognition_error)
        if self.state == "microphone loading" and (self._voice.ready or self._voice.error):
            self._advance()
        elif self.state in ("preparation", "singing"):
            if self._media.failed or (not self._media.playing and now < self._deadline - 1):
                self._media_error("Song playback failed or ended before the configured clip duration. Check the file and test_time.")
            else:
                self._update_song(now)
        elif (self.state == "read" and self._voice_seen
              and self.config.pause_time and self._last_voice_at is not None
              and now - self._last_voice_at >= self.config.pause_time):
            # A recognized pause after speaking finishes the trial early.
            self.mark("pause detected", phase=self.state, trial=self._phase.trial)
            self._advance()
        elif (self.state == "read" and self._deadline is None and not self._voice_seen
              and now - self._phase_started >= self.config.response_time):
            # Timeout-free reading still needs an exit when the microphone
            # never hears anything: treat extended silence as silence.
            self._advance()
        if self._deadline is not None and now >= self._deadline:
            # Tone playback must finish before we open the microphone.
            waiting_audio = self.state == "tone listen" and self._media.playing
            if not waiting_audio or now > self._deadline + 30:
                self._advance()
        self._draw()
        self.after(50, self._tick)

    def _update_song(self, now):
        elapsed = now - self._clip_started + self._clip_offset
        index, current, _ = lyric_at(self._lyrics, elapsed - self.config.lyrics_offset)
        if index != self._lyric_index:
            # A new line begins: the finished line earns/loses its points.
            if self.state == "singing" and self._active_line is not None:
                recognized = " ".join(self._final_text[self._line_start:])
                self._score_line(*self._active_line, recognized)
            self._lyric_index = index
            self._line_start = len(self._final_text)
            self._active_line = (index, current) if index >= 0 and current else None
            if index >= 0:
                self.mark("karaoke line", line=index, text=current, clip_s=round(elapsed, 3))
        if self.state == "singing":
            expected = " ".join(text for stamp, text in self._lyrics
                                if text and self._lead_in <= stamp <= elapsed - self.config.lyrics_offset)
            recognized = " ".join(self._final_text + ([self._partial] if self._partial else []))
            if expected:
                self._lyric_match = phrase_score(expected, recognized)
        if self.state == "singing" and self.config.negative_feedback and now >= self._next_feedback:
            messages = [text.strip() for text in self.config.feedback_messages.split("|")]
            self._last_feedback = messages[self._feedback_count % len(messages)]
            self._feedback_count += 1
            self._scripted_shown = True
            self._next_feedback = now + self.config.feedback_interval
            self.record(kind="singing feedback", scripted=True, text=self._last_feedback, clip_s=round(elapsed, 3))
            self.mark("singing feedback", color="red", scripted=True, text=self._last_feedback)

    def _show_summary(self):
        self._media.stop()
        if self._voice:
            self._voice.stop()
        self.state, self._deadline = "summary", None
        self.mark("SSST end", color="yellow", summary=self._summary())
        self._draw()

    def _restart(self):
        """From the very beginning: fresh events, markers and microphone."""
        self.events = []
        self._reset_markers()
        self._media.stop()
        if self._voice:
            self._voice.stop()
        if self.config.voice_enabled:
            self._voice = VocalMonitor(self.config.mic_device, self.config.vosk_model,
                                       self.config.mic_threshold)
            self._voice.start()
        else:
            self._voice = None
        self.state = "instructions"
        self._phases = phases(self.config)
        self._index, self._phase, self._deadline = -1, None, None
        self._selected, self._song, self._lyrics = None, None, []
        self._contour, self._sing_samples = [], []
        self._line_deltas, self._active_line = {}, None
        self._points, self._verdict = 0, None
        self._last_result, self._last_feedback = "", ""
        self._scripted_shown, self._issue = False, ""
        self._notices = set()
        self.mark("SSST restart", color="yellow", config=self.config.to_dict())
        self._draw()

    def _summary(self):
        def points(kind):
            values = [e["points"] for e in self.events if e["kind"] == kind and e["points"] is not None]
            return sum(values)
        reading = points("speech")
        pitch = points("pitch")
        singing = points("singing line")
        speech = [e["score"] for e in self.events if e["kind"] == "speech" and e["score"] is not None]
        tones = [e["score"] for e in self.events if e["kind"] == "pitch" and e["score"] is not None]
        return {"speech_accuracy": statistics.mean(speech) if speech else None,
                "tone_accuracy": statistics.mean(tones) if tones else None,
                "points": {"reading": reading, "pitch": pitch, "singing": singing,
                           "total": reading + pitch + singing},
                "song_number": self._selected, "song": self._song.display if self._song else None,
                "selection_mode": "participant" if self.config.song == "participant" else "forced",
                "scripted_feedback_shown": self._scripted_shown,
                "singing_completed": any(e["label"] == "SSST singing end" and not e["details"].get("skipped")
                                         and not e["details"].get("aborted") for e in self.timeline)}

    def _finish(self, aborted=False):
        if self.result is not None:
            return
        self._end_phase(aborted=aborted)
        self._media.close()
        if self._voice:
            self._voice.stop()
        if self.state != "summary":
            self.mark("SSST aborted" if aborted else "SSST end", color="yellow", phase=self.state)
        super()._finish(aborted)

    def _redraw_static(self):
        self._draw()

    def _draw_time_bar(self, c, w, h, elapsed, total):
        """Song progress bar, drawn like the MIST response bar."""
        x0, x1, y = w * .25, w * .75, h * .205
        c.create_rectangle(x0, y, x1, y + 14, fill="#292932", outline="")
        c.create_rectangle(x0, y, x0 + (x1 - x0) * max(0.0, min(1.0, elapsed / total)),
                           y + 14, fill="#8ab4c8", outline="")

    def _draw_matched_phrase(self, c, w, h, y):
        """The read-aloud phrase with per-word live coloring.

        Words recognized so far turn green; the rest stay red until they are
        heard. A difflib alignment maps the recognized tokens onto the phrase.
        """
        recognized = " ".join(self._final_text + ([self._partial] if self._partial else []))
        pieces = re.findall(r"[A-Za-z0-9']+", self._phase.text)
        spoken = [token.lower() for token in re.findall(r"[A-Za-z0-9']+", recognized)]
        matcher = difflib.SequenceMatcher(a=[p.lower() for p in pieces], b=spoken,
                                          autojunk=False)
        flags = []
        for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
            flags.extend(tag == "equal" for _ in pieces[i1:i2])
        if not pieces:
            return
        face = tkfont.Font(font=self.font(self.config.font_size or 30))
        widths = [face.measure(piece + " ") for piece in pieces]
        x = w / 2 - sum(widths) / 2
        for piece, width, matched in zip(pieces, widths, flags):
            c.create_text(x, h * y, anchor="w", text=piece, font=face,
                          fill=GREEN if matched else RED)
            x += width

    def _draw_chrome(self, c, w, h):
        """The running points, big and clear, always on top."""
        if self.state in ("summary", "media error"):
            return
        c.create_text(w - 24, h * .055, anchor="e", text=f"{self._points:,} POINTS",
                      fill=self.config.foreground, font=self.font(30))

    def _draw_melody(self, c, w, h, elapsed):
        """Small scrolling pitch bars: the song (amber) and the voice (green/red)."""
        if not self._contour:
            return
        x0, x1 = w * .12, w * .88
        y0, y1 = h * .80, h * .95
        window = 8.0
        slots = int(window / CONTOUR_FRAME)
        slot_w = (x1 - x0) / slots
        c.create_rectangle(x0, y0, x1, y1, fill="#16161c", outline="#2c2c34")
        low, high = math.log2(65), math.log2(900)

        def y_of(hz):
            return y1 - (y1 - y0) * max(0.0, min(1.0, (math.log2(hz) - low) / (high - low)))

        first = int(max(0.0, elapsed - window) / CONTOUR_FRAME)
        for slot in range(first, min(first + slots, len(self._contour))):
            target = self._contour[slot]
            if target is None:
                continue
            x = x0 + (slot * CONTOUR_FRAME - (elapsed - window)) / window * (x1 - x0)
            if x > x1 - slot_w:
                continue
            c.create_rectangle(x + slot_w * .1, y_of(target), x + slot_w * .38, y1,
                               fill="#f2bd62", outline="")
        if self.state == "singing":
            for stamp, hz in self._sing_samples:
                if stamp < elapsed - window:
                    continue
                slot = int(stamp / CONTOUR_FRAME)
                target = self._contour[slot] if 0 <= slot < len(self._contour) else None
                on_target = target is not None and abs(pitch_cents(hz, target)) <= MELODY_TOLERANCE_CENTS
                x = x0 + (stamp - (elapsed - window)) / window * (x1 - x0)
                if x > x1 - slot_w:
                    continue
                c.create_rectangle(x + slot_w * .45, y_of(hz), x + slot_w * .75, y1,
                                   fill=GREEN if on_target else RED, outline="")

    def _draw(self):
        if self._canvas is None:
            return
        c = self._canvas
        c.delete("all")
        w, h = max(640, c.winfo_width()), max(480, c.winfo_height())

        def text(y, content, size=24, color=None):
            return c.create_text(w / 2, h * y, text=content, width=w * .88,
                                 justify="center", fill=color or self.config.foreground, font=self.font(size))

        text(.055, "VOICE RECOGNITION AND MATCHING", 22, "#8ab4c8")
        now = time.monotonic()
        if self.state == "instructions":
            draw_instructions_panel(c, w, h, self.config.background)
            text(.30, "Put on your headphones", 32)
            text(.49, "Read each phrase aloud when asked.\nSit comfortably and keep your hands still.\nYour voice will be used for the matching exercises.")
            text(.78, "SPACE to begin · ESC to finish", 20)
        elif self.state == "reading instructions":
            draw_instructions_panel(c, w, h, self.config.background)
            text(.32, "READING ALOUD", 32)
            text(.50, "A phrase will appear on the screen.\nRead it aloud, clearly and at a comfortable pace.\nA short pause ends the phrase.", 23)
            text(.80, "SPACE to continue", 20)
        elif self.state == "tone instructions":
            draw_instructions_panel(c, w, h, self.config.background)
            text(.32, "PITCH MATCHING", 32)
            text(.50, "You will hear a reference tone.\nHum or sing the same tone back and try to match\nits pitch as closely as you can.", 23)
            text(.80, "SPACE to continue", 20)
        elif self.state == "microphone loading":
            text(.4, "Preparing microphone and English recognition…", 28)
        elif self.state == "media error":
            text(.30, "Audio setup needed", 30, RED)
            text(.5, self._issue, 20)
            text(.76, "ENTER to retry · ESC to finish", 20)
        elif self.state == "summary":
            summary = self._summary()
            text(.23, "EXERCISE COMPLETE", 32)
            text(.34, f"TOTAL: {summary['points']['total']:,} POINTS", 34,
                 GREEN if summary["points"]["total"] > 0 else self.config.foreground)
            reading, pitch, singing = (summary["points"][key] for key in ("reading", "pitch", "singing"))
            text(.46, f"Reading aloud {reading:+,} · Tones {pitch:+,} · Singing {singing:+,}", 22, "#8ab4c8")
            text(.60, f"Word match {summary['speech_accuracy']:.0%} · Tone match {summary['tone_accuracy']:.0%}"
                 if summary["speech_accuracy"] is not None and summary["tone_accuracy"] is not None else "", 20)
            text(.72, "The singing feedback was scripted for the stress exercise.\nIt was not an assessment of your singing ability."
                 if self._scripted_shown else "Thank you for taking part.", 22)
            text(.86, "SPACE to run again · ESC to quit", 20)
        elif self.state == "number selection":
            text(.3, "Choose a number", 36)
            numbers = self._choices()
            for index, number in enumerate(numbers):
                x = w * (.1 + .8 * index / max(1, len(numbers) - 1))
                item = c.create_text(x, h * .52, text=str(number), font=self.font(36), fill="#8ab4c8")
                c.tag_bind(item, "<Button-1>", lambda _event, n=number: self._choose(n))
            text(.7, "Press a number key or click a number.", 20)
        elif self.state in ("read verdict", "tone verdict"):
            # Full-screen check/cross with the points earned (Stroop style).
            if self._verdict is None:
                text(.45, "TRIAL NOT SCORED", 30)
            else:
                correct, delta = self._verdict
                arm = min(w, h) * .12
                draw_verdict(c, w / 2, h * .44, arm, correct)
                c.create_text(w / 2, h * .70, text=f"{delta:+,}",
                              fill=GREEN if delta >= 0 else RED, font=self.font(44))
        elif self.state == "read":
            # Timeout-free reading: no timer, no level bar — a recognized
            # pause ends the trial. Words turn green as they are recognized.
            text(.23, "READ ALOUD", 28)
            self._draw_matched_phrase(c, w, h, .41)
            recognized = " ".join(self._final_text + ([self._partial] if self._partial else []))
            if not self._final_text and not self._partial:
                text(.64, "Listening…", 24, "#8ab4c8")
        elif self.state == "neutral countdown":
            text(.35, "GET READY TO READ", 30)
            text(.55, str(max(0, int(self._deadline - now + .999))), 64, "#f2bd62")
        elif self.state in ("tone listen", "tone matching"):
            text(.23, "LISTEN TO THE TONE" if self.state == "tone listen" else "MATCH THE TONE WITH YOUR VOICE", 26)
            text(.34, "Hum or sing a steady vowel after the reference tone.", 20)
            x0, x1, y = w * .15, w * .85, h * .52
            target = self._phase.target
            tolerance = min(1200, self.config.pitch_tolerance) / 2400
            c.create_rectangle(x0, y, x1, y + 22, fill="#292932", outline="")
            c.create_rectangle(w / 2 - (x1 - x0) * tolerance, y, w / 2 + (x1 - x0) * tolerance,
                               y + 22, fill="#24563a", outline="")
            c.create_line(w / 2, y - 12, w / 2, y + 32, fill="#f2bd62", width=3)
            text(.45, f"TARGET · {target:g} Hz", 20, "#f2bd62")
            if self._pitch:
                cents = pitch_cents(self._pitch, target)
                x = x0 + (x1 - x0) * (max(-1200, min(1200, cents)) + 1200) / 2400
                color = GREEN if abs(cents) <= self.config.pitch_tolerance else RED
                c.create_oval(x - 9, y + 2, x + 9, y + 20, fill=color, outline="")
                text(.65, f"YOUR VOICE · {self._pitch:.0f} Hz · {cents:+.0f} cents", 22, color)
            else:
                text(.65, "Waiting for a steady voice…", 22, "#8ab4c8")
            text(.79, self._last_result, 18)
        elif self.state == "singing instructions":
            draw_instructions_panel(c, w, h, self.config.background)
            text(.30, "NEXT: SING THE SONG", 32)
            text(.42, self._song.display, 26, "#8ab4c8")
            text(.62, "You will hear your song. Sing along out loud,\nfollowing the lyrics on screen.\nEach line you sing earns or loses points.", 23)
            text(.80, "SPACE to continue", 20)
        elif self.state == "anticipation":
            text(.25, "PREPARE TO SING", 32)
            text(.43, self._song.display, 26, "#8ab4c8")
            text(.63, str(max(0, int(self._deadline - now + .999))), 64, "#f2bd62")
        elif self.state in ("preparation", "singing"):
            text(.16, self._song.display, 25, "#8ab4c8")
            self._draw_time_bar(c, w, h, now - self._clip_started + self._clip_offset,
                                self.config.test_time)
            text(.25, "GET READY" if self.state == "preparation" else "SING NOW", 30,
                 None if self.state == "preparation" else "#f2bd62")
            elapsed = now - self._clip_started + self._clip_offset
            index, current, _ = lyric_at(self._lyrics, elapsed - self.config.lyrics_offset)
            upcoming = [line for stamp, line in self._lyrics[max(0, index) + 1:] if line][:UPCOMING_LINES]
            text(.38, current or ("♪" if self._lyrics else "Lyrics not supplied — sing from memory"),
                 self.config.font_size or 34, "#f2bd62")
            for offset, line in enumerate(upcoming):
                text(.47 + offset * .05, line, 22 - 2 * offset, UPCOMING_COLORS[offset])
            if self.state == "singing":
                self._draw_melody(c, w, h, elapsed)
                match = self._lyric_match
                text(.755, f"LYRICS {match:.0%}" if match is not None else "", 18, "#8ab4c8")
                if self._line_deltas:
                    # The points of the line just finished, small, right of it.
                    delta = self._line_deltas[max(self._line_deltas)]
                    c.create_text(w * .74, h * .38, anchor="w", text=f"{delta:+,}",
                                  fill=GREEN if delta >= 0 else RED, font=self.font(18))
            text(.775 if self.state == "preparation" else .785, self._last_feedback, 22, RED)
        else:
            text(.28, "RECOVERY" if self.state == "recovery" else "REST" if self.state == "rest" else "WAIT FOR THE CUE", 28)
            text(.49, "+", 64)
            text(.70, "Sit still and breathe normally." if self.state in ("rest", "recovery") else "Speak only when the countdown reaches zero.", 22)
        if (self._deadline is not None and self.state != "anticipation"
                and self.state not in ("read", "read verdict", "tone verdict",
                                       "neutral countdown")):
            remaining = max(0, self._deadline - now)
            # Instructions show whole seconds only; the phases that need
            # precise timing keep the tenth-of-a-second display.
            shown = (f"{int(remaining + .999)} s" if self.state in INSTRUCTIONS
                     else f"{remaining:.1f} s")
            text(.975 if self.state in ("singing", "preparation") else .88, shown, 22)
        if self._voice and self.state not in ("summary", "singing", "preparation"):
            text(.97, self._audio_notice or self._voice.error or self._voice.recognition_error or self._voice.status, 14, "#8a8a92")
        elif not self.config.voice_enabled and self.state not in ("summary", "singing", "preparation"):
            text(.97, self._audio_notice or "Microphone disabled · responses are not scored", 14, "#8a8a92")
        self._draw_chrome(c, w, h)
