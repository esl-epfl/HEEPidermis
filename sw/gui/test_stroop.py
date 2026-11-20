"""Stroop event device: feed reading, colored markers, and workspace round trip.

Run with: python3 -m unittest discover -s . -p 'test_stroop.py'  (from sw/gui)
"""

import csv
import dataclasses
import json
import math
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui import app
from gui.common.plots import TimePlot, TimeViewport, Trace
from gui.common.session import metadata_path, read_session, write_session
from gui.common.timing import AlignedSnapshot
from gui.sdk.tests.panel import DEFAULT_PATH, TestsPanel, validate_configuration
from gui.sdk.tests.recording import COLUMNS, Event, EventTail, read_recording

try:
    tk.Tk().destroy()
except tk.TclError as error:  # headless CI or a missing python3-tk
    raise unittest.SkipTest(f"Tk display unavailable: {error}") from error


def message(label, sequence, *, run_id="run-1", test="stroop", at=None, unix=None,
            color="green", **details):
    stamp = time.monotonic() + 0.01 if at is None else at
    return {"schema_version": 2, "run_id": run_id, "test": test, "sequence": sequence,
            "label": label, "color": color, "monotonic_s": stamp,
            "unix_s": time.time() + 1 if unix is None else unix, "details": details}


class EventTests(unittest.TestCase):
    def event(self, **changes):
        base = message("test start", 0)
        return Event.from_message({**base, **changes}, time.monotonic() - 1)

    def test_valid_event_keeps_label_relative_time_and_color(self):
        origin = 997.5
        event = Event.from_message(message("correct", 3, at=1000.0, trial=4), origin)
        self.assertEqual(event.label, "correct")
        self.assertEqual(event.color, "green")
        self.assertAlmostEqual(event.time_s, 2.5, delta=0.001)
        self.assertEqual(event.details, {"trial": 4})
        self.assertEqual(list(event.row())[:5],
                         [event.time_s, "correct", "green", event.monotonic_s, event.unix_s])
        self.assertEqual(event.hex_color(), "#3fbf5f")  # Palette name resolved.

    def test_labels_and_tests_are_free_form_and_colors_fall_back(self):
        event = self.event(label="n-back stimulus", test="n-back", color="#123456")
        self.assertEqual(event.label, "n-back stimulus")
        self.assertEqual(event.test, "n-back")
        self.assertEqual(event.hex_color(), "#123456")  # Hex passthrough.
        legacy = Event.from_message({**{k: v for k, v in message("rest", 1).items()
                                        if k != "color"}, "schema_version": 1}, 0.0)
        self.assertEqual(legacy.color, "white")  # Schema 1 feeds predate colors.
        self.assertEqual(legacy.hex_color(), "#ffffff")

    def test_invalid_events_are_rejected(self):
        for changes in ({"schema_version": 99}, {"label": ""}, {"label": 7},
                        {"test": ""}, {"monotonic_s": float("nan")}, {"sequence": -1},
                        {"run_id": ""}, {"details": []}, {"color": ""}, {"color": 9}):
            with self.assertRaises((ValueError, KeyError, TypeError)):
                self.event(**changes)

    def test_recording_round_trip_and_duplicate_rejection(self):
        events = [self.event(), Event.from_message(message("wrong", 1), time.monotonic() - 1)]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "tests.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS)
            for event in events:
                writer.writerow(event.row())
        loaded = read_recording(path)
        self.assertEqual([e.label for e in loaded], ["test start", "wrong"])
        self.assertEqual([e.run_id for e in loaded], ["run-1", "run-1"])
        rows = path.read_text().splitlines()
        with path.open("a", encoding="utf-8") as stream:
            stream.write(rows[1] + "\n")
        with self.assertRaises(ValueError):
            read_recording(path)

    def test_recording_requires_test_columns(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "bad.csv"
        path.write_text("time_s,other\n1,2\n")
        with self.assertRaises(ValueError):
            read_recording(path)

    def test_configuration_validation(self):
        validate_configuration({})
        validate_configuration({"test": "Stroop", "launch_options": "--seed 7",
                                 "event_path": "/tmp/x.jsonl", "show_stimuli": True,
                                 "show_start_stop": False, "show_verdicts": False,
                                 "show_phases": True,
                                 "values": {"test_time": "60", "response_mode": "voice"}})
        for values in ({"test": "n-back"}, {"launch_options": "--bad 'quote"},
                       {"event_path": 3}, {"show_stimuli": "yes"},
                       {"show_verdicts": "no"}, {"show_start_stop": 1},
                       {"values": {"bogus": 1}}, {"values": {"test_time": {}}}, []):
            with self.assertRaises(ValueError):
                validate_configuration(values)


class TailTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "events.jsonl"
        self.tail = EventTail(self.path)

    def poll_all(self):
        messages = []
        for _ in range(3):
            messages.extend(self.tail.poll())
        return messages

    def test_partial_lines_wait_for_completion(self):
        self.path.write_text('{"a": 1')
        self.assertEqual(self.poll_all(), [])
        with self.path.open("a") as stream:
            stream.write('}\n{"a": 2}\n')
        self.assertEqual([m["a"] for m in self.poll_all()], [1, 2])

    def test_rotation_reopens_the_new_file(self):
        self.path.write_text('{"a": 1}\n')
        self.assertEqual(self.poll_all(), [{"a": 1}])
        rotated = self.path.with_suffix(".old")
        self.path.rename(rotated)
        self.path.write_text('{"a": 2}\n')
        self.assertEqual(self.poll_all(), [{"a": 2}])

    def test_truncation_restarts_from_the_beginning(self):
        self.path.write_text('{"a": 1}\n{"a": 2}\n')
        self.poll_all()
        self.path.write_text('{"a": 9}\n')
        self.assertEqual(self.poll_all(), [{"a": 9}])

    def test_malformed_lines_do_not_lose_following_events(self):
        self.path.write_text('not json\n{"a": 3}\n')
        self.assertEqual(self.poll_all(), [None, {"a": 3}])


class PanelTests(unittest.TestCase):
    """Drive the real listener thread against a temporary event feed."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.panel = TestsPanel(self.root, host=tk.Frame(self.root),
                                 reports=tk.Frame(self.root))
        self.panel.test_selection.set("Stroop")
        self.panel._build_form()
        self.addCleanup(self.panel.close)
        self.feed = Path(self.directory.name) / "events.jsonl"
        self.panel.event_path.set(str(self.feed))

    def pump(self, seconds=0.8):
        limit = time.monotonic() + seconds
        while time.monotonic() < limit:
            self.root.update()
            time.sleep(0.02)
            if self.panel.messages.empty() and time.monotonic() > limit - seconds / 2:
                break

    def test_collect_filters_and_deduplicates_feed_events(self):
        self.panel.connect()
        good = message("test start", 0)
        stale = message("rest", 1, unix=time.time() - 10_000, at=time.monotonic() - 10_000)
        with self.feed.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(good) + "\n")
            stream.write("not json\n")
            stream.write(json.dumps({**message("bogus", 2), "label": 7}) + "\n")
            stream.write(json.dumps(stale) + "\n")
            stream.write(json.dumps(good) + "\n")  # Duplicate delivery.
        self.pump()
        self.assertEqual([e.label for e in self.panel.events], ["test start"])
        self.assertEqual(len(self.panel.seen), 1)
        self.assertIn("1 events", self.panel.count_text.get())
        self.assertTrue(self.panel.connected)

    def test_marker_groups_can_be_hidden_independently(self):
        self.panel.connect()
        colors = {"test start": "yellow", "stimulus": "white",
                  "correct": "green", "wrong": "red", "rest": "white"}
        labels = tuple(colors)
        with self.feed.open("w", encoding="utf-8") as stream:
            for sequence, label in enumerate(labels):
                stream.write(json.dumps(message(label, sequence, color=colors[label])) + "\n")
        self.pump()
        snapshot = self.panel.plot_snapshot({"events"})
        # Defaults: everything except stimulus onsets.
        self.assertEqual([label for _, label, _c in snapshot.get_markers(0, 1e9)],
                         ["test start", "correct", "wrong", "rest"])
        self.assertEqual(snapshot.revision, (5, (True, True, False, True)))
        self.panel.show_stimuli.set(True)
        # Snapshots are frozen; the toggle takes effect on the next snapshot.
        refreshed = self.panel.plot_snapshot({"events"})
        self.assertEqual([label for _, label, _c in refreshed.get_markers(0, 1e9)], list(labels))
        self.assertEqual(refreshed.revision, (5, (True, True, True, True)))
        # Each remaining group hides on its own, in any combination.
        self.panel.show_stimuli.set(False)
        self.panel.show_verdicts.set(False)
        verdicts_hidden = self.panel.plot_snapshot({"events"})
        self.assertEqual([label for _, label, _c in verdicts_hidden.get_markers(0, 1e9)],
                         ["test start", "rest"])
        self.panel.show_phases.set(False)
        only_start = self.panel.plot_snapshot({"events"})
        shown = only_start.get_markers(0, 1e9)
        self.assertEqual([label for _t, label, _c in shown], ["test start"])
        self.assertEqual(shown[0][2], "#f2bd62")  # Start/stop markers are yellow.
        self.panel.show_start_stop.set(False)
        none_left = self.panel.plot_snapshot({"events"})
        self.assertEqual(none_left.get_markers(0, 1e9), [])
        # The events series (CSV export shape) is never filtered.
        series = snapshot.get_series()["events"]
        self.assertEqual(series["labels"], list(labels))
        self.assertEqual(series["kind"], "events")
        windowed = snapshot.get_series(series["times"][2], series["times"][2])
        self.assertEqual(windowed["events"]["labels"], ["correct"])

    def test_snapshot_series_window_excludes_delayed_insertions(self):
        self.panel.connect()
        # A late voice verdict must land between its neighbours by timestamp.
        for sequence, (label, delay) in enumerate((("test start", 0.0), ("wrong", 0.5),
                                                   ("correct", 0.1))):
            base = message(label, sequence, at=self.panel.clock_origin + 1 + delay)
            self.panel.messages.put(base)
        for _ in range(40):
            self.root.update()
            time.sleep(0.01)
        self.assertEqual([e.label for e in self.panel.events], ["test start", "correct", "wrong"])
        self.assertEqual(self.panel.times, sorted(self.panel.times))

    def test_open_button_launches_the_test_and_close_stops_everything(self):
        self.assertEqual(self.panel.connect_labels, ("Open", "Close"))
        with patch("gui.sdk.tests.panel.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            self.panel.connect_toggle()  # The device's "Open" button.
        popen.assert_called_once()
        self.assertTrue(self.panel.connected)
        self.assertEqual(self.panel.connection(), ("ok", "Test open · listening"))
        self.panel.connect_toggle()  # "Close" ends the test and the listener.
        self.assertFalse(self.panel.connected)
        self.assertIsNone(self.panel.process)
        self.assertEqual(self.panel.connection(), ("off", "Closed"))

    def test_open_also_listens_when_a_window_is_already_running(self):
        self.panel.process = SimpleNamespace(poll=Mock(return_value=None))
        self.panel.launch()  # Regression: Open used to skip connecting here.
        self.assertTrue(self.panel.connected)
        self.assertEqual(self.panel.connection(), ("ok", "Test open · listening"))
        self.assertIn("already open", self.panel.status.get())

    def test_window_exit_is_observed_and_clears_the_state(self):
        self.panel.connect()
        self.assertEqual(self.panel.connection(), ("ok", "Listening for events"))
        self.panel.process = SimpleNamespace(poll=Mock(return_value=0))
        self.panel._pump()
        self.panel.close()
        self.assertIsNone(self.panel.process)
        self.assertFalse(self.panel.connected)
        self.assertEqual(self.panel.connection(), ("off", "Closed"))

    def test_common_stop_reports_a_still_open_window_honestly(self):
        self.panel.transport("start")
        self.panel.process = SimpleNamespace(poll=Mock(return_value=None))
        self.assertEqual(self.panel.connection(), ("ok", "Test open · listening"))
        self.panel.transport("stop")  # Common Stop only stops the listener.
        self.assertFalse(self.panel.connected)
        self.assertEqual(self.panel.connection(), ("ok", "Test open · not listening"))

    def test_failed_launch_does_not_leave_a_half_open_device(self):
        with patch("gui.sdk.tests.panel.subprocess.Popen",
                   side_effect=OSError("no such file")):
            self.panel.connect_toggle()
        self.assertFalse(self.panel.connected)
        self.assertIn("Could not open", self.panel.status.get())

    def test_transport_toggles_the_listener(self):
        self.panel.transport("start")
        self.assertTrue(self.panel.connected)
        self.assertEqual(self.panel.transport_state(), "running")
        self.panel.transport("pause")
        self.assertFalse(self.panel.connected)
        self.assertEqual(self.panel.transport_state(), "stopped")

    def test_launch_runs_the_selected_test_with_the_feed_path(self):
        with patch("gui.sdk.tests.panel.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            self.panel.test_selection.set("Stroop")
            self.panel.launch_options.set("--seed 7")
            self.panel.launch()
        (args,), kwargs = popen.call_args
        self.assertTrue(args[1].endswith("run_test.py"))
        self.assertEqual(args[2], "stroop")
        self.assertIn("--seed", args)
        self.assertNotIn("--response-mode", args)  # Keys mode by default.
        self.assertEqual(args[args.index("--events") + 1], str(self.feed))
        self.assertTrue(self.panel.connected)
        # A still-running test is never launched twice.
        with patch("gui.sdk.tests.panel.subprocess.Popen") as popen:
            self.panel.launch()
        popen.assert_not_called()
        self.assertIn("already open", self.panel.status.get())

    def test_form_options_become_launch_flags(self):
        mode = "keys" if self.panel.form_base["response_mode"] == "voice" else "voice"
        with patch("gui.sdk.tests.panel.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            self.panel.apply_configuration({"test": "Stroop",
                                            "values": {"test_time": "90",
                                                       "response_mode": mode,
                                                       "voice_calibration": False}})
            self.panel.launch()
        (args,), _ = popen.call_args
        self.assertEqual(args[args.index("--test-time") + 1], "90")
        self.assertEqual(args[args.index("--response-mode") + 1], mode)
        self.assertEqual(args[args.index("--voice-calibration") + 1], "false")
        # Defaults are not passed: the test's own configuration stays the truth.
        self.assertNotIn("--rest-time", args)
        self.assertNotIn("--n-colors", args)
        self.panel.close_task()     # The first window is gone; start fresh.
        self.panel._build_form()    # A fresh form holds only the defaults.
        with patch("gui.sdk.tests.panel.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            self.panel.launch()     # Back to the defaults: no flags at all.
        (args,), _ = popen.call_args
        self.assertNotIn("--response-mode", args)
        self.assertNotIn("--voice-calibration", args)

    def test_form_covers_the_whole_config_and_starts_at_defaults(self):
        entry = self.panel._entry()
        fields = {f.name for f in dataclasses.fields(entry["spec"].Config)} - {"color_hexes"}
        self.assertEqual(set(self.panel.form_values), fields)  # Everything is modifiable.
        base = self.panel.form_base
        self.assertEqual(base["test_time"], 120.0)     # From configs/defaults.hjson.
        self.assertEqual(base["n_colors"], 9)
        self.assertEqual(self.panel.form_values["test_time"].get(), "120")
        # A pristine form sends no flags at all.
        self.assertEqual(self.panel._form_flags(entry["spec"]), [])
        # Checkboxes carry their own label: box and text share one line.
        from gui.tests.Stroop import spec as stroop_spec
        for field in stroop_spec.FORM:
            if field.get("widget") == "checkbox":
                widget = next(w for w in self.panel.form_frame.winfo_children()
                              if isinstance(w, ttk.Checkbutton)
                              and field["label"] in str(w.cget("text")))
                self.assertIn(field["label"], widget.cget("text"))

    def test_configuration_files_load_and_save(self):
        entry = self.panel._entry()
        configs = self.panel._configs_dir(entry)
        self.assertTrue((configs / "defaults.hjson").exists())
        self.assertEqual(self.panel.config_selection.get(), "defaults.hjson")
        # Load… opens a file explorer (like the workspace's "Load
        # configuration…") and can load any .hjson on disk.  The form is
        # first reset to the test's defaults, then the file is applied.
        self.panel.form_values["test_time"].set("90")
        defaults = str(configs / "defaults.hjson")
        with patch("gui.sdk.tests.panel.filedialog.askopenfilename",
                   return_value=defaults) as dialog:
            self.panel.load_config_file()
        dialog.assert_called_once()
        self.assertIn("*.hjson", str(dialog.call_args))
        self.assertEqual(self.panel.form_values["test_time"].get(), "120")
        self.assertEqual(self.panel.config_selection.get(),
                         "sw/gui/tests/Stroop/configs/defaults.hjson")
        # Cancelling the explorer changes nothing.
        with patch("gui.sdk.tests.panel.filedialog.askopenfilename", return_value=""):
            self.panel.load_config_file()
        self.assertEqual(self.panel.config_selection.get(),
                         "sw/gui/tests/Stroop/configs/defaults.hjson")
        # Save as… writes the current form values through its own explorer.
        self.panel.form_values["test_time"].set("90")
        self.panel.form_values["color_pool"].set("red green blue")
        saved = configs / "quick run.hjson"
        saved.unlink(missing_ok=True)
        self.addCleanup(saved.unlink, missing_ok=True)
        with patch("gui.sdk.tests.panel.filedialog.asksaveasfilename",
                   return_value=str(saved)) as dialog:
            self.panel.save_config_file()
        dialog.assert_called_once()
        self.assertIn("*.hjson", str(dialog.call_args))
        self.assertTrue(saved.exists())
        self.assertEqual(self.panel.config_selection.get(),
                         "sw/gui/tests/Stroop/configs/quick run.hjson")
        # The saved file restores the edited values through load + launch.
        self.panel.form_values["test_time"].set("120")
        with patch("gui.sdk.tests.panel.filedialog.askopenfilename",
                   return_value=str(saved)):
            self.panel.load_config_file()
        self.assertEqual(self.panel.form_values["test_time"].get(), "90")
        with patch("gui.sdk.tests.panel.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            self.panel.launch()
        (args,), _ = popen.call_args
        self.assertEqual(args[args.index("--test-time") + 1], "90")
        self.assertEqual(args[args.index("--color-pool") + 1], "red green blue")
        self.panel.close_task()
        # Hand-written files load even without the test's section (bare keys
        # or a single old-named section), from anywhere on disk.
        bare = configs / "bare.hjson"
        bare.write_text("{ test_time: 30, n_colors: 5 }\n")
        self.addCleanup(bare.unlink)
        with patch("gui.sdk.tests.panel.filedialog.askopenfilename",
                   return_value=str(bare)):
            self.panel.load_config_file()
        self.assertEqual(self.panel.form_values["test_time"].get(), "30")
        self.assertEqual(self.panel.form_values["n_colors"].get(), "5")
        self.assertIn("Loaded bare.hjson", self.panel.config_status.get())
        self.assertIn("3 values changed", self.panel.config_status.get())
        elsewhere = Path(self.directory.name) / "elsewhere.hjson"
        elsewhere.write_text("{ stroop-stress: { test_time: 25 } }\n")
        with patch("gui.sdk.tests.panel.filedialog.askopenfilename",
                   return_value=str(elsewhere)):
            self.panel.load_config_file()
        self.assertEqual(self.panel.form_values["test_time"].get(), "25")
        # A bad file reports the error instead of failing silently.
        broken = Path(self.directory.name) / "broken.hjson"
        broken.write_text("{ a: }\n")
        with patch("gui.sdk.tests.panel.filedialog.askopenfilename",
                   return_value=str(broken)):
            self.panel.load_config_file()
        self.assertIn("Could not load", self.panel.config_status.get())
        # Loading the same file again (values already current) still reports.
        with patch("gui.sdk.tests.panel.filedialog.askopenfilename",
                   return_value=str(bare)):
            self.panel.load_config_file()
            self.panel.load_config_file()
        self.assertIn("Loaded bare.hjson", self.panel.config_status.get())
        self.assertIn("values already current", self.panel.config_status.get())

    def test_voice_section_needs_voice_mode(self):
        # The calibration and microphone only exist in voice mode, so the
        # whole Voice section is greyed out until Response mode = voice.
        self.panel.form_values["response_mode"].set("keys")
        self.panel._update_section_states()
        rules = self.panel._section_rules
        self.assertEqual(len(rules), 1)
        (rule, widgets), = rules
        self.assertEqual(rule["key"], "response_mode")
        self.assertTrue(widgets)  # calibration checkbox among them
        self.assertTrue(any(w.instate(["disabled"]) for w in widgets))
        # Selecting voice mode enables the section (the dropdown event
        # triggers this in the real GUI).
        self.panel.form_values["response_mode"].set("voice")
        self.panel._update_section_states()
        self.assertTrue(all(not w.instate(["disabled"]) for w in widgets))

    def test_placeholder_tests_refuse_to_launch(self):
        self.panel.test_selection.set("SSST")  # Folder exists, test does not.
        self.panel._build_form()
        with patch("gui.sdk.tests.panel.subprocess.Popen") as popen:
            self.panel.launch()
        popen.assert_not_called()
        self.assertIn("No such test was found", self.panel.status.get())
        self.assertIn("No such test was found.",
                      [w.cget("text") for w in self.panel.form_frame.winfo_children()])

    def test_configuration_round_trip(self):
        values = {"event_path": "/tmp/feed.jsonl", "test": "Stroop",
                  "config_file": "defaults.hjson",
                  "launch_options": "--fullscreen false", "show_stimuli": True,
                  "show_start_stop": False, "show_verdicts": True,
                  "show_phases": False,
                  "values": {"test_time": "45", "seed": "3"}}
        self.panel.apply_configuration(values)
        snapshot = self.panel.configuration_snapshot()
        for key, value in values.items():
            if key == "values":
                for form_key, form_value in value.items():
                    self.assertEqual(snapshot["values"][form_key], form_value)
            else:
                self.assertEqual(snapshot[key], value)
        # Every rendered form control is part of the snapshot.
        self.assertEqual(set(snapshot["values"]), set(self.panel.form_values))

    def test_workspace_clock_is_set_only_before_events(self):
        origin = self.panel.clock_origin - 5
        self.panel.set_workspace_clock(origin)
        self.assertEqual(self.panel.clock_origin, origin)
        self.panel.events.append(self.event_for_panel())
        self.panel.set_workspace_clock(origin - 100)
        self.assertEqual(self.panel.clock_origin, origin)

    def event_for_panel(self):
        return Event.from_message(message("rest", 0, color="white"), self.panel.clock_origin)

    def test_load_recording_replaces_the_live_listener(self):
        self.panel.connect()
        event = self.event_for_panel()
        path = Path(self.directory.name) / "tests.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS)
            writer.writerow(event.row())
        self.panel.load_recording(path)
        self.assertFalse(self.panel.connected)
        self.assertIsNone(self.panel.clock_origin)
        self.assertEqual([e.label for e in self.panel.events], ["rest"])
        self.assertEqual(self.panel.plot_snapshot({"events"}).get_markers(0, 1e9),
                         [(event.time_s, "rest", "#ffffff")])

    def test_default_feed_matches_the_publisher(self):
        from gui.tests.common.events import DEFAULT_PATH as PUBLISHER_PATH
        self.assertEqual(DEFAULT_PATH, PUBLISHER_PATH)


class AlignedSnapshotTests(unittest.TestCase):
    def snapshot(self):
        def series(low=None, high=None):
            inside = [t for t in (10.0, 20.0)
                      if (low is None or t >= low) and (high is None or t <= high)]
            return {"a": {"times": inside,
                          "values": [1, 2][:len(inside)],
                          "labels": [label for t, label in zip((10.0, 20.0), ("x", "y"))
                                     if t in inside] if low is not None
                          else ["x", "y", "all"]}}
        return SimpleNamespace(
            get_series=series,
            invalid_spans=lambda low=None, high=None: [(10.0, 12.0)],
            get_markers=lambda low, high: [(15.0, "correct")],
            latest=20.0, revision=7)

    def test_every_view_is_shifted_by_the_device_offset(self):
        aligned = AlignedSnapshot(self.snapshot(), 5.0)
        self.assertEqual(aligned.get_series()["a"]["times"], [15.0, 25.0])
        self.assertEqual(aligned.get_series(18, 30)["a"]["times"], [25.0])
        self.assertEqual(aligned.get_series(18, 30)["a"]["labels"], ["y"])
        self.assertEqual(aligned.invalid_spans(0, 100), [(15.0, 17.0)])
        self.assertEqual(aligned.get_markers(0, 100), [(20.0, "correct")])
        self.assertEqual(aligned.latest, 25.0)
        self.assertEqual(aligned.revision, (7, 5.0))

    def test_zero_offset_is_transparent(self):
        aligned = AlignedSnapshot(self.snapshot(), 0.0)
        self.assertEqual(aligned.get_series()["a"]["times"], [10.0, 20.0])
        self.assertEqual(aligned.get_series()["a"]["labels"], ["x", "y", "all"])
        self.assertEqual(aligned.latest, 20.0)

    def test_optional_methods_default_to_empty(self):
        bare = SimpleNamespace(get_series=lambda low=None, high=None: {"a": {"times": [], "values": []}},
                               latest=0.0, revision=1)
        aligned = AlignedSnapshot(bare, 3.0)
        self.assertEqual(aligned.invalid_spans(0, 10), [])
        self.assertEqual(aligned.get_markers(0, 10), [])


class MarkerRenderingTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.viewport = TimeViewport(span=10.0, latest=10.0)
        self.plot = TimePlot(self.root, self.viewport, "Row 1", "", lambda: None,
                             width=800, height=300)
        self.plot.pack()
        self.root.update()

    def labels(self):
        return [self.plot.itemcget(item, "text") for item in self.plot.find_withtag("event-label")]

    def test_events_are_white_lines_with_labels_beside_them(self):
        self.plot.set_data([], markers=[(1.0, "test start"), (5.0, "correct")])
        lines = self.plot.find_withtag("event-line")
        self.assertEqual(len(lines), 2)
        for item in lines:
            self.assertEqual(self.plot.itemcget(item, "fill"), "#ffffff")
            coords = self.plot.coords(item)
            self.assertEqual(coords[1], 34)  # From the top to the bottom of the plot.
        self.assertEqual(self.labels(), ["test start", "correct"])
        for item in self.plot.find_withtag("event-label"):
            self.assertEqual(self.plot.itemcget(item, "fill"), "#ffffff")

    def test_markers_offscreen_are_not_drawn(self):
        self.plot.set_data([], markers=[(50.0, "rest")])
        self.assertFalse(self.plot.find_withtag("event-line"))
        self.assertEqual(self.labels(), [])

    def test_clustered_markers_bound_the_label_count(self):
        markers = [(1.0 + i * 0.01, "calibration end") for i in range(60)]
        self.plot.set_data([], markers=markers)
        self.assertLessEqual(len(self.plot.find_withtag("event-label")), 4)
        notices = [text for text in self.labels() + self._all_texts() if "Zoom in for" in text]
        self.assertTrue(notices)
        self.assertIn("56", notices[0])

    def _all_texts(self):
        texts = []
        for item in self.plot.find_withtag("all"):
            try:
                texts.append(self.plot.itemcget(item, "text"))
            except tk.TclError:
                pass
        return texts

    def test_event_markers_replace_the_no_signal_message(self):
        self.plot.set_data([], markers=[(1.0, "test start")])
        self.assertFalse(any("No signals" in text for text in self._all_texts()))
        self.plot.set_data([], markers=[])
        self.assertTrue(any("No signals" in text for text in self._all_texts()))

    def test_labels_flip_side_near_the_right_edge(self):
        self.plot.set_data([], markers=[(9.7, "test end")])
        self.assertEqual(self.labels(), ["test end"])
        item = self.plot.find_withtag("event-label")[0]
        self.assertEqual(self.plot.itemcget(item, "anchor"), "ne")

    def test_verdict_markers_are_colored_and_info_stays_white(self):
        self.plot.set_data([], markers=[(1.0, "correct", "#3fbf5f"),
                                        (3.0, "wrong", "#e04848"),
                                        (5.0, "rest", "#ffffff"),
                                        (7.0, "test start")])  # 2-tuple: white default.
        lines = self.plot.find_withtag("event-line")
        self.assertEqual([self.plot.itemcget(item, "fill") for item in lines],
                         ["#3fbf5f", "#e04848", "#ffffff", "#ffffff"])
        self.assertEqual([self.plot.itemcget(item, "fill")
                          for item in self.plot.find_withtag("event-label")],
                         ["#3fbf5f", "#e04848", "#ffffff", "#ffffff"])

    def test_marker_zorder_layers_event_lines_against_the_signals(self):
        trace = Trace(times=[0.5, 1.0, 2.0], values=[0.4, 0.5, 0.6],
                      color="#4fa3ff", zorder=5)

        def layering():
            """Positive when the event line paints above the signal trace."""
            items = self.plot.find_withtag("all")
            return (items.index(self.plot.find_withtag("event-line")[0])
                    - items.index(self.plot.find_withtag("trace-line")[0]))

        self.plot.set_data([trace], markers=[(1.0, "correct", "#3fbf5f", -1)])
        self.assertLess(layering(), 0, "lowered zorder must draw behind the signal")
        self.plot.set_data([trace], markers=[(1.0, "correct", "#3fbf5f", 7)])
        self.assertGreater(layering(), 0, "raised zorder must draw in front")


class WorkspaceMarkerFrameTests(unittest.TestCase):
    """Marker colors/zorder and row hiding inside the workspace render frame."""

    def renderer(self, rows, markers, biopac=False):
        panels = {"tests": SimpleNamespace(
            get_series=lambda *a: {},
            get_markers=lambda low, high: [m for m in markers if low <= m[0] <= high],
            invalid_spans=lambda *a: [])}
        if biopac:
            panels["biopac"] = SimpleNamespace(
                get_series=lambda *a: {"conductance_uS": {"times": [1.0, 2.0],
                                                          "values": [5.0, 6.0]}},
                invalid_spans=lambda *a: [])
        renderer = app.Workspace.__new__(app.Workspace)
        renderer.viewport = TimeViewport(span=10.0)
        renderer.signal_rows = rows
        renderer.panels = panels
        renderer.feature_cache, renderer.error_seen = {}, set()
        return renderer

    @staticmethod
    def markers_of(built):
        return [marker for trace in built for marker in trace.get("event_markers", [])]

    def test_markers_keep_event_colors_and_the_lowest_zorder(self):
        rows = [app.SignalRow(device="tests", signal="events", zorder=-2),
                app.SignalRow(device="tests", signal="events", zorder=3)]
        built, _ = self.renderer(rows, [(1.0, "correct", "#3fbf5f"), (2.0, "wrong", "#e04848"),
                                        (3.0, "timeout", "#e04848"), (4.0, "rest", "#ffffff")])._compute_frame()
        self.assertEqual(self.markers_of(built),
                         [(1.0, "correct", "#3fbf5f", -2), (2.0, "wrong", "#e04848", -2),
                          (3.0, "timeout", "#e04848", -2), (4.0, "rest", "#ffffff", -2)])

    def test_palette_names_and_legacy_2tuples_fall_back_to_white(self):
        rows = [app.SignalRow(device="tests", signal="events", zorder=0)]
        built, _ = self.renderer(rows, [(1.0, "rest", "green"),
                                        (2.0, "legacy")])._compute_frame()
        self.assertEqual(self.markers_of(built),
                         [(1.0, "rest", "#3fbf5f", 0), (2.0, "legacy", "#ffffff", 0)])

    def test_hiding_every_test_row_suppresses_the_markers(self):
        rows = [app.SignalRow(device="tests", signal="events", hidden=True)]
        built, _ = self.renderer(rows, [(1.0, "correct")])._compute_frame()
        self.assertEqual(self.markers_of(built), [])

    def test_hidden_signal_rows_drop_traces_but_keep_the_markers(self):
        rows = [app.SignalRow(device="biopac", signal="conductance_uS", hidden=True),
                app.SignalRow(device="tests", signal="events")]
        built, _ = self.renderer(rows, [(1.0, "correct")], biopac=True)._compute_frame()
        self.assertEqual([trace for trace in built if "event_markers" not in trace], [])
        self.assertEqual(len(self.markers_of(built)), 1)
        rows[0].hidden = False
        built, _ = self.renderer(rows, [(1.0, "correct")], biopac=True)._compute_frame()
        self.assertEqual([trace for trace in built if "event_markers" not in trace],
                         [built[0]])


class StroopSessionPanel:
    """A fake Stroop panel for workspace save/load (no Tk widgets needed)."""

    recording_state, operation = "stopped", None

    def __init__(self, device):
        self.device, self.configuration, self.loaded, self.cleared = device, {}, None, 0
        self.clock = None

    def set_workspace_clock(self, origin):
        self.clock = origin

    def configuration_snapshot(self):
        return self.configuration

    def apply_configuration(self, values):
        self.configuration = values

    def signals(self):
        return {"events": {"label": "Test events", "unit": "", "primary": True, "kind": "events"}}

    def plot_snapshot(self, requested=()):
        events = [(1.0, "test start", "#ffffff"), (2.0, "correct", "#3fbf5f"),
                  (3.0, "wrong", "#e04848")]
        return SimpleNamespace(
            signals=self.signals,
            get_series=lambda *a: {"events": {"times": [t for t, *_ in events],
                                              "values": [float("nan"), 1.0, 0.0],
                                              "labels": [label for _, label, _ in events],
                                              "kind": "events"}},
            get_markers=lambda low, high: [(t, label, hex_color) for t, label, hex_color in events
                                           if low <= t <= high],
            invalid_spans=lambda *_: [],
            revision=(len(events),), clock_origin=7.0, latest=3.0)

    def recording_snapshot(self):
        rows = [(1.0, "test start", "white", 8.0, 100.0, "run-1", 0, "stroop", "{}"),
                (2.0, "correct", "green", 9.0, 101.0, "run-1", 1, "stroop", '{"trial": 1}'),
                (3.0, "wrong", "red", 10.0, 102.0, "run-1", 2, "stroop", "{}")]
        return {"columns": list(COLUMNS), "rows": lambda: iter(rows), "count": 3, "sidecars": {}}

    def clear_recording(self):
        self.cleared += 1

    def load_recording(self, path):
        self.loaded = path
        return path

    def close(self):
        pass


class WorkspaceRoundTripTests(unittest.TestCase):
    """A Stroop recording survives the combined workspace save/load."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(app, "WORKSPACE_CONFIG", self.directory / "ws.json"):
            self.workspace = app.Workspace(self.root)

    def enable_all(self):
        panels = {"tests": StroopSessionPanel("tests")}
        devices = tuple({**device, "panel": (lambda *a, k=device["key"], **kw: panels[k])}
                        if device["key"] in panels else device for device in app.DEVICES)
        patcher = patch.object(app, "DEVICES", devices)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace.device_frames["tests"]["variable"].set(True)
        return panels

    def test_stroop_recording_and_offsets_survive_save_and_load(self):
        panels = self.enable_all()
        ws = self.workspace
        ws.add_signal_row("tests", "events", color="#abcdef")
        path = self.directory / "with-tests.csv"
        with patch.object(app.filedialog, "asksaveasfilename", return_value=str(path)):
            ws.save_all()
        ws.file_future.result(timeout=20)
        ws._finish_file_operation()
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertIn("workspace_label", rows[0])
        samples = [row for row in rows if row["workspace_kind"] == "sample"
                   and row["workspace_device"] == "tests"]
        self.assertEqual([row["event_label"] for row in samples],
                         ["test start", "correct", "wrong"])
        self.assertEqual([row["workspace_label"] for row in samples], ["", "", ""])
        self.assertTrue(metadata_path(path).exists())
        # Reload into clean panels; the Stroop CSV must validate and load.
        ws.timeline_offsets.clear()
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)):
            ws.load_csv()
        ws.file_future.result(timeout=20)
        ws._finish_file_operation()
        self.assertTrue(str(panels["tests"].loaded).endswith("tests.csv"))
        self.assertEqual(ws.signal_rows[-1].device, "tests")

    def test_hidden_rows_and_their_button_state_survive_the_round_trip(self):
        ws = self.workspace
        ws.add_signal_row("tests", "events", color="#abcdef")
        widgets = ws.table_rows[-1]
        self.assertEqual(widgets["hide"].cget("text"), "Hide")
        ws._toggle_row_hidden(widgets)
        self.assertTrue(widgets["row"].hidden)
        self.assertEqual(widgets["hide"].cget("text"), "Show")
        self.assertEqual(widgets["color"].cget("state"), "disabled")
        ws._toggle_row_hidden(widgets)  # Toggling back restores the row.
        self.assertFalse(widgets["row"].hidden)
        self.assertEqual(widgets["hide"].cget("text"), "Hide")
        self.assertEqual(widgets["color"].cget("state"), "normal")
        ws._toggle_row_hidden(widgets)
        state = ws._workspace_state()
        self.assertTrue(state["rows"][-1]["hidden"])
        # A fresh workspace rebuilds the table with the hidden state shown.
        with patch.object(app, "WORKSPACE_CONFIG", self.directory / "ws-hidden.json"):
            fresh = app.Workspace(self.root)
        self.addCleanup(fresh.close)
        fresh._apply_workspace_state(state)
        self.assertTrue(fresh.signal_rows[-1].hidden)
        self.assertEqual(fresh.table_rows[-1]["hide"].cget("text"), "Show")
        self.assertEqual(fresh.table_rows[-1]["color"].cget("state"), "disabled")


