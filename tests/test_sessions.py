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
import review
from test_cuts import cut_fixture


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "cuts.mkv"
        cut_fixture(self.source)
        self.result = engine.scan(self.source, max_gap=12)

    def tearDown(self):
        self.temp.cleanup()

    def test_saved_clip_has_context_runtime_tc_and_can_be_viewed(self):
        gap = self.result.gaps[-1]
        clip = review.render_event(self.result, gap, self.root, "hold", "compatible")
        self.assertIn("LaufzeitTC_00-00-02-05", Path(clip["clip"]).name)
        self.assertEqual(clip["clip_timecode"], "00:00:01:05")
        self.assertEqual((clip["start"], clip["end"]), (30, 83))
        with av.open(clip["clip"]) as container:
            self.assertEqual(container.streams.video[0].metadata["timecode"], "00:00:01:05")
            self.assertEqual(sum(1 for f in container.decode(video=0)), 54)
        viewed = review.preview_artifact(self.result, gap, clip)
        self.assertEqual(viewed.comparison, "clip")
        self.assertEqual(len(viewed.before), len(viewed.after))
        self.assertGreater(viewed.after[viewed.selected].mean(), 30)

    def test_complete_clips_survive_cancellation_and_resume_without_inference(self):
        first = self.result.gaps[-1]
        clip = review.render_event(self.result, first, self.root, "hold", "lossless")
        engine.set_gap_decision(self.result, self.result.gaps[1], "repair")
        with self.assertRaises(engine.Cancelled):
            review.render_event(self.result, self.result.gaps[1], self.root, "hold", "lossless", cancel=lambda: True)
        saved = review.artifacts(self.result, self.root)
        self.assertEqual(saved[review.event_key(first)]["clip"], clip["clip"])
        with patch("engine.Renderer.render", side_effect=AssertionError("Already rendered")):
            resumed = review.render_event(self.result, first, self.root, "hold", "lossless")
        self.assertEqual(resumed, clip)
        self.assertFalse(list(review.session_directory(self.result, self.root).glob(".clip-*")))

    def test_master_uses_saved_native_repair_instead_of_second_inference(self):
        gap = self.result.gaps[-1]
        clip = review.render_event(self.result, gap, self.root, "blend", "lossless")
        for other in self.result.gaps:
            if other.approved:
                review.render_event(self.result, other, self.root, "blend", "lossless")
        with patch("engine.Renderer.render", side_effect=AssertionError("Do not compute twice")):
            output, _ = engine.repair(self.result, self.root, "blend", export="lossless",
                                     artifacts=review.artifacts(self.result, self.root))
        native = review.patch_frames(clip, self.result, gap)
        frames = engine.read_window(output, self.result.timestamps, gap.start, gap.end)
        self.assertTrue(all(engine.same_pixels(a, b) for a, b in zip(native, frames)))

    def test_changed_plan_is_stale_and_decisions_restore_after_new_scan(self):
        gap = self.result.gaps[1]
        engine.set_event_type(self.result, gap, "gap")
        gap.mode = "blend"
        review.save_decisions(self.result, self.root)
        clip = review.render_event(self.result, gap, self.root, "hold", "lossless")
        reloaded = engine.scan(self.source, max_gap=12)
        saved = review.restore_decisions(reloaded, self.root)
        restored = reloaded.gaps[1]
        self.assertEqual((restored.effective_kind, restored.mode, restored.approved), ("gap", "blend", True))
        self.assertTrue(review.matches(reloaded, restored, "hold", "lossless", saved[review.event_key(restored)]))
        restored.mode = "flow"
        self.assertFalse(review.matches(reloaded, restored, "hold", "lossless", clip))
        self.assertTrue(Path(clip["clip"]).is_file())

    def test_source_tc_and_drop_frame_boundary(self):
        self.result.source_timecode = "10:00:00:00"
        gap = self.result.gaps[-1]
        clip = review.render_event(self.result, gap, self.root, "hold", "compatible")
        self.assertEqual(clip["event_timecode"], "10:00:02:05")
        self.assertEqual(clip["clip_timecode"], "10:00:01:05")
        with av.open(clip["clip"]) as container:
            self.assertEqual(container.streams.video[0].metadata["timecode"], clip["clip_timecode"])
        self.assertEqual(engine.smpte_timecode("30000/1001", 1, "00:00:59;29"), "00:01:00;02")
        self.assertEqual(engine.smpte_timecode("30000/1001", 17982, "00:00:00;00"), "00:10:00;00")
        self.assertEqual(engine.smpte_timecode("60000/1001", 1, "00:00:59;59"), "00:01:00;04")
        self.assertEqual(engine.smpte_timecode("25", 1, "23:59:59:24"), "00:00:00:00")

    def test_live_scan_emits_independent_cache_and_never_requires_rescan(self):
        partials = []
        result = engine.scan(self.source, max_gap=12, on_partial=partials.append)
        self.assertTrue(partials)
        self.assertTrue(partials[0].partial)
        self.assertLess(partials[0].count, result.count)
        self.assertFalse(result.partial)
        with patch("engine.av.open", side_effect=AssertionError("Cached filter only")):
            engine.refresh_scan(partials[0], sensitivity=80)
        self.assertEqual(result.count, 90)

    def test_clip_rendered_during_scan_can_be_viewed_after_scan_grows(self):
        from dataclasses import replace
        partial = replace(self.result, count=25, timestamps=self.result.timestamps[:25],
                          durations=self.result.durations[:25], partial=True)
        cut = partial.gaps[0]
        clip = review.render_event(partial, cut, self.root, "hold", "lossless")
        self.assertEqual(clip["end"], 24)
        viewed = review.preview_artifact(self.result, self.result.gaps[0], clip)
        self.assertEqual(len(viewed.after), len(viewed.before))
        self.assertEqual(len(viewed.after), 25)


