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
        self.decomposition = None

    def configuration_snapshot(self):
        return self.configuration

    def apply_configuration(self, values):
        self.configuration = values

    def set_decomposition(self, **values):
        self.decomposition = values

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
        self.assertEqual(self.workspace.table_rows[-1]["device"].get(), "biopac")

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
        # validatable rule as device/signal rows).
        features = app.SignalRow(device="features", signal="SCR frequency",
                                 source_a="biopac:conductance_uS", color="#ff0000")
        comparison = app.SignalRow(device="comparison", signal=app.COMPARISON_CHOICES[0],
                                   source_a="biopac:conductance_uS",
                                   source_b="heepidermis:conductance_uS", color="#00ff00")
        self.workspace._add_table_row(features)
        self.workspace._add_table_row(comparison)
        self.assertEqual(self.workspace.table_rows[0]["source_a"].get(), "biopac:conductance_uS")
        self.assertEqual(self.workspace.table_rows[1]["source_a"].get(), "biopac:conductance_uS")
        self.assertEqual(self.workspace.table_rows[1]["source_b"].get(), "heepidermis:conductance_uS")

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
        ws.decomposition_variables["smoothing"].set("3")
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
        ws.decomposition_variables["smoothing"].set("9")
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
        self.assertEqual(panels["heepidermis"].decomposition["smoothing"], 3)
        self.assertEqual(panels["biopac"].decomposition["smoothing"], 3)
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


if __name__ == "__main__":
    unittest.main()