class EndToEndTests(unittest.TestCase):
    """A real Stroop run publishes phase/outcome events the panel collects."""

    def setUp(self):
        from gui.tests.Stroop.test import Config as StroopConfig, Test as StroopTest
        self.StroopTest, self.StroopConfig = StroopTest, StroopConfig
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.feed = Path(self.directory.name) / "events.jsonl"
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.panel = TestsPanel(self.root, host=tk.Frame(self.root),
                                 reports=tk.Frame(self.root))
        self.addCleanup(self.panel.close)
        self.panel.event_path.set(str(self.feed))
        self.panel.connect()

    def pump_until(self, condition, timeout=20.0):
        limit = time.monotonic() + timeout
        while time.monotonic() < limit:
            self.root.update()
            time.sleep(0.01)
            if condition():
                return True
        return False

    def test_full_run_reaches_the_panel_as_ordered_colored_markers(self):
        test = self.StroopTest(self.StroopConfig(rest_time=1.0, test_time=2.5,
                                                 stimulus_time=0.8, isi=0.1,
                                                 fullscreen=False, n_colors=2, seed=3))
        test.run(parent=self.root, event_path=str(self.feed))
        answered, pressed = [], set()

        def drive():
            """Press SPACE through the phases, answer, then leave the summary."""
            state = test.state
            if state == "summary":
                test._on_escape(None)  # Finished: reports the full result, not aborted.
            elif state in ("instructions", "rest") and state not in pressed:
                pressed.add(state)
                test._handle_key("space")
            elif state == "stimulus" and test._trial is not None and \
                    (len(answered) == 0 or answered[-1] != len(test.events)):
                answered.append(len(test.events))
                ink = test._trial["ink"]
                # Alternate correct and wrong number-key answers.
                if len(answered) % 2:
                    key = next(k for k, name in test.keys.items() if name == ink)
                else:
                    key = next(k for k, name in test.keys.items() if name != ink)
                test._handle_key(key)

        self.assertTrue(self.pump_until(lambda: test.result is not None or drive()),
                        "The Stroop run did not finish")
        result = test.result
        self.assertFalse(result.aborted)
        labels = [marker["label"] for marker in result.timeline]
        for phase in ("instructions", "rest start", "rest end", "Stroop start", "Stroop end"):
            self.assertIn(phase, labels)
        self.assertEqual(labels[0], "instructions")
        self.assertEqual(labels[-1], "Stroop end")
        self.assertTrue(result.summary["accuracy"] is not None or result.events)

        # Every published marker arrives at the listening panel in order.
        self.assertTrue(self.pump_until(lambda: len(self.panel.events) == len(result.timeline)))
        self.assertEqual([e.label for e in self.panel.events], labels)
        self.assertEqual([e.run_id for e in self.panel.events],
                         [marker["run_id"] for marker in result.timeline])
        times = [e.time_s for e in self.panel.events]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(e.unix_s >= self.panel.since_unix for e in self.panel.events))

        # The panel's snapshot renders them as colored labeled lines: verdicts
        # green/red, every phase white.
        snapshot = self.panel.plot_snapshot({"events"})
        self.plot = TimePlot(self.root, TimeViewport(span=60.0, latest=snapshot.latest),
                             "Row 1", "", lambda: None, width=800, height=300)
        self.plot.pack()
        self.root.update()
        self.plot.set_data([], markers=snapshot.get_markers(0, 1e9))
        rendered = [self.plot.itemcget(item, "text")
                    for item in self.plot.find_withtag("event-label")]
        self.assertIn("Stroop start", rendered)
        fills = {self.plot.itemcget(item, "fill")
                 for item in self.plot.find_withtag("event-line")}
        self.assertEqual(fills, {"#ffffff", "#3fbf5f", "#e04848", "#f2bd62"})

        # A saved native CSV reloads into a fresh panel with the same timeline.
        path = Path(self.directory.name) / "tests.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS)
            for event in self.panel.events:
                writer.writerow(event.row())
        fresh = TestsPanel(self.root, host=tk.Frame(self.root), reports=tk.Frame(self.root))
        self.addCleanup(fresh.close)
        fresh.load_recording(path)
        self.assertEqual([e.label for e in fresh.events], labels)
        self.assertEqual([e.time_s for e in fresh.events], times)
        # Offline alignment shifts the loaded timeline by the workspace offset.
        offset = 12.5
        base = [t for t, *_ in fresh.plot_snapshot({"events"}).get_markers(0, 1e9)]
        aligned = AlignedSnapshot(fresh.plot_snapshot({"events"}), offset)
        self.assertEqual([t + offset for t in base],
                         [t for t, *_ in aligned.get_markers(0, 1e9)])
        self.assertEqual(aligned.latest, fresh.plot_snapshot({"events"}).latest + offset)


if __name__ == "__main__":
    unittest.main()
