import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QTest
import app
import engine
import review
from test_engine import fixture

class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setStyleSheet(app.STYLE)

    def test_load_scan_review_then_explicit_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "gui.mov"
            fixture(source)
            window = app.Window()
            window.mode.setCurrentIndex(window.mode.findData("flow"))
            window.max_gap.setCurrentIndex(5)
            window.export.setCurrentIndex(window.export.findData("compatible"))
            window.show()
            with patch("engine.scan") as scan, patch("engine.repair") as repair:
                window.choose(str(source))
                QTest.qWait(150)
                scan.assert_not_called(); repair.assert_not_called()
            self.assertIsNone(window.worker)
            self.assertTrue(window.scan_button.isEnabled())
            self.assertFalse(window.repair_button.isEnabled())
            self.assertEqual(window.table.rowCount(), 0)
            self.assertEqual(list(Path(directory).glob("*_report*")), [])
            with patch("engine.Renderer.render", side_effect=AssertionError("Original preview must not invoke inference")):
                window.scan_button.click()
                self.wait_for(window.worker)
                self.wait_until(lambda: window.preview_data is not None and window.repair_button.isEnabled() and not window.is_busy())
            self.assertFalse(window.output)
            self.assertTrue(window.show_report.isEnabled())
            self.assertEqual(window.table.rowCount(), 3)
            self.assertEqual(window.table.item(2, 1).text(), "Schnitt mit schwarzer Lücke")
            self.assertEqual(window.preview_data.comparison, "original")
            self.assertFalse(window.original_view.pixmap().isNull())
            self.assertTrue(window.repaired_view.pixmap().isNull())
            window.auto_master.setChecked(False)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                window.repair_button.click()
                self.wait_until(lambda: not window.is_busy() and len(window.artifacts) == 2)
                QTest.qWait(350)
            self.assertFalse(window.output)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                window.repair_button.click()
                self.wait_for(window.worker)
            self.assertTrue(window.output)
            self.assertTrue(Path(window.output).exists())
            self.assertEqual(window.table.item(0, 3).text(), "Repariert")
            window.close()

    def test_click_preview_type_method_and_slider_keep_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review.mov"
            fixture(source)
            window = app.Window()
            window.mode.setCurrentIndex(window.mode.findData("hold"))
            window.choose(str(source))
            window.scan_button.click()
            self.wait_for(window.worker)
            self.wait_until(lambda: window.preview_data is not None and window.event_type.isEnabled() and not window.is_busy())
            window.table.selectRow(2)
            self.wait_until(lambda: window.preview_data is not None and window.event_type.isEnabled() and not window.is_busy())
            self.assertEqual(window.current_gap().effective_kind, "cut_gap")
            self.assertTrue(window.current_gap().approved)
            self.assertEqual(window.preview_data.comparison, "original")
            source_pixels = window.preview_data.before
            position = window.frame_slider.value()
            window.event_type.setCurrentIndex(window.event_type.findData("gap"))
            window.event_mode.setCurrentIndex(window.event_mode.findData("blend"))
            self.assertTrue(window.current_gap().approved)
            self.assertEqual(window.current_gap().effective_kind, "gap")
            self.assertIs(window.preview_data.before, source_pixels)
            self.assertEqual(window.frame_slider.value(), position)
            window.step_frame(1)
            window.toggle_playback()
            QTest.qWait(90)
            self.assertGreater(window.frame_slider.value(), 0)
            window.stop_playback()
            window.preview_button.click()
            self.wait_for(window.render_worker)
            self.wait_until(lambda: window.preview_data.comparison == "clip" and not window.is_busy())
            self.assertTrue(Path(window.artifacts[review.event_key(window.current_gap())]["clip"]).is_file())
            self.assertEqual(window.preview_data.comparison, "clip")
            before = window.preview_data.before
            with patch("engine.scan", side_effect=AssertionError("No rescan")), patch("engine.av.open", side_effect=AssertionError("Same preview is cached")):
                window.sensitivity.setValue(80)
                QTest.qWait(220)
            self.assertEqual(window.table.currentRow(), 2)
            self.assertEqual(window.current_gap().type_override, "gap")
            self.assertEqual(window.current_gap().mode, "blend")
            self.assertIs(window.preview_data.before, before)
            self.assertEqual(window.preview_data.comparison, "original")
            self.assertTrue(window.play_button.isEnabled())
            window.event_type.setCurrentIndex(window.event_type.findData("fade"))
            self.assertFalse(window.current_gap().approved)
            window.close()

    def test_live_scan_list_is_clickable_and_changes_survive_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "live.mov"
            fixture(source)
            full = engine.scan(source, max_gap=12, sensitivity=50)
            cache = replace(full.cache, **{name: getattr(full.cache, name)[:65].copy() for name in
                ("thumbs", "means", "fractions", "field_fractions", "color_thumbs", "changes")})
            partial = replace(full, count=65, timestamps=full.timestamps[:65], durations=full.durations[:65],
                              cache=cache, gaps=[], partial=True, review_history={})
            engine.refresh_scan(partial)
            finish = threading.Event()
            def scanning(path, max_gap, progress, cancel, on_partial, **settings):
                on_partial(partial)
                while not finish.wait(.01):
                    engine.check(cancel)
                return full
            window = app.Window()
            window.show()
            window.choose(str(source))
            with patch("engine.scan", side_effect=scanning):
                window.scan_button.click()
                self.wait_until(lambda: window.preview_data is not None and "preview" not in window.jobs)
                self.assertIn("scan", window.jobs)
                self.assertTrue(window.table.isEnabled())
                self.assertTrue(window.event_mode.isEnabled())
                self.assertEqual(window.table.rowCount(), 1)
                window.event_mode.setCurrentIndex(window.event_mode.findData("blend"))
                window.decision.setCurrentIndex(window.decision.findData("skip"))
                with patch("engine.av.open", side_effect=AssertionError("Slider must use current cache")):
                    window.sensitivity.setValue(80)
                    QTest.qWait(200)
                finish.set()
                self.wait_for(window.worker)
                self.wait_until(lambda: not window.is_busy())
            self.assertEqual(window.table.rowCount(), 3)
            self.assertEqual((window.result.gaps[0].decision, window.result.gaps[0].mode), ("skip", "blend"))
            self.assertEqual(window.result.settings["sensitivity"], 80)
            window.close()

    def test_queue_remains_interactive_and_cancel_keeps_first_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "queue.mov"
            fixture(source)
            window = app.Window()
            window.mode.setCurrentIndex(window.mode.findData("hold"))
            window.choose(str(source))
            window.scan_button.click()
            self.wait_for(window.worker)
            self.wait_until(lambda: not window.is_busy())
            first, second = [g for g in window.result.gaps if g.approved][:2]
            original_render = review.render_event
            entered_second = threading.Event()
            def rendering(result, gap, *args, **kwargs):
                if gap.start == second.start:
                    entered_second.set()
                    while not kwargs["cancel"]():
                        threading.Event().wait(.01)
                    raise engine.Cancelled()
                return original_render(result, gap, *args, **kwargs)
            with patch("review.render_event", side_effect=rendering):
                window.render_all_button.click()
                self.wait_until(entered_second.is_set)
                self.assertIn("render", window.jobs)
                self.assertIn("Korrigiert", window.table.item(0, 3).text())
                window.table.selectRow(1)
                self.wait_until(lambda: window.preview_data is not None and "preview" not in window.jobs)
                window.table.selectRow(0)
                self.wait_until(lambda: window.preview_data is not None and window.preview_data.comparison == "clip")
                self.assertTrue(window.event_mode.isEnabled())
                window.event_mode.setCurrentIndex(window.event_mode.findData("blend"))
                self.assertIn("Plan geändert", window.table.item(0, 3).text())
                window.cancel_button.click()
                self.wait_for(window.render_worker)
                self.wait_until(lambda: not window.is_busy())
            saved = review.artifacts(window.result, directory)
            self.assertIn(review.event_key(first), saved)
            self.assertNotIn(review.event_key(second), saved)
            self.assertTrue(Path(saved[review.event_key(first)]["clip"]).exists())
            window.auto_master.setChecked(False)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                window.render_all_button.click()
                self.wait_for(window.render_worker)
                self.wait_until(lambda: not window.is_busy())
                QTest.qWait(350)
            self.assertFalse(window.output)
            self.assertEqual(len(window.artifacts), 3)
            self.assertIn("Korrigiert", window.table.item(1, 3).text())
            window.close()

    def test_shortcuts_classify_change_method_navigate_and_show_help(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "keys.mov"
            fixture(source)
            window = app.Window()
            window.show()
            window.choose(str(source))
            window.scan_button.click()
            self.wait_for(window.worker)
            self.wait_until(lambda: not window.is_busy())
            window.activateWindow()
            QTest.qWait(80)
            QTest.keyClick(window, Qt.Key_D)
            self.assertEqual(window.table.currentRow(), 1)
            QTest.keyClick(window, Qt.Key_A)
            self.assertEqual(window.table.currentRow(), 0)
            QTest.keyClick(window, Qt.Key_C)
            self.assertEqual((window.current_gap().effective_kind, window.current_gap().mode, window.current_gap().approved),
                             ("cut_gap", "motion", True))
            QTest.keyClick(window, Qt.Key_B)
            self.assertFalse(window.current_gap().approved)
            QTest.keyClick(window, Qt.Key_I)
            self.assertEqual((window.current_gap().effective_kind, window.current_gap().mode, window.current_gap().approved),
                             ("gap", "blend", True))
            QTest.keyClick(window, Qt.Key_U)
            self.assertEqual(window.result.gaps[0].decision, "skip")
            self.assertEqual(window.table.currentRow(), 1)
            with patch.object(QMessageBox, "information") as info:
                QTest.keyClick(window, Qt.Key_H)
                info.assert_called_once()
                self.assertIn("Q   RIFE Qualität", info.call_args.args[2])
            self.wait_until(lambda: not window.is_busy())
            window.close()

    def wait_for(self, worker):
        self.wait_until(lambda: not worker.isRunning())
        self.application.processEvents()
        self.assertFalse(worker.isRunning())

    def wait_until(self, condition):
        self.application.processEvents()
        if condition():
            return
        loop = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if condition() else None)
        poll.start(10)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(30000)
        loop.exec()
        poll.stop(); deadline.stop()
        self.assertTrue(condition(), "UI operation did not complete")

if __name__ == "__main__":
    unittest.main()
