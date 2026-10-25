import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import av
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QEventLoop, QTimer
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import engine
import review
import storage
from test_engine import fixture, audio_info
from test_options import title_fixture


class TitleRemovalTests(unittest.TestCase):
    def test_title_detection_stays_but_legacy_flags_never_composite_title(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'title.mov'
            title_fixture(source, only_gap=True)
            scan = engine.scan(source, max_gap=12, sensitivity=50, preserve_overlays=True)
            gap = scan.gaps[0]
            self.assertEqual((gap.start, gap.length, gap.approved), (15, 12, True))
            scan.settings.update(preserve_overlays=True, force_cpu=True)
            gap.overlays = True
            clip = review.render_event(scan, gap, folder, 'rife', 'lossless')
            image = engine.frame_rgb(review.patch_frames(clip, scan, gap)[5], 'off')
            self.assertGreater(image.mean(), 90)
            self.assertLess(image[72:78, 40:120].mean(), 160)
            current = review.fingerprint(scan, gap, 'rife')
            plan = review.repair_plan(scan, gap, 'rife')
            plan['overlays'] = True
            self.assertFalse(review.patch_matches(scan, gap, 'rife', dict(clip, fingerprint=storage.digest(plan))))
            gap.overlays = False
            scan.settings['preserve_overlays'] = False
            self.assertEqual(review.fingerprint(scan, gap, 'rife'), current)


class AutomaticMasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def wait(self, condition):
        self.application.processEvents()
        if condition():
            return
        loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
        poll.timeout.connect(lambda: loop.quit() if condition() else None)
        poll.start(10)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(45000)
        loop.exec()
        poll.stop()
        deadline.stop()
        self.assertTrue(condition(), 'GUI task did not complete')

    def load(self, path):
        window = app.Window()
        window.mode.setCurrentIndex(window.mode.findData('hold'))
        window.choose(str(path))
        window.accept_scan(engine.scan(path, max_gap='2s', sensitivity=50))
        self.wait(lambda: not window.is_busy())
        return window

    def test_default_batch_finishes_prores_with_audio_without_question(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'overnight.mov'
            fixture(source)
            window = self.load(source)
            self.assertTrue(window.auto_master.isChecked())
            self.assertEqual(window.export.currentData(), 'prores')
            self.assertFalse(hasattr(window, 'preserve_overlays'))
            self.assertFalse(hasattr(window, 'event_overlays'))
            with patch.object(QMessageBox, 'question', side_effect=AssertionError('No unattended modal')):
                window.render_all_button.click()
                self.wait(lambda: bool(window.output) and not window.is_busy())
            self.assertTrue(window.output.endswith('.mov'))
            with av.open(window.output) as video:
                self.assertEqual(video.streams.video[0].codec_context.name, 'prores')
                self.assertEqual(sum(1 for _ in video.decode(video=0)), window.result.count)
            self.assertEqual(audio_info(source), audio_info(window.output))
            window.close()

    def test_disabled_batch_asks_and_no_keeps_review_clips(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'manual.mov'
            fixture(source)
            window = self.load(source)
            window.auto_master.setChecked(False)
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.No) as question:
                window.render_all_button.click()
                self.wait(lambda: question.called and not window.is_busy())
                self.assertEqual(question.call_count, 1)
            self.assertFalse(window.output)
            self.assertTrue(window.artifacts)
            self.assertTrue(all(Path(v['clip']).is_file() for v in window.artifacts.values()))
            window.close()
            reopened = app.Window()
            reopened.choose(str(source))
            self.assertFalse(reopened.auto_master.isChecked())
            self.assertIsNone(reopened.result)
            reopened.close()

    def test_single_event_stays_separate_even_with_automatic_master_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'single.mov'
            fixture(source)
            window = self.load(source)
            with patch.object(window, 'launch', side_effect=AssertionError('Single event must stay separate')):
                window.render_selected()
                self.wait(lambda: bool(window.artifacts) and not window.is_busy())
            self.assertFalse(window.output)
            self.assertFalse(window.ask_after_render)
            window.close()

    def test_v8_mkv_preference_migrates_once_but_explicit_v9_choice_remains(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'old.mov'
            fixture(source)
            scan = engine.scan(source)
            data = review.read_session(scan, folder)
            data['preferences'] = {'settings': {'preserve_overlays': True}, 'export': 'lossless'}
            review.save_session(scan, folder, data)
            window = app.Window()
            window.choose(str(source))
            self.assertEqual(window.export.currentData(), 'prores')
            self.assertTrue(window.auto_master.isChecked())
            window.result = scan
            window.export.setCurrentIndex(window.export.findData('lossless'))
            window.save_review_decisions()
            window.close()
            reopened = app.Window()
            reopened.choose(str(source))
            self.assertEqual(reopened.export.currentData(), 'lossless')
            reopened.close()
