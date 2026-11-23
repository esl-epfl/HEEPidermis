"""SSST protocol, DSP and GUI integration without microphone hardware."""

from dataclasses import fields
from pathlib import Path
import contextlib
import io
import math
import tempfile
import tkinter as tk
import shutil
import unittest
from unittest.mock import patch

from gui.tests import available_tests, load_test
from gui.tests.common import settings
from gui.tests.common.vocal import estimate_pitch, phrase_score
from gui.tests.SSST.assets import (discover_songs, lyric_at, read_lyrics, read_segment,
                                   segment_lyrics, song_contour)
from gui.tests.SSST.spec import Config, FORM, SONG_OPTIONS
from gui.tests.SSST.test import Test, phases
from gui.sdk.tests.panel import TestsPanel


class ContractTests(unittest.TestCase):
    def test_discovery_defaults_and_complete_form(self):
        entry = available_tests()["ssst"]
        self.assertTrue(entry["implemented"])
        self.assertIs(load_test("ssst"), Test)
        self.assertEqual({f.name for f in fields(Config)}, {f["key"] for f in FORM if "key" in f})
        values = settings.config_kwargs("ssst", settings.load_settings(entry["folder"] / "configs/defaults.hjson"), Config)
        self.assertEqual(Config(**values), Config())

    def test_configuration_rejects_invalid_timing_and_input(self):
        for values in ({"lead_in": 60}, {"test_time": 0}, {"pitch_tolerance": 0},
                       {"tone_frequencies": "nan"}, {"tone_frequencies": "12"},
                       {"song": "missing"}, {"phrases": "one||two"},
                       {"mic_threshold": float("nan")}, {"feedback_interval": 0},
                       {"voice_enabled": "yes"}, {"instruction_time": -1},
                       {"pause_time": -1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config(**values)

    def test_forced_song_bypasses_selection_and_anticipation_is_separate(self):
        names = [p.name for p in phases(Config())]
        self.assertLess(names.index("number selection"), names.index("singing instructions"))
        self.assertEqual(names[names.index("singing instructions"):][:4],
                         ["singing instructions", "anticipation", "preparation", "singing"])
        # Instructions first, then the read-aloud trials; the vacuum-cleaner
        # countdown practice is gone, and phrases are read, never played.
        self.assertEqual(names[0], "reading instructions")
        self.assertNotIn("listen", names)
        self.assertNotIn("speak", names)
        self.assertNotIn("practice instructions", names)
        self.assertEqual(names.count("read"), len(Config().phrases.split("|")))
        forced = phases(Config(song=SONG_OPTIONS[1]))
        self.assertNotIn("number selection", [p.name for p in forced])
        self.assertEqual(sum(p.duration for p in forced if p.name in ("singing", "preparation")), 60)


class AssetsTests(unittest.TestCase):
    def test_numbered_files_and_optional_tenth_song(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("0_baby_one_more_time--britney_spears.mp3", "9_extra_song--artist.wav", "0_lyrics.txt"):
                (root / name).touch()
            library = discover_songs(root)
            self.assertEqual(set(library), {0, 9})
            self.assertEqual(library[9].display, "EXTRA SONG, by Artist")
            (root / "0_duplicate--artist.wav").touch()
            with self.assertRaisesRegex(ValueError, "Multiple"):
                discover_songs(root)

    def test_lyrics_boundaries_repeats_blank_lines_and_seek(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "0_lyrics.txt"
            path.write_text("[ti:Example]\n[00:08.50][00:40.00]First line\n[00:12.20]Second line\n[00:18.00]\n25.0\tNext line\n")
            lyrics = read_lyrics(path)
            self.assertEqual(lyric_at(lyrics, 8.49)[1:], ("", "First line"))
            self.assertEqual(lyric_at(lyrics, 8.5)[1:], ("First line", "Second line"))
            self.assertEqual(lyric_at(lyrics, 20)[1:], ("", "Next line"))
            self.assertEqual(lyric_at(lyrics, 50)[1:], ("First line", ""))
            path.write_text("not timestamped")
            with self.assertRaisesRegex(ValueError, "0_lyrics.txt:1"):
                read_lyrics(path)

    def test_segment_selects_the_excerpt_and_rebases_lyrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(read_segment(root / "0_segment.txt"), 0.0)  # Missing = start.
            (root / "0_segment.txt").write_text("# chorus enters at 0:31\n[00:31.20]\n")
            self.assertEqual(read_segment(root / "0_segment.txt"), 31.2)
            (root / "0_segment.txt").write_text("[00:31.20]\n[00:45.00]\n")
            with self.assertRaisesRegex(ValueError, "one segment"):
                read_segment(root / "0_segment.txt")
            (root / "0_segment.txt").write_text("31.2\n")
            with self.assertRaisesRegex(ValueError, "mm:ss"):
                read_segment(root / "0_segment.txt")
        source = [(0.0, ""), (30.0, "line already in progress"), (45.0, "inside"),
                  (95.0, "just beyond the excerpt"), (120.0, "far beyond")]
        excerpt = segment_lyrics(source, 31.2, 60)
        self.assertEqual(excerpt, [(0.0, "line already in progress"), (13.8, "inside")])
        self.assertEqual(segment_lyrics([(5.0, "before")], 31.2, 60), [(0.0, "before")])
        self.assertEqual(segment_lyrics([], 10, 60), [])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is optional")
    def test_song_contour_tracks_a_synth_tone(self):
        import numpy as np
        from gui.tests.common.audio import RATE, _write_wav
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tone.wav"
            t = np.arange(RATE * 2) / RATE
            _write_wav(path, (.4 * np.sin(2 * math.pi * 440 * t)).tolist())
            contour = song_contour(path, 0, 2.0)
        self.assertEqual(len(contour), 20)
        voiced = [hz for hz in contour if hz is not None]
        self.assertGreaterEqual(len(voiced), 15)
        for hz in voiced:
            self.assertLess(abs(1200 * math.log2(hz / 440)), 50)

    def test_song_contour_is_empty_without_media_support(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.wav"
            path.touch()
            self.assertEqual(song_contour(path, 0, 1.0), [])
        with patch("gui.tests.SSST.assets.shutil.which", return_value=None):
            self.assertEqual(song_contour("anything.wav", 0, 1.0), [])


class PitchTests(unittest.TestCase):
    def test_pitch_is_accurate_for_tones_and_vocal_harmonics(self):
        import numpy as np
        t = np.arange(1600) / 16000
        for hz in (90, 150, 220, 440, 700):
            for harmonic in (0, .65):
                signal = .25 * (np.sin(2 * math.pi * hz * t) + harmonic * np.sin(4 * math.pi * hz * t))
                actual = estimate_pitch(signal)
                self.assertIsNotNone(actual)
                self.assertLess(abs(1200 * math.log2(actual / hz)), 8)

    def test_silence_and_noise_are_not_scored_as_tones(self):
        import numpy as np
        self.assertIsNone(estimate_pitch(np.zeros(1600)))
        self.assertIsNone(estimate_pitch(np.random.default_rng(7).normal(0, .1, 1600)))

    def test_speech_scoring_counts_order_and_repetition(self):
        self.assertEqual(phrase_score("Vacuum cleaner!", "vacuum cleaner"), 1)
        self.assertEqual(phrase_score("vacuum cleaner vacuum cleaner", "vacuum cleaner"), .5)
        self.assertEqual(phrase_score("vacuum cleaner", ""), 0)
        self.assertLess(phrase_score("a blue cup", "cup blue a"), 1)


class FakeMedia:
    def __init__(self, clock):
        self.clock = clock
        self.started = 0
        self.error = ""
        self.failed = False
        self.playing = False
        self.calls = []

    def play(self, path, offset=0, duration=None):
        self.started = self.clock()
        self.playing = True
        self.calls.append((str(path), offset, duration))
        return True

    def speak(self, text):
        self.playing = False
        return True

    def tone(self, frequency, duration):
        self.playing = False
        return True

    def stop(self):
        self.playing = False

    close = stop


class FakeVoice:
    ready = True
    error = ""
    recognition_error = ""
    status = "Microphone ready"

    def __init__(self, *args):
        self.results = []
        self.stopped = False

    def start(self):
        pass

    def listen(self, mode):
        self.results.clear()

    def drain(self):
        values, self.results = self.results, []
        return values

    def stop(self):
        self.stopped = True


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        (self.folder / "0_baby_one_more_time--britney_spears.wav").touch()
        (self.folder / "0_lyrics.txt").write_text("[00:08.00]First line\n[00:15.00]Next line\n")
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.now = 100.0
        self.media = FakeMedia(lambda: self.now)
        for target, value in (("gui.tests.SSST.test.time.monotonic", lambda: self.now),
                              ("gui.tests.SSST.test.MediaPlayer", lambda: self.media),
                              ("gui.tests.SSST.test.VocalMonitor", FakeVoice)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def make_task(self, **values):
        task = Test(Config(songs_dir=str(self.folder), **values))
        task.run(parent=self.root, event_path=self.folder / "events.jsonl")
        self.addCleanup(lambda: task._finish(aborted=task.state != "summary"))
        return task

    def advance_time(self, task):
        if task._deadline is None:
            # Timeout-free read trial: a pause longer than pause_time ends it.
            self.now += 2.0
        else:
            self.now = task._deadline + .01
        task._tick()

    def test_full_run_real_scores_karaoke_and_scripted_feedback_are_separate(self):
        task = self.make_task()
        task._handle_key("space")
        visited = []
        while task.state != "summary":
            visited.append(task.state)
            if task.state == "number selection":
                task._handle_key("0")
                continue
            if task.state in ("read", "tone matching"):
                task._voice.results = [{"at": self.now + .01, "level": .1,
                                        "pitch": task._phase.target or None,
                                        "text": task._phase.text, "final": True, "overflow": False}]
            if task.state == "singing":
                task._voice.results = [{"at": self.now, "level": .1, "pitch": None,
                                        "text": "First line", "final": True, "overflow": False}]
                self.now += 10  # Past the second line (15 s) and the feedback delay.
                task._tick()
                # The finished karaoke line earned its points right away.
                self.assertEqual(task._lyric_index, 1)
                self.assertEqual(task._line_deltas[0], 500)
                self.assertTrue(task._scripted_shown)
                task._voice.results = [{"at": self.now, "level": .1, "pitch": None,
                                        "text": "Next line", "final": True, "overflow": False}]
            self.advance_time(task)
            self.assertNotEqual(task.state, "media error")
        self.assertIn("anticipation", visited)
        self.assertNotIn("listen", visited)
        self.assertNotIn("speak", visited)  # The vacuum-cleaner practice is gone.
        self.assertEqual(len(self.media.calls), 1)  # No restart at the singing cue.
        summary = task._summary()
        self.assertEqual(summary["speech_accuracy"], 1)
        self.assertEqual(summary["tone_accuracy"], 1)
        self.assertTrue(summary["singing_completed"])
        self.assertTrue(task._voice.stopped)
        # Points, all perfect: 3 read-aloud trials (3 × +500), each tone
        # trial captured one 100 ms frame in tune (4 × +100), and each sung
        # karaoke line matched (2 × +500). The 50% match mark is worth 0.
        self.assertEqual(summary["points"]["reading"], 1_500)
        self.assertEqual(summary["points"]["pitch"], 400)
        self.assertEqual(summary["points"]["singing"], 1_000)
        self.assertEqual(summary["points"]["total"], 2_900)
        self.assertTrue(all(e["scripted"] for e in task.events if e["kind"] == "singing feedback"))
        labels = [e["label"] for e in task.timeline]
        for label in ("SSST anticipation start", "SSST anticipation end", "SSST singing start", "SSST singing end", "SSST end"):
            self.assertIn(label, labels)
        task._finish()
        task.result.to_json()  # Complete result must be serializable.

    def test_check_cross_verdicts_and_pause_finish_read_aloud(self):
        task = self.make_task()
        task._handle_key("space")
        while task.state != "read":
            task._handle_key("space")
        # A poor match is a red cross with negative points (1% = 100 points).
        task._voice.results = [{"at": self.now + .01, "level": .1, "pitch": None,
                                "text": "completely different words here", "final": True,
                                "overflow": False}]
        self.advance_time(task)  # A recognized pause ends the timeout-free trial.
        self.assertEqual(task.state, "read verdict")
        correct, delta = task._verdict
        self.assertFalse(correct)
        self.assertLess(delta, 0)
        self.assertEqual(task._points, delta)
        scored = [e for e in task.events if e["kind"] == "speech" and e["points"] is not None]
        self.assertEqual(scored[0]["points"], delta)
        # SPACE skips the verdict screen into the next countdown.
        while task.state != "read":
            task._handle_key("space")
        task._voice.results = [{"at": self.now, "level": .1, "pitch": None,
                                "text": task._phase.text, "final": True, "overflow": False}]
        self.now += .5
        task._tick()
        self.assertEqual(task.state, "read")
        self.now += 1.5  # pause_time (1.2 s) after the last voice activity.
        task._tick()
        self.assertEqual(task.state, "read verdict")
        self.assertLess(self.now, task._deadline + .01)  # Verdict just began.
        self.assertEqual(task._verdict, (True, 500))
        self.assertEqual(task._points, delta + 500)

    def test_singing_scores_lines_and_draws_feedback_bars(self):
        (self.folder / "0_lyrics.txt").write_text("[00:08.00]hit me baby one more time\n[00:15.00]hit me baby one more time\n")
        task = self.make_task(song="0", voice_enabled=True)
        task._handle_key("space")
        while task.state != "singing":
            task._handle_key("space")
        self.now += 4
        task._tick()
        # A live partial with pitch: recognized words and one singer bar.
        task._voice.results = [{"at": self.now, "level": .1, "pitch": 220.0,
                                "text": "hit me baby", "final": False, "overflow": False}]
        task._tick()
        self.assertEqual(len(task._sing_samples), 1)
        self.assertIsNotNone(task._lyric_match)
        # The feedback bars render with an approximate song contour.
        task._contour = [220.0] * 600
        task._draw()
        # Crossing into the next line scores the finished line immediately.
        task._voice.results = [{"at": self.now + 1, "level": .1, "pitch": None,
                                "text": "hit me baby one more time", "final": True,
                                "overflow": False}]
        self.now += 8  # Elapsed clip time 16 s: past the second line's start.
        task._tick()
        self.assertEqual(task._line_deltas[0], 500)
        # The line in progress at the end is scored when singing finishes.
        task._voice.results = [{"at": self.now + 1, "level": .1, "pitch": None,
                                "text": "hit me baby one more time", "final": True,
                                "overflow": False}]
        while task.state != "summary":
            self.advance_time(task)
        lines = [e for e in task.events if e["kind"] == "singing line"]
        self.assertEqual([e["points"] for e in lines], [500, 500])
        self.assertEqual(task._summary()["points"]["singing"], 1_000)

    def test_summary_offers_restart_with_a_fresh_run(self):
        task = self.make_task(song="0", voice_enabled=False, instruction_time=.5,
                              neutral_countdown=0, response_time=1, tone_frequencies="",
                              rest_time=0, recovery_time=0, anticipation_time=0,
                              test_time=10, lead_in=5)
        task._handle_key("space")
        while task.state != "summary":
            self.advance_time(task)
        run_id = task._run_id
        task._points = 500
        task._handle_key("space")  # "SPACE to run again" on the end screen.
        self.assertEqual(task.state, "instructions")
        self.assertEqual(task._points, 0)
        self.assertEqual(task.events, [])
        self.assertNotEqual(task._run_id, run_id)
        task._handle_key("space")  # The fresh run starts from the instructions.
        self.assertEqual(task.state, "reading instructions")

    def test_space_skips_all_sections_and_forced_song_skips_selection(self):
        task = self.make_task(song=SONG_OPTIONS[1], voice_enabled=False)
        task._handle_key("space")
        states = []
        while task.state != "summary":
            states.append(task.state)
            task._handle_key("space")
        self.assertNotIn("number selection", states)
        self.assertEqual([call[1] for call in self.media.calls], [0, 8])
        # Playback is bounded to the excerpt: full source tracks must stop at
        # test_time instead of running to the end of the file.
        self.assertEqual([call[2] for call in self.media.calls], [60, 52])
        self.assertFalse(task._summary()["singing_completed"])
        self.assertTrue(all(e["score"] is None for e in task.events if "score" in e))
        endings = [e for e in task.timeline if e["label"].endswith(" end") and e["label"] != "SSST end"]
        self.assertTrue(all(e["details"]["skipped"] for e in endings))

    def test_missing_media_can_be_retried_after_file_is_supplied(self):
        task = self.make_task(song="2", voice_enabled=False)
        task._handle_key("space")
        while task.state != "media error":
            task._handle_key("space")
        self.assertIn("Missing clip 2", task._issue)
        self.assertIsNone(task._deadline)
        (self.folder / "2_baby--justin_bieber.mp3").touch()
        task._handle_key("Return")
        self.assertEqual(task.state, "singing instructions")
        self.assertEqual(task._selected, 2)

    def test_first_lyric_sets_each_songs_singing_cue(self):
        # A first lyric after the configured lead-in delays the cue to it.
        (self.folder / "0_lyrics.txt").write_text("[00:12.00]First line\n")
        task = self.make_task(song="0", voice_enabled=False)
        task._handle_key("space")
        while task.state != "preparation":
            task._handle_key("space")
        self.assertEqual(task._deadline - task._clip_started, 12)
        task._handle_key("space")
        self.assertEqual(self.media.calls[-1][1], 12)
        self.assertEqual(self.media.calls[-1][2], 48)
        self.assertEqual(task._deadline - self.now, 48)
        # A clip opening with vocals keeps its minimum preparation time.
        task2 = self.make_task(song="0", voice_enabled=False)
        (self.folder / "0_lyrics.txt").write_text("[00:00.00]Immediate line\n")
        task2._handle_key("space")
        while task2.state != "singing":
            task2._handle_key("space")
        self.assertEqual(self.media.calls[-1][1], 8)

    def test_audio_failure_stops_song_and_abort_keeps_results(self):
        task = self.make_task(song=SONG_OPTIONS[1])
        task._handle_key("space")
        while task.state != "singing":
            task._handle_key("space")
        self.media.failed = True
        task._tick()
        self.assertEqual(task.state, "media error")
        self.assertFalse(task._summary()["singing_completed"])
        task._finish(aborted=True)
        self.assertTrue(task.result.aborted)
        self.assertTrue(task._voice.stopped)
        self.assertFalse(self.media.playing)
        self.assertEqual(task.result.timeline[-1]["label"], "SSST aborted")

    def test_gui_form_persists_song_and_lyrics_settings(self):
        panel = TestsPanel(self.root, host=tk.Frame(self.root), reports=tk.Frame(self.root))
        self.addCleanup(panel.close)
        panel.apply_configuration({"test": "SSST", "values": {"song": "3", "lyrics_offset": "0.25"}})
        snapshot = panel.configuration_snapshot()
        self.assertEqual(snapshot["values"]["song"], "3")
        self.assertTrue(all(not widget.instate(["disabled"]) for _, widgets in panel._section_rules for widget in widgets))
        panel.form_values["negative_feedback"].set(False)
        feedback_widgets = next(widgets for rule, widgets in panel._section_rules if rule["key"] == "negative_feedback")
        self.assertTrue(all(widget.instate(["disabled"]) for widget in feedback_widgets))
        path = self.folder / "ssst.hjson"
        with patch("gui.sdk.tests.panel.filedialog.asksaveasfilename", return_value=str(path)):
            panel.save_config_file()
        config = Config(**settings.config_kwargs("ssst", settings.load_settings(path), Config))
        self.assertEqual(config.song, "3")
        self.assertEqual(config.lyrics_offset, .25)
        with patch("gui.sdk.tests.panel.subprocess.Popen") as process:
            panel.launch()
        self.assertIn("ssst", process.call_args.args[0])
        # The --flag/value pair stays adjacent (the song value contains spaces).
        arguments = process.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--song") + 1], "3")
        panel.close_task()

    def test_dropdown_lists_the_songs_that_actually_exist(self):
        (self.folder / "5_wannabe--spice_girls.mp3").touch()
        panel = TestsPanel(self.root, host=tk.Frame(self.root), reports=tk.Frame(self.root))
        self.addCleanup(panel.close)
        panel.test_selection.set("SSST")
        panel._build_form()
        field = next(f for f in FORM if f.get("key") == "song")
        # Options resolve against the configured folder and refresh on open.
        panel.form_values["songs_dir"].set(str(self.folder))
        options = panel._dropdown_options(field)
        self.assertEqual(options[0], "participant")
        self.assertIn("0 — Baby One More Time, by Britney Spears", options)
        self.assertIn("5 — Wannabe, by Spice Girls", options)
        panel.close_task()


if __name__ == "__main__":
    unittest.main()
