import sys
import tempfile
import unittest
from pathlib import Path
from fractions import Fraction
from unittest.mock import patch
import av
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


def cut_fixture(path):
    with av.open(str(path), "w") as out:
        stream = out.add_stream("ffv1", rate=25)
        stream.width, stream.height, stream.pix_fmt = 160, 96, "yuv444p"
        for index in range(90):
            image = np.full((96, 160, 3), [90, 120, 140] if index < 20 else [210, 190, 120], np.uint8)
            if index >= 33:
                image[...] = [65, 90, 160]
            if 30 <= index < 33 or 55 <= index < 59:
                image[...] = 0
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 25)
            out.mux(stream.encode(frame))
        out.mux(stream.encode(None))


class CutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "cuts.mkv"
        cut_fixture(self.source)
        self.result = engine.scan(self.source, max_gap=12)

    def tearDown(self):
        self.temp.cleanup()

    def test_normal_cut_cut_with_gap_and_regular_gap_have_distinct_types(self):
        self.assertEqual([(g.start, g.end, g.kind, g.approved) for g in self.result.gaps],
            [(20, 19, "cut", False), (30, 32, "cut_gap", True), (55, 58, "gap", True)])
        with patch("engine.av.open", side_effect=AssertionError("No video rescan")):
            engine.refresh_scan(self.result, sensitivity=100)
        self.assertEqual([g.kind for g in self.result.gaps], ["cut", "cut_gap", "gap"])

    def test_strong_change_is_cut_even_if_scene_guard_disabled(self):
        engine.refresh_scan(self.result, scene_guard=False)
        self.assertTrue(self.result.gaps[1].approved)
        self.assertEqual(self.result.gaps[1].kind, "cut_gap")

    def test_default_export_preserves_cut_and_repairs_gap_at_cut(self):
        output, _ = engine.repair(self.result, self.root, "hold", export="lossless")
        with av.open(str(self.source)) as src, av.open(output) as dst:
            before, after = list(src.decode(video=0)), list(dst.decode(video=0))
        for i in (19, 20, 29, 33):
            self.assertTrue(engine.same_pixels(before[i], after[i]))
        self.assertGreater(after[56].to_ndarray(format="gray").mean(), 40)

    def test_cut_gap_can_be_retyped_and_decisions_survive_list_refresh(self):
        gap = self.result.gaps[1]
        engine.set_event_type(self.result, gap, "gap")
        self.assertTrue(gap.approved)
        gap.mode = "blend"
        with patch("engine.av.open", side_effect=AssertionError("No rescan")):
            engine.refresh_scan(self.result, sensitivity=70)
        gap = self.result.gaps[1]
        self.assertEqual((gap.kind, gap.effective_kind, gap.mode, gap.approved), ("cut_gap", "gap", "blend", True))
        engine.set_event_type(self.result, gap, "fade")
        self.assertFalse(gap.approved)
        engine.set_event_type(self.result, self.result.gaps[0], "gap")
        self.assertTrue(self.result.gaps[0].approved)

    def test_original_preview_never_starts_inference(self):
        gap = self.result.gaps[-1]
        with patch("engine.Renderer.render", side_effect=AssertionError("Unexpected inference")):
            original = engine.preview(self.result, gap, "rife_hq", original_only=True)
        self.assertEqual(original.comparison, "original")
        self.assertTrue(all(np.array_equal(a, b) for a, b in zip(original.before, original.after)))
        cut = engine.preview(self.result, self.result.gaps[0], "rife_hq", original_only=True)
        self.assertIn("Schnitt", cut.labels[cut.selected])

    def test_cut_gap_motion_default_does_not_morph_scenes(self):
        gap = self.result.gaps[1]
        engine.set_gap_decision(self.result, gap, "repair")
        self.result.gaps[-1].mode = "hold"
        with patch("rife_backend.Rife", side_effect=AssertionError("Cut must not interpolate")):
            output, _ = engine.repair(self.result, self.root, "rife_hq", export="lossless")
        with av.open(str(self.source)) as src, av.open(output) as dst:
            before, after = list(src.decode(video=0)), list(dst.decode(video=0))
        for index, reference in ((30, 29), (31, 29), (32, 29)):
            self.assertLess(np.abs(engine.frame_rgb(after[index]).astype(float) - engine.frame_rgb(before[reference])).mean(), 2)


if __name__ == "__main__":
    unittest.main()
