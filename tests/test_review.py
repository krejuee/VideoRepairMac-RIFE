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
from test_engine import fixture, audio_info
from test_options import title_fixture


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_slider_detects_previously_absent_title_gaps_without_opening_video(self):
        source = self.root / "titles.mov"
        title_fixture(source)
        scan = engine.scan(source, max_gap=12, sensitivity=0)
        self.assertEqual([g for g in scan.gaps if g.length], [])
        with patch("engine.av.open", side_effect=AssertionError("No rescan allowed")):
            engine.refresh_scan(scan, sensitivity=50)
            self.assertEqual([(g.start, g.length, g.approved) for g in scan.gaps], [(15, 12, True)])
            engine.set_gap_decision(scan, scan.gaps[0], "skip")
            engine.refresh_scan(scan, sensitivity=80)
            self.assertEqual(scan.gaps[0].decision, "skip")
            engine.refresh_scan(scan, sensitivity=0)
            self.assertEqual([g for g in scan.gaps if g.length], [])
            engine.refresh_scan(scan, sensitivity=50)
            self.assertEqual(scan.gaps[0].decision, "skip")

    def test_per_event_override_preview_export_and_preservation(self):
        source = self.root / "source.mov"
        fixture(source)
        scan = engine.scan(source, max_gap=12)
        engine.set_gap_decision(scan, scan.gaps[0], "skip")
        gap = scan.gaps[-1]
        self.assertTrue(gap.approved)
        engine.set_gap_decision(scan, gap, "repair")
        gap.mode = "hold"
        planned = engine.preview(scan, gap, "flow")
        self.assertLess(planned.before[planned.selected].mean(), 4)
        self.assertGreater(planned.after[planned.selected].mean(), 60)
        output, report = engine.repair(scan, self.root, "flow", export="lossless", control_clips=True)
        actual = engine.preview(scan, gap, "flow", rendered=output)
        self.assertTrue(np.array_equal(planned.after[planned.selected], actual.after[actual.selected]))
        with av.open(output) as out:
            frames = list(out.decode(video=0))
        self.assertLess(frames[25].to_ndarray(format="gray").mean(), 4)
        self.assertGreater(frames[110].to_ndarray(format="gray").mean(), 60)
        before_audio, after_audio = audio_info(source), audio_info(output)
        self.assertEqual([p[2] for p in before_audio], [p[2] for p in after_audio])
        self.assertTrue(all(abs(a[0] - b[0]) <= Fraction(1, 1000) for a, b in zip(before_audio, after_audio)))
        report_data = json.loads(Path(report).read_text().split("\n", 1)[1])
        directory = Path(report_data["quality"]["control_clips"])
        entries = json.loads((directory / "INDEX.json").read_text())
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[-1]["decision"], "repair")
        entry = entries[-1]
        with av.open(str(directory / entry["result"])) as clip:
            clip_frames = list(clip.decode(video=0))
            self.assertFalse(clip.streams.audio)
        self.assertTrue(engine.same_pixels(clip_frames[110 - entry["start_frame"]], frames[110]))
        with patch("engine.av.open", side_effect=AssertionError("No rescan")):
            engine.refresh_scan(scan, sensitivity=50)
            self.assertEqual(scan.gaps[-1].decision, "repair")
            self.assertEqual(scan.gaps[-1].mode, "hold")

    def test_manual_override_keeps_boundary_and_length_limits(self):
        source = self.root / "source.mov"
        fixture(source)
        scan = engine.scan(source, max_gap=6)
        with self.assertRaises(ValueError):
            engine.set_gap_decision(scan, scan.gaps[1], "repair")
        with self.assertRaises(ValueError):
            engine.set_gap_decision(scan, engine.Gap(0, 1, 0), "repair")
        engine.set_gap_decision(scan, scan.gaps[-1], "repair")
        engine.set_gap_decision(scan, scan.gaps[-1], "auto")
        self.assertTrue(scan.gaps[-1].approved)

    def test_vfr_preview_and_control_clip_times(self):
        source = self.root / "vfr.mov"
        fixture(source, variable=True)
        scan = engine.scan(source)
        planned = engine.preview(scan, scan.gaps[0], "blend")
        self.assertTrue(all(b > a for a, b in zip(planned.times, planned.times[1:])))
        output, _ = engine.repair(scan, self.root, "blend", control_clips=True)
        start, end = engine.clip_bounds(scan, scan.gaps[0])
        clip = next(Path(str(Path(output).with_suffix("")) + "_KONTROLLE").glob("001*ergebnis.mov"))
        with av.open(str(clip)) as out:
            times = [f.pts * f.time_base for f in out.decode(video=0)]
        self.assertEqual(times, [t - scan.timestamps[start] for t in scan.timestamps[start:end + 1]])

    def test_gentle_chroma_adjustment_is_bounded_and_does_not_grade_cuts(self):
        a = np.full((96, 160, 3), [120, 120, 120], np.uint8)
        tinted = np.full_like(a, [124, 120, 120])
        corrected = engine.gentle_color_match(tinted, a, a, .5)
        self.assertLess(corrected[..., 0].mean() - corrected[..., 1].mean(), 4)
        self.assertLessEqual(np.abs(corrected.astype(int) - tinted).max(), 2)
        b = np.full_like(a, [180, 80, 90])
        self.assertTrue(np.array_equal(engine.gentle_color_match(tinted, a, b, .5), tinted))

    def test_control_clip_cancel_removes_partial_directory(self):
        source = self.root / "source.mov"
        fixture(source)
        scan = engine.scan(source)
        output, _ = engine.repair(scan, self.root, "hold")
        with self.assertRaises(engine.Cancelled):
            engine.export_control_clips(scan, output, cancel=lambda: True)
        self.assertFalse(Path(str(Path(output).with_suffix("")) + "_KONTROLLE").exists())


