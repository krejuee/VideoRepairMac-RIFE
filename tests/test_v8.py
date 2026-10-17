import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import av
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import media
import master
import review
from test_cuts import cut_fixture
from test_engine import fixture, audio_info, video_info


def ten_bit_fixture(path, fmt="yuv422p10le", order="off", partial=False):
    with av.open(str(path), "w") as out:
        out.metadata["timecode"] = "10:00:00:00"
        video = out.add_stream("ffv1", rate=25)
        video.width, video.height, video.pix_fmt = 160, 96, fmt
        video.options = {"level": "3", "field_order": "tt" if order == "tff" else "bb" if order == "bff" else "progressive"}
        video.codec_context.colorspace = 1
        video.codec_context.color_range = 1
        video.codec_context.color_primaries = 1
        video.codec_context.color_trc = 1
        tag = engine.FieldTagger(order)
        yy, xx = np.mgrid[:96, :160]
        rgb = np.empty((96, 160, 3), np.uint16)
        rgb[..., 0] = 14000 + xx * 173
        rgb[..., 1] = 19000 + yy * 191
        rgb[..., 2] = 23000 + xx * 83
        if order != "off":
            rgb[1::2, :, 0] = 48000
            rgb[1::2, :, 2] = 12000
        for index in range(24):
            image = rgb.copy()
            if 8 <= index <= 10:
                image[:] = 0
            if partial and index == 7:
                image[1::2] = 0
            if order == "off":
                frame = av.VideoFrame.from_ndarray(image, format="rgb48le").reformat(format=fmt,
                    src_colorspace=5, dst_colorspace=1, src_color_range=2, dst_color_range=1, interpolation=engine.COLOR_FLAGS)
            else:
                frame = engine.weave_native_fields(image, fmt, 1, 1)
            frame.colorspace, frame.color_range = 1, 1
            frame.pts, frame.time_base = index, Fraction(1, 25)
            out.mux(video.encode(tag(frame)))
        out.mux(video.encode(None))


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mkv"
        cut_fixture(self.source)
        self.scan = engine.scan(self.source, max_gap="2s")
        self.gap = self.scan.gaps[-1]

    def tearDown(self):
        self.temp.cleanup()

    def test_changing_export_or_returning_to_old_settings_does_not_repeat_inference(self):
        first = review.render_event(self.scan, self.gap, self.root, "blend", "lossless")
        with patch("engine.Renderer.render", side_effect=AssertionError("Do not recompute pixels")):
            mov = review.render_event(self.scan, self.gap, self.root, "blend", "compatible")
        self.assertEqual(first["patch"], mov["patch"])
        self.assertNotEqual(first["clip"], mov["clip"])
        self.gap.mode = "hold"
        review.render_event(self.scan, self.gap, self.root, "blend", "lossless")
        self.gap.mode = "global"
        with patch("engine.Renderer.render", side_effect=AssertionError("Use previous setting")):
            restored = review.render_event(self.scan, self.gap, self.root, "blend", "lossless")
        self.assertEqual(first["patch"], restored["patch"])

    def test_native_patch_survives_cancel_during_preview_encoding(self):
        original = engine.write_clip
        def write(frames, path, *args, **kwargs):
            if kwargs.get("export") == "compatible":
                raise engine.Cancelled("test")
            return original(frames, path, *args, **kwargs)
        with patch("engine.write_clip", side_effect=write), self.assertRaises(engine.Cancelled):
            review.render_event(self.scan, self.gap, self.root, "hold", "compatible")
        saved = review.artifacts(self.scan, self.root)[review.event_key(self.gap)]
        self.assertTrue(Path(saved["patch"]).exists())
        self.assertFalse(saved.get("clip"))
        with patch("engine.Renderer.render", side_effect=AssertionError("Already saved")):
            review.render_event(self.scan, self.gap, self.root, "hold", "compatible")

    def test_missing_original_and_damaged_preview_do_not_invalidate_patch(self):
        first = review.render_event(self.scan, self.gap, self.root, "hold", "lossless")
        Path(first["original"]).unlink()
        with open(first["clip"], "r+b") as file:
            file.seek(100); file.write(b"broken")
        with patch("engine.Renderer.render", side_effect=AssertionError("Patch is intact")):
            restored = review.render_event(self.scan, self.gap, self.root, "hold", "lossless")
        self.assertTrue(Path(restored["original"]).is_file())

    def test_journals_recover_damaged_index_and_v7_patch_can_be_imported(self):
        clip = review.render_event(self.scan, self.gap, self.root, "hold", "lossless")
        directory = review.session_directory(self.scan, self.root)
        (directory / "INDEX.json").write_text("invalid")
        with patch("engine.Renderer.render", side_effect=AssertionError("Use journal")):
            review.render_event(self.scan, self.gap, self.root, "hold", "lossless")
        # Simulate an unchanged V7 entry with its old, export-dependent hash.
        data = review.read_session(self.scan, self.root)
        entry = data["events"][review.event_key(self.gap)]
        entry["fingerprint"] = review.legacy_fingerprint(self.scan, self.gap, "hold", "lossless")
        entry.pop("plan", None); entry.pop("exports", None)
        data["versions"] = {}
        review.save_session(self.scan, self.root, data)
        with patch("engine.Renderer.render", side_effect=AssertionError("Keep V7 repair")):
            reused = review.render_event(self.scan, self.gap, self.root, "hold", "compatible")
        self.assertEqual(reused["patch"], clip["patch"])

    def test_display_and_unused_global_settings_do_not_change_pixel_plan(self):
        self.gap.mode, self.gap.overlays = "hold", False
        old = review.fingerprint(self.scan, self.gap, "rife", "lossless")
        self.scan.settings.update(sensitivity=10, pixel_threshold=31, color_stabilize=False, force_cpu=True, language="en")
        self.assertEqual(old, review.fingerprint(self.scan, self.gap, "flow", "compatible"))


