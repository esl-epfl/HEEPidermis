"""Workspace-level behaviours: loading recordings and signal-table ranges."""

import csv
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui import app
from gui.common.session import write_session
from gui.sdk.heepidermis.recording import read_recording
from gui.sdk.biopac.recording import load_recording

try:
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
except tk.TclError as error:  # headless CI or a missing python3-tk
    raise unittest.SkipTest(f"Tk display unavailable: {error}") from error


class FakePanel:
    """Records load calls; mimics the per-device guard attributes."""

    def __init__(self, busy=False):
        self.paths = []
        self.state = "connected" if busy else "disconnected"
        self.pending = False
        self.recording_state = "running" if busy else "stopped"
        self.operation = None

    def load_csv(self, path):
        self.paths.append(path)

    def load_recording(self, path):
        self.paths.append(path)
        return path


class SessionPanel(FakePanel):
    def __init__(self, device):
        super().__init__()
        self.device = device
        self.configuration = {}

    def configuration_snapshot(self):
        return self.configuration

    def apply_configuration(self, values):
        self.configuration = values

    def close(self):
        pass

    def signals(self):
        return {"conductance_uS": {"unit": "µS", "primary": True}}

    def plot_snapshot(self, requested=()):
        return SimpleNamespace(signals=self.signals, get_series=lambda: {
            "conductance_uS": {"times": [0, 1, 2], "values": [1, 2, 3], "segments": [0, 0, 1]}})

    def recording_snapshot(self):
        if self.device == "heepidermis":
            columns = ("time_s", "vco_p_uV", "vco_n_uV", "injected_current_uA", "buffer_loss")
            rows = [(0, 300000, 200000, 0.12, 0), (1, 310000, 200000, 0.12, 1)]
            sidecars = {}
        else:
            columns = ("sample_index", "time_s", "adc_count", "sensitivity_us_per_v", "zero_voltage_v")
            rows = [(0, 0.25, -123, 5, 0.1), (1, 0.5, 32767, 5, 0.1)]
            sidecars = {".json": {"frozen_buffer_verified": True}}
        return {"columns": columns, "rows": lambda: iter(rows), "count": 2, "sidecars": sidecars}

    def load_csv(self, path):
        self.paths.append(path)
        return path


class LoadCsvTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(app, "WORKSPACE_CONFIG", self.directory / "ws.json"):
            self.workspace = app.Workspace(self.root)

    def write(self, name, header):
        path = self.directory / name
        path.write_text(header + "\n1,2,3\n")
        return path

    def test_header_detection(self):
        heepidermis = self.write("he.csv", "time_s,vco_p_uV,vco_n_uV,delta_v_uV,conductance_uS")
        biopac = self.write("biopac.csv", "sample_index,time_s,segment,segment_sample_index,channel,"
                                         "sample_rate_hz,adc_count")
        other = self.write("other.csv", "a,b,c")
        self.assertEqual(self.workspace._recording_device(heepidermis), "heepidermis")
        self.assertEqual(self.workspace._recording_device(biopac), "biopac")
        self.assertIsNone(self.workspace._recording_device(other))
        self.assertIsNone(self.workspace._recording_device(self.directory / "missing.csv"))

    def test_load_routes_to_the_right_panel_and_enables_its_device(self):
        heepidermis = self.write("he.csv", "time_s,vco_p_uV,vco_n_uV")
        biopac = self.write("biopac.csv", "sample_index,time_s,adc_count")
        he_panel, biopac_panel = FakePanel(), FakePanel()
        self.workspace.panels["heepidermis"] = he_panel
        self.workspace.panels["biopac"] = biopac_panel
        with patch.object(app.filedialog, "askopenfilename", side_effect=[str(biopac), str(heepidermis)]):
            self.workspace.load_csv()
            self.workspace.load_csv()
        self.assertEqual(biopac_panel.paths, [str(biopac)])
        self.assertEqual(he_panel.paths, [str(heepidermis)])
        # The BIOPAC row was never enabled; loading must not touch the devices.
        self.assertFalse(self.workspace.device_frames["biopac"]["variable"].get())

    def test_load_enables_a_disabled_device_before_loading(self):
        path = self.write("he.csv", "time_s,vco_p_uV,vco_n_uV")
        # No real hardware here: replace the panel factory with a fake.
        device = next(d for d in app.DEVICES if d["key"] == "heepidermis")
        fake = FakePanel()
        with patch.object(app, "DEVICES", [{**device, "panel": lambda *a, **k: fake}]):
            with patch.object(app.filedialog, "askopenfilename", return_value=str(path)):
                self.workspace.load_csv()
        self.assertTrue(self.workspace.device_frames["heepidermis"]["variable"].get())
        self.assertEqual(fake.paths, [str(path)])

    def test_busy_device_refuses_the_load_without_touching_it(self):
        path = self.write("biopac.csv", "sample_index,time_s,adc_count")
        panel = FakePanel(busy=True)
        self.workspace.panels["biopac"] = panel
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)), \
             patch.object(app.messagebox, "showinfo") as info:
            self.workspace.load_csv()
        self.assertEqual(panel.paths, [])
        info.assert_called_once()

    def test_rows_for_disabled_devices_survive_enabling_another_device(self):
        # A row restored for BIOPAC while only HEEPidermis gets enabled must
        # keep its device and signal (regression: rows were silently
        # reassigned to the newly enabled device).
        self.workspace.signal_rows.append(app.SignalRow(device="biopac", signal="conductance_uS",
                                                        color="#ff0000"))
        self.workspace._add_table_row(self.workspace.signal_rows[-1])
        device = next(d for d in app.DEVICES if d["key"] == "heepidermis")
        devices = tuple({**device, "panel": lambda *a, **k: object()} if d["key"] == "heepidermis"
                        else d for d in app.DEVICES)
        with patch.object(app, "DEVICES", devices):
            self.workspace.device_frames["heepidermis"]["variable"].set(True)
        row = self.workspace.signal_rows[-1]
        self.assertEqual((row.device, row.signal), ("biopac", "conductance_uS"))
        self.assertEqual(self.workspace.table_rows[-1]["device"].get(),
                         app.source_name("biopac"))

    def test_load_adds_a_row_for_the_loaded_device_when_none_exists(self):
        path = self.write("biopac.csv", "sample_index,time_s,adc_count")
        panel = FakePanel()
        panel.signals = lambda: {"voltage_V": {"label": "Amplifier voltage", "unit": "V"},
                                 "conductance_uS": {"label": "Skin conductance", "unit": "µS",
                                                    "primary": True}}
        self.workspace.panels["biopac"] = panel
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)):
            self.workspace.load_csv()
        # The auto-added row shows the primary magnitude, not the first
        # catalogue entry, and takes the free axis of the top stack.
        row = self.workspace.signal_rows[-1]
        self.assertEqual((row.device, row.signal, row.axis), ("biopac", "conductance_uS", "Left"))
        # A second device auto-added afterwards takes the other axis.
        other = FakePanel()
        other.signals = lambda: {"conductance_uS": {"label": "Tissue conductance", "unit": "µS",
                                                    "primary": True}}
        self.workspace.panels["heepidermis"] = other
        he_path = self.write("he.csv", "time_s,vco_p_uV,vco_n_uV")
        with patch.object(app.filedialog, "askopenfilename", return_value=str(he_path)):
            self.workspace.load_csv()
        he_row = self.workspace.signal_rows[-1]
        self.assertEqual((he_row.device, he_row.signal, he_row.axis),
                         ("heepidermis", "conductance_uS", "Right"))
        # Loading again must not duplicate rows.
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)):
            self.workspace.load_csv()
        self.assertEqual(len([r for r in self.workspace.signal_rows if r.device == "biopac"]), 1)

    def test_new_rows_default_to_the_primary_signal(self):
        panel = FakePanel()
        panel.signals = lambda: {"voltage_V": {"label": "Amplifier voltage", "unit": "V"},
                                 "conductance_uS": {"label": "Skin conductance", "unit": "µS",
                                                    "primary": True}}
        self.workspace.panels["biopac"] = panel
        self.assertEqual(self.workspace._default_signal(app.SignalRow(device="biopac")),
                         "conductance_uS")
        # Without a primary marker, the first catalogue entry is kept.
        panel.signals = lambda: {"voltage_V": {"label": "Amplifier voltage", "unit": "V"}}
        self.assertEqual(self.workspace._default_signal(app.SignalRow(device="biopac")),
                         "voltage_V")

    def test_feature_and_comparison_sources_survive_disabled_devices(self):
        # Restored feature/comparison rows keep their saved sources while the
        # referenced device's panel is not enabled yet (same keep-until-
        # validatable rule as device/signal rows). The built-in annotations
        # row sits first in the table, hence the negative indices.
        features = app.SignalRow(device="features", signal="SCR frequency",
                                 source_a="biopac:conductance_uS", color="#ff0000")
        comparison = app.SignalRow(device="comparison", signal=app.COMPARISON_CHOICES[0],
                                   source_a="biopac:conductance_uS",
                                   source_b="heepidermis:conductance_uS", color="#00ff00")
        self.workspace._add_table_row(features)
        self.workspace._add_table_row(comparison)
        self.assertEqual(self.workspace.table_rows[-2]["source_a"].get(), "biopac:conductance_uS")
        self.assertEqual(self.workspace.table_rows[-1]["source_a"].get(), "biopac:conductance_uS")
        self.assertEqual(self.workspace.table_rows[-1]["source_b"].get(), "heepidermis:conductance_uS")

    def test_sources_keep_until_the_referenced_panel_validates_them(self):
        # Enabling an unrelated panel must not touch biopac sources; enabling
        # the referenced panel validates them (stale names are corrected).
        row = app.SignalRow(device="comparison", signal=app.COMPARISON_CHOICES[0],
                            source_a="biopac:conductance_uS",
                            source_b="biopac:stale_signal", color="#ff0000")
        self.workspace._add_table_row(row)
        devices = tuple({**d, "panel": lambda *a, **k: object()} if d["key"] == "heepidermis"
                        else d for d in app.DEVICES)
        with patch.object(app, "DEVICES", devices):
            self.workspace.device_frames["heepidermis"]["variable"].set(True)
        self.assertEqual(row.source_a, "biopac:conductance_uS")

        panel = FakePanel()
        panel.signals = lambda: {"conductance_uS": {"unit": "µS"}}
        self.workspace.panels["biopac"] = panel
        self.workspace._rebuild_signal_choices()
        # The valid source survives; the stale one falls back to a real key.
        self.assertEqual(row.source_a, "biopac:conductance_uS")
        self.assertEqual(row.source_b, "biopac:conductance_uS")

    def test_unrecognized_file_is_reported(self):
        path = self.write("other.csv", "a,b,c")
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)), \
             patch.object(app.messagebox, "showerror") as error:
            self.workspace.load_csv()
        error.assert_called_once()

    def session_panels(self):
        panels = {key: SessionPanel(key) for key in ("heepidermis", "biopac")}
        devices = tuple({**device, "panel": (lambda *a, k=device["key"], **kw: panels[k])}
                        if device["key"] in panels else device for device in app.DEVICES)
        patcher = patch.object(app, "DEVICES", devices)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in panels:
            self.workspace.device_frames[key]["variable"].set(True)
        return panels

    def test_one_save_and_load_restores_both_devices_and_all_signal_rows(self):
        panels = self.session_panels()
        ws = self.workspace
        ws.add_signal_row("heepidermis", "conductance_uS", color="#123456", y_scale="Fixed", y_max=20)
        ws.add_signal_row("biopac", "conductance_uS", axis="Right", color="#654321")
        ws.add_signal_row("comparison", "Difference (A − B)", source_a="heepidermis:conductance_uS",
                          source_b="biopac:conductance_uS", color="#112233")
        ws.add_signal_row("features", "Peak rising time", source_a="heepidermis:conductance_uS",
                          smoothing=4, prominence=0.05, color="#332211")
        expected = ws._workspace_state()
        path = self.directory / "all.csv"
        with patch.object(app.filedialog, "asksaveasfilename", return_value=str(path)) as dialog:
            ws.save_all()
        dialog.assert_called_once()
        ws.file_future.result(timeout=20)
        ws._finish_file_operation()
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({r["workspace_device"] for r in rows if r["workspace_kind"] == "sample"},
                         {"heepidermis", "biopac"})
        # Full signal histories, even though the viewport covers only a
        # fraction. Exported signal rows carry workspace-aligned timestamps
        # (offset 0 for a same-origin BIOPAC session, but still floats).
        self.assertEqual([r["time_s"] for r in rows if r["workspace_device"] == "biopac"
                          and r["workspace_kind"] == "signal" and not r["workspace_row"]],
                         ["0.0", "1.0", "2.0"])
        for widgets in list(ws.table_rows):
            ws._remove_row(widgets)
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)) as dialog:
            ws.load_csv()
        dialog.assert_called_once()
        ws.file_future.result(timeout=20)
        ws._finish_file_operation()
        self.assertEqual(ws._workspace_state(), expected)
        he = read_recording(panels["heepidermis"].paths[-1])
        bi, metadata = load_recording(panels["biopac"].paths[-1])
        self.assertEqual([s.p_uv for s in he], [300000, 310000])
        self.assertTrue(he[-1].buffer_loss)
        self.assertEqual([s.adc_count for s in bi.samples], [-123, 32767])
        self.assertTrue(bi.samples[-1].clipped)
        self.assertTrue(bi.samples[0].segment.verified)
        self.assertEqual(bi.samples[0].segment.settings.sensitivity, 5)
        for directory in ws.recording_directories:
            directory.cleanup()

    def test_configuration_keeps_settings_for_disabled_devices(self):
        panels = self.session_panels()
        ws = self.workspace
        panels["biopac"].configuration = {"interface": "saved-adapter"}
        ws.device_frames["biopac"]["variable"].set(False)
        path = self.directory / "settings.json"
        with patch.object(app.filedialog, "asksaveasfilename", return_value=str(path)):
            ws.save_configuration()
        ws.saved_device_configurations.clear()
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)):
            ws.load_configuration()
        self.assertNotIn("biopac", ws.panels)
        ws.device_frames["biopac"]["variable"].set(True)
        self.assertEqual(panels["biopac"].configuration["interface"], "saved-adapter")

    def test_workspace_load_checks_all_devices_before_changing_any(self):
        panels = self.session_panels()
        path = self.directory / "all.csv"
        write_session(path, self.workspace._configuration_bundle(),
                      {key: panel.recording_snapshot() for key, panel in panels.items()}, [])
        panels["biopac"].state = "recording"
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)), \
             patch.object(app.messagebox, "showinfo") as info:
            self.workspace.load_csv()
        info.assert_called_once()
        self.assertIsNone(self.workspace.file_future)
        self.assertFalse(any(panel.paths for panel in panels.values()))

    def test_damaged_workspace_rejected_before_loading_any_device(self):
        panels = self.session_panels()
        path = self.directory / "all.csv"
        write_session(path, self.workspace._configuration_bundle(),
                      {key: panel.recording_snapshot() for key, panel in panels.items()}, [])
        # Lose the last native sample without changing the metadata count.
        path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)):
            self.workspace.load_csv()
        with self.assertRaisesRegex(ValueError, "Incomplete recording"):
            self.workspace.file_future.result(timeout=20)
        with patch.object(app.messagebox, "showerror") as error:
            self.workspace._finish_file_operation()
        error.assert_called_once()
        self.assertFalse(any(panel.paths for panel in panels.values()))

    def test_annotation_menu_add_edit_delete_cycle(self):
        ws = self.workspace
        ws.viewport.cursor = 7.0
        app.set_widget_value(ws.annotation_label_entry, "event")
        ws._annotation_add()
        self.assertEqual([(a.time, a.label) for a in ws.annotations], [(7.0, "event")])
        self.assertTrue(ws.annotation_edit_button.instate(["!disabled"]))
        # Editing applies changes live; Escape restores the previous values.
        ws._annotation_edit()
        self.assertEqual(len(ws._edit_bind_ids), 2)  # Root Enter/Escape bindings.
        app.set_widget_value(ws.annotation_description_text, "changed")
        ws.annotation_description_text.edit_modified(True)
        ws._annotation_description_changed()
        self.assertEqual(ws.annotations[0].description, "changed")
        ws._annotation_edit_finish(cancel=True)
        self.assertEqual(ws.annotations[0].description, "")
        self.assertEqual(ws._edit_bind_ids, [])
        # Enter accepts the edits instead.
        ws._annotation_edit()
        app.set_widget_value(ws.annotation_label_entry, "renamed")
        ws._annotation_edit_finish(cancel=False)
        self.assertEqual(ws.annotations[0].label, "renamed")
        # Deleting keeps the fields so + Add re-creates the same marker.
        ws._annotation_delete()
        self.assertEqual(ws.annotations, [])
        self.assertEqual(app.widget_value(ws.annotation_label_entry), "renamed")
        ws.annotation_time_var.set("7.0")
        ws._annotation_add()
        self.assertEqual([(a.time, a.label) for a in ws.annotations], [(7.0, "renamed")])

    def test_enter_in_annotation_fields_adds_the_marker(self):
        ws = self.workspace
        ws.viewport.cursor = 4.0
        app.set_widget_value(ws.annotation_label_entry, "tap")
        self.assertEqual(ws._annotation_field_return(), "break")
        self.assertEqual([(a.time, a.label) for a in ws.annotations], [(4.0, "tap")])
        # While editing, Enter must accept the edit instead of adding another
        # marker: still one annotation, with the rename applied.
        ws._annotation_edit()
        self.assertIsNone(ws._annotation_field_return())
        app.set_widget_value(ws.annotation_label_entry, "renamed")
        ws._annotation_field_return()
        ws._annotation_edit_accept()
        self.assertEqual([(a.time, a.label) for a in ws.annotations], [(4.0, "renamed")])

    def test_annotation_click_selects_within_one_percent_of_the_window(self):
        ws = self.workspace
        from gui.common.plots import TimePlot
        plot = TimePlot(ws.root, ws.viewport, "Row 1", "", lambda: None)
        annotation = app.Annotation(time=50.0, label="mid")
        ws.annotations.append(annotation)
        plot.annotation_lines = [(50.0, annotation, False)]
        ws.viewport.span = 100.0  # Tolerance: ±1 s around the marker.
        ws._on_plot_click(plot, 50.7)
        self.assertIs(ws.selected_annotation, annotation)
        ws._on_plot_click(plot, 51.5)
        self.assertIsNone(ws.selected_annotation)

    def test_annotations_round_trip_through_workspace_csv(self):
        panels = self.session_panels()
        ws = self.workspace
        ws.add_signal_row("heepidermis", "conductance_uS", color="#123456")
        ws.viewport.cursor = 12.5
        app.set_widget_value(ws.annotation_label_entry, "tap")
        app.set_widget_value(ws.annotation_description_text, "stimulus onset")
        ws.annotation_signal_var.set("heepidermis:conductance_uS")
        ws._annotation_add()
        path = self.directory / "annotated.csv"
        with patch.object(app.filedialog, "asksaveasfilename", return_value=str(path)):
            ws.save_all()
        ws.file_future.result(timeout=20)
        ws._finish_file_operation()
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        saved = [row for row in rows if row["workspace_kind"] == "annotation"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["workspace_label"], "tap")
        self.assertEqual(saved[0]["annotation_description"], "stimulus onset")
        self.assertEqual((saved[0]["workspace_device"], saved[0]["workspace_signal"]),
                         ("heepidermis", "conductance_uS"))
        # Load the file back into a cleared workspace.
        ws.annotations = []
        ws.selected_annotation = None
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)):
            ws.load_csv()
        ws.file_future.result(timeout=20)
        ws._finish_file_operation()
        self.assertEqual([(a.time, a.label, a.description, a.signal) for a in ws.annotations],
                         [(12.5, "tap", "stimulus onset", "heepidermis:conductance_uS")])
        for directory in ws.recording_directories:
            directory.cleanup()

    def test_unknown_rows_are_skipped_and_reported_on_load(self):
        panels = self.session_panels()
        ws = self.workspace
        path = self.directory / "all.csv"
        write_session(path, ws._configuration_bundle(),
                      {key: panel.recording_snapshot() for key, panel in panels.items()}, [],
                      [{"time": 4.0, "label": "keep", "description": "", "color": "#00ff00", "signal": ""}])
        # Junk from another GUI version: unknown record kind and an annotation
        # without a usable timestamp. Loading must keep everything readable.
        path.write_text(path.read_text() + "bogus_kind,x,y\nannotation,,,,,\n")
        with patch.object(app.filedialog, "askopenfilename", return_value=str(path)):
            ws.load_csv()
        ws.file_future.result(timeout=20)
        ws._finish_file_operation()
        self.assertEqual([(a.time, a.label, a.color) for a in ws.annotations], [(4.0, "keep", "#00ff00")])
        self.assertIn("skipped 0 unreadable annotation(s) and 2 unknown record(s)",
                      ws.messages_text.get("1.0", "end"))
        for directory in ws.recording_directories:
            directory.cleanup()