def colour_fixture(path, order, value_range, fmt="yuv420p", partial=False):
    with av.open(str(path), "w") as out:
        stream = out.add_stream("ffv1", rate=25)
        stream.width, stream.height, stream.pix_fmt = 160, 96, fmt
        stream.options = {"level": "3", "field_order": "tt" if order == "tff" else "bb"}
        stream.codec_context.colorspace = 1  # BT.709
        stream.codec_context.color_range = value_range
        rgb = np.empty((96, 160, 3), np.uint8)
        rgb[0::2] = [160, 100, 80]
        rgb[1::2] = [80, 100, 160]
        for i in range(16):
            pixels = np.zeros_like(rgb) if 6 <= i <= 8 else rgb
            if partial and i == 5:
                pixels = rgb.copy(); pixels[1::2] = 0
            frame = engine.weave_native_fields(pixels, fmt, 1, value_range)
            frame.pts, frame.time_base = i, Fraction(1, 25)
            out.mux(stream.encode(frame))
        out.mux(stream.encode(None))


class ColourFieldTests(unittest.TestCase):
    def test_422_tff_partial_field_keeps_good_native_field(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fields422.mkv"
            colour_fixture(path, "tff", 1, fmt="yuv422p", partial=True)
            scan = engine.scan(path, interlaced="tff", force_cpu=True, scene_guard=False)
            self.assertEqual([(g.start, g.end) for g in scan.gaps], [(5, 8)])
            output, _ = engine.repair(scan, temp, "rife", export="lossless")
            with av.open(str(path)) as src, av.open(output) as dst:
                before, after = list(src.decode(video=0)), list(dst.decode(video=0))
            for a, b in zip(before[5].planes, after[5].planes):
                a = np.frombuffer(a, np.uint8).reshape(a.height, a.line_size)[:, :a.width]
                b = np.frombuffer(b, np.uint8).reshape(b.height, b.line_size)[:, :b.width]
                self.assertTrue(np.array_equal(a[0::2], b[0::2]))
            for f in after[5:9]:
                fields = engine.split_native_fields(f)
                self.assertGreater(fields[0][..., 0].mean() - fields[0][..., 2].mean(), 50)
                self.assertGreater(fields[1][..., 2].mean() - fields[1][..., 0].mean(), 50)

    def test_709_rgb_roundtrip_has_no_systematic_dimming(self):
        frame = av.VideoFrame(160, 96, "yuv422p")
        frame.colorspace, frame.color_range = 1, 1
        for plane, value in zip(frame.planes, (100, 145, 160)):
            plane.update(np.full((plane.height, plane.line_size), value, np.uint8).tobytes())
        frame.pts, frame.time_base = 0, Fraction(1, 25)
        for order in ("off", "tff", "bff"):
            rgb = engine.frame_rgb(frame, order)
            replacement = engine.replacement_frame(frame, rgb, "yuv422p", order)
            again = engine.frame_rgb(replacement, order)
            self.assertLess(np.abs(rgb.astype(float) - again).mean(), .6)

    def test_real_rife_fast_hq_tff_bff_and_709_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for order, value_range, mode in (("tff", 1, "rife"), ("tff", 2, "rife_hq"),
                                           ("bff", 1, "rife_hq"), ("bff", 2, "rife")):
                with self.subTest(order=order, value_range=value_range, mode=mode):
                    source = root / f"{order}_{value_range}.mkv"
                    colour_fixture(source, order, value_range)
                    scan = engine.scan(source, interlaced=order, scene_guard=False, force_cpu=True)
                    self.assertEqual([(g.start, g.end) for g in scan.gaps], [(6, 8)])
                    planned = engine.preview(scan, scan.gaps[0], mode)
                    output, _ = engine.repair(scan, root, mode, export="lossless")
                    with av.open(str(source)) as src, av.open(output) as dst:
                        original = list(src.decode(video=0))
                        final = list(dst.decode(video=0))
                    fields = engine.split_native_fields(original[5])
                    for i in range(6, 9):
                        got = engine.split_native_fields(final[i])
                        for parity in (0, 1):
                            self.assertGreater(got[parity].mean(), 60)
                            self.assertLess(np.abs(got[parity].astype(float) - fields[parity]).mean(), 3)
                    for i in list(range(6)) + list(range(9, 16)):
                        self.assertTrue(engine.same_pixels(original[i], final[i]))
                    self.assertGreater(np.ptp(planned.after[planned.selected].mean(axis=(0, 1))), 20)


if __name__ == "__main__":
    unittest.main()