class TenBitTests(unittest.TestCase):
    def test_progressive_and_interlaced_lossless_keep_ten_bit_planes_and_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            for fmt, order in (("yuv420p10le", "off"), ("yuv422p10le", "tff"), ("yuv444p10le", "bff")):
                with self.subTest(fmt=fmt, order=order):
                    source = Path(folder) / f"{fmt}_{order}.mkv"
                    ten_bit_fixture(source, fmt, order, partial=order != "off")
                    scan = engine.scan(source, max_gap="2s", interlaced=order)
                    self.assertEqual(scan.bit_depth, 10)
                    gap = next(g for g in scan.gaps if g.length)
                    clip = review.render_event(scan, gap, folder, "blend", "lossless")
                    native = review.patch_frames(clip, scan, gap)
                    self.assertTrue(all(media.bit_depth(f) == 10 for f in native))
                    self.assertEqual(engine.frame_rgb(native[0], order).dtype, np.uint16)
                    output, _ = engine.repair(scan, folder, "blend", export="lossless")
                    with av.open(str(source)) as before, av.open(output) as after:
                        a, b = list(before.decode(video=0)), list(after.decode(video=0))
                    for i, (x, y) in enumerate(zip(a, b)):
                        self.assertEqual(y.format.name, fmt)
                        self.assertEqual(x.pts * x.time_base, y.pts * y.time_base)
                        if not gap.start <= i <= gap.end:
                            self.assertTrue(engine.same_pixels(x, y))
                    if order != "off":
                        for p, q in zip(a[7].planes, b[7].planes):
                            width = media.active_bytes(a[7], p)
                            self.assertTrue(np.array_equal(np.frombuffer(p, np.uint8).reshape(p.height, p.line_size)[::2, :width],
                                np.frombuffer(q, np.uint8).reshape(q.height, q.line_size)[::2, :width]))

    def test_prores_mov_is_ten_bit_and_xdcam_eight_bit_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "ten.mkv"
            ten_bit_fixture(source)
            scan = engine.scan(source)
            output, _ = engine.repair(scan, folder, "hold", export="compatible")
            with av.open(output) as video:
                stream = video.streams.video[0]
                self.assertEqual(stream.codec_context.name, "prores")
                self.assertEqual(media.bit_depth(stream.codec_context.format.name), 10)
                self.assertEqual(stream.metadata["timecode"], "10:00:00:00")
            with self.assertRaisesRegex(ValueError, "8 Bit"):
                engine.repair(scan, folder, "hold", export="mxf")

    def test_rife_preserves_more_than_eight_bit_output_precision(self):
        from rife_backend import Rife
        model = Rife(force_cpu=True)
        rgb = np.random.default_rng(17).integers(12000, 48000, (48, 64, 3), dtype=np.uint16)
        output = model.interpolate(rgb, np.roll(rgb, 2, axis=1), [.5], high_quality=False)[0]
        self.assertEqual(output.dtype, np.uint16)
        self.assertGreater(np.count_nonzero(output % 257), output.size // 2)


class ResumeMasterTests(unittest.TestCase):
    def test_cancel_resume_reuses_encoded_parts_and_completed_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mov"
            fixture(source)
            scan = engine.scan(source, max_gap="2s")
            stop = {"value": False}
            original = master._encode_part
            with patch.object(master, "PART_SECONDS", 2):
                def progress(p, message):
                    if "Exportabschnitt 2/" in message:
                        stop["value"] = True
                with self.assertRaises(engine.Cancelled):
                    engine.repair(scan, folder, "hold", export="compatible", progress=progress, cancel=lambda: stop["value"])
                parts = list((review.session_directory(scan, folder) / "MASTER_compatible").glob("part_*.json"))
                self.assertGreaterEqual(len(parts), 1)
                encoded = []
                def encode(result, start, *args, **kwargs):
                    encoded.append(start)
                    return original(result, start, *args, **kwargs)
                with patch("engine.Renderer.render", side_effect=AssertionError("No repeated inference")), patch("master._encode_part", side_effect=encode):
                    output, report = engine.repair(scan, folder, "hold", export="compatible")
                self.assertNotIn(0, encoded)
                self.assertEqual(video_info(source)[0], video_info(output)[0])
                self.assertEqual(audio_info(source), audio_info(output))
                with patch("master._encode_part", side_effect=AssertionError("Already finished")), patch("master._mux", side_effect=AssertionError("Already finished")):
                    same, same_report = engine.repair(scan, folder, "hold", export="compatible")
                self.assertEqual((same, same_report), (output, report))

    def test_only_part_overlapping_changed_repair_is_encoded_again(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mov"
            fixture(source)
            scan = engine.scan(source, max_gap="2s")
            with patch.object(master, "PART_SECONDS", 2):
                engine.repair(scan, folder, "hold", export="lossless")
                scan.gaps[0].mode = "blend"
                original = master._encode_part
                starts = []
                def encode(result, start, *args, **kwargs):
                    starts.append(start)
                    return original(result, start, *args, **kwargs)
                with patch("master._encode_part", side_effect=encode):
                    engine.repair(scan, folder, "hold", export="lossless")
                self.assertEqual(starts, [0])



class PersistenceAndLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.application = QApplication.instance() or QApplication([])

    def test_language_switch_keeps_event_ids_plans_and_pixmaps(self):
        import app, i18n
        from PySide6.QtCore import QSettings
        from PySide6.QtGui import QPixmap
        settings = QSettings('VideoRepair', 'VideoRepair')
        previous = settings.value('language', 'de')
        try:
            window = app.Window()
            window.language.setCurrentIndex(window.language.findData('de'))
            window.original_view.setPixmap(QPixmap(20, 20))
            data = window.mode.currentData()
            window.language.setCurrentIndex(window.language.findData('en'))
            self.assertEqual(window.scan_button.text(), 'Scan')
            self.assertEqual(window.resume_button.text(), 'Resume')
            self.assertEqual(window.event_type.itemText(window.event_type.findData('cut_gap')), 'Cut with black gap')
            self.assertEqual(window.mode.currentData(), data)
            self.assertFalse(window.original_view.pixmap().isNull())
            self.assertIn('C   Cut with black gap', i18n.t('Datei laden → Scannen → Ereignisse prüfen'))
            self.assertEqual(i18n.t('Exportabschnitt 2/4 bereits vorhanden'), 'Export part 2/4 already saved')
            self.assertIn('8 bit', i18n.t('Das XDCAM-MXF-Profil ist 8 Bit. Für 10 Bit bitte FFV1/MKV oder ProRes/MOV wählen.'))
            second = app.Window()
            self.assertEqual(second.scan_button.text(), 'Scan')
            second.close()
            window.language.setCurrentIndex(window.language.findData('de'))
            self.assertEqual(window.scan_button.text(), 'Scannen')
            window.close()
        finally:
            i18n.set_language(previous)

    def test_reopen_restores_project_settings_without_starting_scan(self):
        import app
        from PySide6.QtCore import QSettings
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.mov'
            destination = Path(folder) / 'renders'; destination.mkdir()
            fixture(source)
            window = app.Window()
            window.choose(str(source))
            window.directory = str(destination)
            window.mode.setCurrentIndex(window.mode.findData('blend'))
            window.export.setCurrentIndex(window.export.findData('prores'))
            window.sensitivity.setValue(71)
            window.max_gap.setCurrentIndex(window.max_gap.findData('1.5s'))
            window.result = engine.scan(source, max_gap='1.5s', sensitivity=71)
            window.save_review_decisions()
            key = window.directory_key()
            window.close()
            other = app.Window()
            with patch('engine.scan') as scan, patch('engine.repair') as repair:
                other.choose(str(source))
                scan.assert_not_called(); repair.assert_not_called()
            self.assertIsNone(other.result)
            self.assertEqual(other.output_directory(), str(destination))
            self.assertEqual(other.mode.currentData(), 'blend')
            self.assertEqual(other.export.currentData(), 'prores')
            self.assertEqual(other.sensitivity.value(), 71)
            self.assertEqual(other.max_gap.currentData(), '1.5s')
            self.assertTrue(other.resume_button.isEnabled())
            with patch.object(other, 'launch') as launch:
                other.resume_button.click()
                launch.assert_called_once_with(False)
                self.assertTrue(other.pending_resume)
            other.close()
            QSettings('VideoRepair', 'VideoRepair').remove(key)


class FinalMuxResumeTests(unittest.TestCase):
    def check_audio(self, source, output):
        original, saved = audio_info(source), audio_info(output)
        self.assertEqual(len(original), len(saved))
        self.assertEqual([p[2] for p in original], [p[2] for p in saved])
        for a, b in zip(original, saved):
            self.assertLessEqual(abs(a[0] - b[0]), Fraction(1, 1000))
            self.assertLessEqual(abs(a[1] - b[1]), Fraction(1, 1000))

    def test_cancel_in_final_mux_reuses_all_parts_and_corrupt_part_alone_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.mov'; fixture(source)
            scan = engine.scan(source, max_gap='2s')
            with patch.object(master, 'PART_SECONDS', 2):
                with patch('master._mux', side_effect=engine.Cancelled()), self.assertRaises(engine.Cancelled):
                    engine.repair(scan, folder, 'hold', export='lossless')
                job = review.session_directory(scan, folder) / 'MASTER_lossless'
                parts = sorted(job.glob('part_*.mkv'))
                self.assertEqual(len(parts), 3)
                damaged = parts[1]
                with open(damaged, 'r+b') as file:
                    file.seek(100); file.write(b'broken')
                original = master._encode_part
                starts = []
                def encode(result, start, *args, **kwargs):
                    starts.append(start)
                    return original(result, start, *args, **kwargs)
                with patch('master._encode_part', side_effect=encode), patch('engine.Renderer.render', side_effect=AssertionError('Reuse repairs')):
                    output, _ = engine.repair(scan, folder, 'hold', export='lossless')
                self.assertEqual(starts, [50])
                self.assertEqual(video_info(source)[0], video_info(output)[0])
                self.check_audio(source, output)

    def test_part_boundaries_keep_variable_frame_timing_and_delayed_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.mov'; fixture(source, variable=True, delayed_audio=True)
            scan = engine.scan(source, max_gap='2s')
            with patch.object(master, 'PART_SECONDS', .7):
                output, _ = engine.repair(scan, folder, 'hold', export='lossless')
            self.assertEqual(video_info(source)[0], video_info(output)[0])
            self.check_audio(source, output)

    def test_mxf_parts_keep_tff_timecode_and_pcm(self):
        from test_sessions import hd_fixture
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'hd.mkv'; hd_fixture(source)
            scan = engine.scan(source, max_gap='2s', interlaced='tff')
            with patch.object(master, 'PART_SECONDS', .6):
                output, _ = engine.repair(scan, folder, 'hold', export='mxf')
            data = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', output]))
            self.assertEqual(data['streams'][0]['field_order'], 'tt')
            self.assertEqual(data['format']['tags']['timecode'], '10:00:00:00')
            self.assertEqual(video_info(source)[0], video_info(output)[0])
            self.check_audio(source, output)

    def test_real_rife_ten_bit_interlaced_patch_is_reused_in_master(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'ten.mkv'; ten_bit_fixture(source, order='tff')
            scan = engine.scan(source, force_cpu=True, interlaced='tff')
            gap = next(g for g in scan.gaps if g.approved)
            clip = review.render_event(scan, gap, folder, 'rife', 'lossless')
            native = review.patch_frames(clip, scan, gap)
            self.assertTrue(all(media.bit_depth(f) == 10 for f in native))
            with patch('engine.Renderer.render', side_effect=AssertionError('Do not repeat RIFE')):
                output, _ = engine.repair(scan, folder, 'rife', export='lossless')
            self.assertEqual(video_info(source)[0], video_info(output)[0])


if __name__ == "__main__":
    unittest.main()