def hd_fixture(path):
    with av.open(str(path), "w") as out:
        out.metadata["timecode"] = "10:00:00:00"
        video = out.add_stream("ffv1", rate=25)
        video.width, video.height, video.pix_fmt = 1920, 1080, "yuv422p"
        video.codec_context.colorspace = 1
        video.codec_context.color_range = 1
        audio = out.add_stream("pcm_s16le", rate=48000)
        audio.layout = "mono"
        for index in range(50):
            image = np.full((1080, 1920, 3), [80, 120, 160] if not 18 <= index < 21 else 0, np.uint8)
            frame = av.VideoFrame.from_ndarray(image, "rgb24").reformat(format="yuv422p")
            frame.pts, frame.time_base = index, Fraction(1, 25)
            out.mux(video.encode(frame))
            samples = np.arange(index * 1920, (index + 1) * 1920, dtype=np.int32).astype(np.int16).reshape(1, -1)
            sound = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            sound.sample_rate, sound.time_base, sound.pts = 48000, Fraction(1, 48000), index * 1920
            out.mux(audio.encode(sound))
        out.mux(video.encode(None)); out.mux(audio.encode(None))


class MxfTests(unittest.TestCase):
    def test_xdcam_clip_and_master_keep_tff_tc_pcm_and_frame_timing(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "xdcam.mkv"
            hd_fixture(source)
            result = engine.scan(source, max_gap=12, interlaced="tff")
            self.assertEqual(result.source_timecode, "10:00:00:00")
            gap = result.gaps[0]
            clip = review.render_event(result, gap, root, "hold", "mxf")
            self.assertEqual(clip["clip_timecode"], "10:00:00:00")
            with patch("engine.Renderer.render", side_effect=AssertionError("Reuse completed patch")):
                output, _ = engine.repair(result, root, "hold", export="mxf", artifacts=review.artifacts(result, root))
            for name in (clip["clip"], output):
                data = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", name]))
                video = data["streams"][0]
                self.assertEqual((video["codec_name"], video["pix_fmt"], video["field_order"]), ("mpeg2video", "yuv422p", "tt"))
                self.assertEqual(int(video["bit_rate"]), 50000000)
                self.assertEqual(data["format"]["tags"]["timecode"], "10:00:00:00")
            with av.open(output) as container:
                frames = list(container.decode(video=0))
            self.assertEqual(len(frames), 50)
            self.assertEqual([f.pts * f.time_base for f in frames], result.timestamps)
            self.assertGreater(frames[19].to_ndarray(format="gray").mean(), 40)
            def samples(path):
                with av.open(str(path)) as container:
                    return np.concatenate([f.to_ndarray().ravel() for f in container.decode(audio=0)])
            np.testing.assert_array_equal(samples(source), samples(output))
            viewed = review.preview_artifact(result, gap, clip)
            self.assertEqual(len(viewed.after), len(viewed.before))

    def test_mxf_rejects_unsupported_dimensions_and_variable_frame_rate(self):
        from test_engine import fixture
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "small.mov"
            fixture(source, variable=True)
            result = engine.scan(source)
            with self.assertRaisesRegex(ValueError, "1920"):
                engine.validate_mxf(result, 160, 96)
            with self.assertRaisesRegex(ValueError, "konstante"):
                engine.validate_mxf(result, 1920, 1080)


if __name__ == "__main__":
    unittest.main()