class RenderFrameTests(unittest.TestCase):
    """Frame rendering must survive pseudo-traces and malformed entries.

    Regression: the ``event_markers`` pseudo-trace appended by
    ``_compute_frame`` reached the per-trace loop of ``_render_frame`` and
    raised ``KeyError: 'row_key'``, so every display refresh failed as soon
    as a test had emitted events — including when merely loading a recorded
    session back. The whole visualization was lost, not just the markers.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(app, "WORKSPACE_CONFIG",
                          Path(self.temporary.name) / "ws.json"):
            self.workspace = app.Workspace(self.root)

    def test_feature_choices_come_from_the_features_module(self):
        from gui.common import features as feature_module
        choices = self.workspace._signal_choices("features")
        self.assertEqual(choices, list(feature_module.FEATURE_LABELS))
        # The tonic/phasic decomposition is offered here, not as a device signal.
        self.assertIn("Tonic conductance", choices)
        self.assertIn("Phasic conductance", choices)

    def test_tonic_and_phasic_feature_rows_trace_the_decomposition(self):
        series = {"biopac:conductance_uS": {"times": [i / 10 for i in range(300)],
                                            "values": [2.0 + (i % 20) / 100 for i in range(300)],
                                            "segments": [0] * 300}}
        tonic = app.SignalRow(device="features", signal="Tonic conductance",
                              source_a="biopac:conductance_uS")
        trace = self.workspace._build_trace(tonic, series)
        self.assertEqual(trace["name"], "Tonic conductance · biopac:conductance_uS")
        self.assertEqual(len(trace["times"]), len(series["biopac:conductance_uS"]["times"]))
        self.assertFalse(trace["markers"])  # A full series, not peak markers.
        phasic = app.SignalRow(device="features", signal="Phasic conductance",
                               source_a="biopac:conductance_uS")
        self.assertEqual(len(self.workspace._build_trace(phasic, series)["values"]), 300)

    def test_removing_a_row_updates_the_plots_immediately(self):
        self.workspace._render_frame([
            {"row_key": (1, False), "times": [0.0, 1.0], "values": [1.0, 2.0],
             "name": "biopac:conductance_uS"},
            {"row_key": (1, False), "times": [0.0, 1.0], "values": [3.0, 4.0],
             "name": "heepidermis:conductance_uS"},
        ], [])
        self.assertEqual(len(self.workspace.row_plots[1].traces), 2)
        he_row = app.SignalRow(device="heepidermis", signal="conductance_uS", color="#00ff00")
        self.workspace.signal_rows.append(he_row)
        self.workspace._add_table_row(he_row)
        row = app.SignalRow(device="biopac", signal="conductance_uS", color="#ff0000")
        self.workspace.signal_rows.append(row)
        self.workspace._add_table_row(row)
        # The background frame is still pending; the removal must not wait for it.
        self.workspace._remove_row(self.workspace.table_rows[-1])
        self.assertEqual([t.name for t in self.workspace.row_plots[1].traces],
                         ["heepidermis:conductance_uS"])

    def test_event_markers_frame_renders(self):
        self.workspace._render_frame(
            [{"event_markers": [(1.0, "correct", "#3fbf5f", 0)]}], [])
        marker = self.workspace.row_plots[1].markers[0]
        self.assertEqual((1.0, "correct", "#3fbf5f", 0), tuple(marker))

    def test_malformed_trace_is_skipped_without_losing_the_rest(self):
        good = {"row_key": (1, False), "times": [0.0, 1.0], "values": [1.0, 2.0],
                "name": "partial"}  # Every other field left at its default.
        broken = {"times": [0.0], "values": [1.0]}  # No row_key at all.
        self.workspace._render_frame([good, broken], [])
        self.assertEqual([t.name for t in self.workspace.row_plots[1].traces],
                         ["partial"])

    def test_transform_styles_map_to_the_trace(self):
        series = {"biopac:conductance_uS": {"times": [0.0, 1.0], "values": [1.0, 2.0],
                                            "segments": None}}

        def trace_for(transform):
            row = app.SignalRow(device="biopac", signal="conductance_uS",
                                transform=transform)
            return self.workspace._build_trace(row, series)

        markers = trace_for("markers")  # "No interpolation": dots only.
        self.assertTrue(markers["markers"])
        self.assertFalse(markers["line"])
        self.assertFalse(markers["spline"])
        spline = trace_for("spline")
        self.assertTrue(spline["spline"] and spline["line"])
        self.assertFalse(spline["markers"] or spline["steps"])
        line = trace_for("line")
        self.assertTrue(line["line"])
        self.assertFalse(line["markers"] or line["steps"] or line["spline"])

    def test_spline_smoothing_passes_through_every_sample(self):
        from gui.common.plots import spline_points
        points = [(0.0, 0.0), (1.0, 10.0), (2.0, 0.0)]
        smooth = spline_points(points)
        self.assertEqual(points[0], smooth[0])
        self.assertEqual(points[-1], smooth[-1])
        for original in points:
            self.assertIn(original, smooth)
        self.assertGreater(len(smooth), len(points))
        self.assertEqual([(3.0, 1.0)], spline_points([(3.0, 1.0)]))  # Too short.


if __name__ == "__main__":
    unittest.main()
