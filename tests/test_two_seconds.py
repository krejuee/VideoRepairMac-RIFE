import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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
import review
from test_cuts import cut_fixture


def long_fixture(path, rate=25, length=50, step=None, cut=False):
    with av.open(str(path), "w") as out:
        out.metadata["timecode"] = "10:00:00:00"
        video = out.add_stream("ffv1", rate=rate)
        video.width, video.height, video.pix_fmt = 160, 96, "yuv444p"
        video.codec_context.time_base = Fraction(1, 1000)
        elapsed = Fraction(0)
        for index in range(length + 20):
            image = np.full((96, 160, 3), (80, 120, 160), np.uint8)
            if 10 <= index < 10 + length:
                image[:] = 0
            elif cut and index >= 10 + length:
                image[:] = (230, 190, 80)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts, frame.time_base = round(elapsed * 1000), Fraction(1, 1000)
            out.mux(video.encode(frame))
            elapsed += step if step and 10 <= index < 10 + length else Fraction(1, rate)
        out.mux(video.encode(None))


class TwoSecondTests(unittest.TestCase):
    def test_two_seconds_at_25_and_50_fps_are_fully_visible_and_exported(self):
        with tempfile.TemporaryDirectory() as folder:
            for rate in (25, 50):
                with self.subTest(rate=rate):
                    source = Path(folder) / f"long_{rate}.mkv"
                    long_fixture(source, rate, rate * 2)
                    result = engine.scan(source, max_gap="2s")
                    gap = next(g for g in result.gaps if g.length)
                    self.assertEqual(gap.length, rate * 2)
                    self.assertEqual(engine.gap_duration(result, gap), 2)
                    self.assertTrue(gap.approved)
                    clip = review.render_event(result, gap, folder, "hold", "lossless")
                    self.assertGreater(clip["end"], gap.end)
                    viewed = review.preview_artifact(result, gap, clip)
                    self.assertTrue(all(viewed.after[i].mean() > 40 for i in range(viewed.selected, viewed.selected + gap.length)))
                    with patch("engine.Renderer.render", side_effect=AssertionError("No second inference")):
                        output, _ = engine.repair(result, folder, "hold", export="lossless", artifacts=review.artifacts(result, folder))
                    with av.open(output) as container:
                        frames = list(container.decode(video=0))
                    self.assertEqual(len(frames), result.count)
                    self.assertTrue(all(frames[i].to_ndarray(format="gray").mean() > 40 for i in range(gap.start, gap.end + 1)))

    def test_limit_changes_update_cached_list_without_rescan(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "cache.mkv"
            long_fixture(source)
            result = engine.scan(source, max_gap=12)
            self.assertFalse(result.gaps[0].approved)
            with patch("engine.av.open", side_effect=AssertionError("No rescan")):
                engine.refresh_scan(result, max_gap="2s")
                self.assertTrue(result.gaps[0].approved)
                engine.refresh_scan(result, max_gap="0.5s")
                self.assertFalse(result.gaps[0].approved)
                engine.refresh_scan(result, max_gap="2s")
                self.assertTrue(result.gaps[0].approved)

    def test_over_two_seconds_cannot_be_forced_or_defined_manually(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "too_long.mkv"
            long_fixture(source, length=51)
            result = engine.scan(source, max_gap="2s")
            gap = result.gaps[0]
            self.assertFalse(gap.approved)
            with self.assertRaises(ValueError):
                engine.set_gap_decision(result, gap, "repair")
            with self.assertRaises(ValueError):
                engine.set_event_range(result, gap, 10, 60)
            engine.set_event_range(result, gap, 10, 59)
            engine.set_event_type(result, gap, "gap")
            self.assertTrue(gap.approved)

    def test_variable_rate_uses_elapsed_duration_not_nominal_frame_count(self):
        with tempfile.TemporaryDirectory() as folder:
            for length, step, expected in ((60, Fraction(1, 50), True), (30, Fraction(2, 25), False)):
                source = Path(folder) / f"vfr_{length}.mkv"
                long_fixture(source, length=length, step=step)
                result = engine.scan(source, max_gap="2s")
                gap = next(g for g in result.gaps if g.length)
                self.assertEqual(gap.approved, expected)
                self.assertEqual(engine.gap_duration(result, gap), length * step)

    def test_cut_gap_keeps_six_frame_motion_and_complete_two_second_clip(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "cut_long.mkv"
            long_fixture(source, cut=True)
            result = engine.scan(source, max_gap="2s")
            gap = next(g for g in result.gaps if g.length)
            engine.set_event_type(result, gap, "cut_gap")
            with patch("rife_backend.Rife", side_effect=AssertionError("Do not interpolate scenes")):
                clip = review.render_event(result, gap, folder, "rife_hq", "lossless")
            self.assertEqual(clip["motion_reference_frames"], 6)
            frames = review.patch_frames(clip, result, gap)
            self.assertEqual(len(frames), 50)
            left = engine.read_window(result.source, result.timestamps, gap.start - 1, gap.start - 1)[0]
            self.assertTrue(all(np.abs(engine.frame_rgb(f).astype(float) - engine.frame_rgb(left)).mean() < 2 for f in frames))

    def test_manual_cut_range_and_decisions_survive_new_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "manual.mkv"
            cut_fixture(source)
            result = engine.scan(source, max_gap="2s")
            gap = result.gaps[0]
            engine.set_event_type(result, gap, "gap")
            engine.set_event_range(result, gap, 20, 22)
            review.save_decisions(result, folder)
            fresh = engine.scan(source, max_gap="2s")
            review.restore_decisions(fresh, folder)
            engine.refresh_scan(fresh, max_gap="2s")
            self.assertEqual((fresh.gaps[0].start, fresh.gaps[0].end, fresh.gaps[0].approved), (20, 22, True))

    def test_motion_prediction_uses_all_six_moving_pictures(self):
        import cv2
        base = np.random.default_rng(18).integers(40, 210, (96, 160, 3), dtype=np.uint8)
        history = [cv2.warpAffine(base, np.float32([[1, 0, 2*i], [0, 1, 0]]), (160, 96), borderMode=cv2.BORDER_REFLECT) for i in range(6)]
        predicted = engine.extend_motion(history, history[-1], [.04, .08], [.04]*5)
        for index, image in enumerate(predicted, 1):
            expected = cv2.warpAffine(history[-1], np.float32([[1, 0, 2*index], [0, 1, 0]]), (160, 96), borderMode=cv2.BORDER_REPLICATE)
            self.assertLess(np.abs(image[15:-15, 30:-15].astype(float) - expected[15:-15, 30:-15]).mean(), 5)

    def test_decoder_field_order_detection_from_flags(self):
        for order in ("off", "tff", "bff"):
            frame = av.VideoFrame(160, 96, "yuv422p")
            frame.pts, frame.time_base = 0, Fraction(1, 25)
            frame = engine.FieldTagger(order)(frame)
            self.assertEqual(engine.frame_field_order(frame), order)

    def test_saved_range_waits_for_frames_during_partial_rescan(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "partial.mkv"
            cut_fixture(source)
            full = engine.scan(source, max_gap="2s")
            engine.set_event_type(full, full.gaps[0], "gap")
            engine.set_event_range(full, full.gaps[0], 20, 28)
            review.save_decisions(full, folder)
            cache = replace(full.cache, **{k: getattr(full.cache, k)[:25].copy() for k in
                ("thumbs", "means", "fractions", "field_fractions", "color_thumbs", "changes")})
            partial = replace(full, count=25, timestamps=full.timestamps[:25], durations=full.durations[:25],
                cache=cache, gaps=[], partial=True, review_history={})
            engine.refresh_scan(partial)
            review.restore_decisions(partial, folder)
            engine.refresh_scan(partial)
            self.assertEqual(engine.gap_duration(partial, partial.gaps[0]), 0)
            fresh = engine.scan(source, max_gap="2s")
            review.restore_decisions(fresh, folder)
            self.assertEqual(fresh.gaps[0].end, 28)

    def test_rife_renders_the_complete_two_second_patch(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "rife_long.mkv"
            long_fixture(source)
            result = engine.scan(source, max_gap="2s", force_cpu=True)
            gap = result.gaps[0]
            clip = review.render_event(result, gap, folder, "rife", "lossless")
            self.assertEqual(len(review.patch_frames(clip, result, gap)), 50)
            self.assertEqual(clip["inference"]["model"], "Practical-RIFE 4.25")

    def test_ui_offers_two_seconds_and_L_for_cuts(self):
        from PySide6.QtWidgets import QApplication
        import app
        application = QApplication.instance() or QApplication([])
        window = app.Window()
        self.assertEqual(window.max_gap.currentData(), "2s")
        self.assertEqual(window.interlaced.currentData(), "auto")
        self.assertTrue(any(s.key().toString() == "L" for s in window.shortcuts))
        window.close()
        application.processEvents()


if __name__ == "__main__":
    unittest.main()
