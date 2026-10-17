import hashlib
import errno
import sys
import tempfile
import unittest
from unittest.mock import patch
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


def fixture(path, variable=False, delayed_audio=False):
    with av.open(str(path), "w") as out:
        v = out.add_stream("libx264", rate=25)
        v.width, v.height, v.pix_fmt = 160, 96, "yuv420p"
        v.codec_context.time_base = Fraction(1, 1000)
        v.options = {"crf": "12"}
        a = out.add_stream("aac", rate=48000)
        a.layout = "mono"
        x, y = np.meshgrid(np.arange(160), np.arange(96))
        for i in range(150):
            img = np.zeros((96, 160, 3), np.uint8)
            if i < 114:
                img[..., 0] = 80 + x // 2
                img[..., 1] = 90 + y
                img[..., 2] = 80 + y // 2
                # modest moving feature
                pos = 20 + i % 40
                img[25:45, pos:pos + 20] = 210
            else:
                img[...] = 245
                img[::3] = 45
            if 25 <= i <= 28 or 70 <= i <= 78 or 110 <= i <= 113:
                img[...] = 0
            f = av.VideoFrame.from_ndarray(img, format="rgb24")
            f.pts, f.time_base = i * 40 + (i // 30 * 10 if variable else 0), Fraction(1, 1000)
            out.mux(v.encode(f))
        out.mux(v.encode(None))
        for start in range(0, 288000, 1024):
            samples = (0.1 * np.sin(2 * np.pi * 440 * np.arange(start, min(start + 1024, 288000)) / 48000)).astype(np.float32)
            f = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
            f.sample_rate, f.time_base = 48000, Fraction(1, 48000)
            f.pts = start + (9600 if delayed_audio else 0)
            out.mux(a.encode(f))
        out.mux(a.encode(None))


def video_info(path):
    with av.open(str(path)) as c:
        frames = list(c.decode(video=0))
        return [f.pts * f.time_base for f in frames], [f.to_ndarray(format="gray").mean() for f in frames]


def audio_info(path):
    with av.open(str(path)) as c:
        return [(p.pts * p.time_base if p.pts is not None else None,
                 p.dts * p.time_base if p.dts is not None else None,
                 hashlib.sha256(bytes(p)).hexdigest()) for p in c.demux(audio=0) if p.size]


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "test original.mov"
        fixture(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def test_detection_and_scene_safety(self):
        result = engine.scan(self.source)
        self.assertEqual([(g.start, g.length, g.approved) for g in result.gaps],
                         [(25, 4, True), (70, 9, False), (110, 4, True)])

    def test_repair_preserves_frames_timestamps_and_audio(self):
        before_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = engine.scan(self.source)
        output, report = engine.repair(result, self.root)
        before_times, _ = video_info(self.source)
        after_times, means = video_info(output)
        self.assertEqual(before_times, after_times)
        self.assertTrue(all(means[i] > 50 for i in range(25, 29)))
        self.assertTrue(all(means[i] < 3 for i in range(70, 79)))
        self.assertTrue(all(means[i] > 50 for i in range(110, 114)))
        self.assertEqual(audio_info(self.source), audio_info(output))
        self.assertEqual(before_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertIn('"action": "repariert"', Path(report).read_text())
        second, _ = engine.repair(result, self.root, mode="hold")
        self.assertNotEqual(output, second)

    def test_variable_timing_and_delayed_audio(self):
        source = self.root / "variable.mov"
        fixture(source, variable=True, delayed_audio=True)
        result = engine.scan(source)
        output, _ = engine.repair(result, self.root)
        self.assertEqual(video_info(source)[0], video_info(output)[0])
        self.assertEqual(audio_info(source), audio_info(output))

    def test_cancel_cleans_partial_output(self):
        result = engine.scan(self.source)
        with self.assertRaises(engine.Cancelled):
            engine.repair(result, self.root, cancel=lambda: True)
        self.assertFalse(list(self.root.glob(".video-repair-*")))
        self.assertFalse(list(self.root.glob("*_REPAIRED.mov")))

    def test_changed_source_rejected(self):
        result = engine.scan(self.source)
        with self.source.open("ab") as f:
            f.write(b"changed")
        with self.assertRaises(ValueError):
            engine.repair(result, self.root)

    def test_export_on_filesystem_without_hardlinks(self):
        result = engine.scan(self.source)
        with patch("engine.os.link", side_effect=OSError(errno.ENOTSUP, "No hardlinks")):
            output, _ = engine.repair(result, self.root)
        self.assertEqual(video_info(self.source)[0], video_info(output)[0])
        self.assertEqual(audio_info(self.source), audio_info(output))
        self.assertFalse(list(self.root.glob(".video-repair-*")))

    def test_boundary_black_runs_not_approved(self):
        thumbs = [np.zeros((27, 48), np.uint8), np.full((27, 48), 120, np.uint8)]
        self.assertFalse(engine.classify(engine.Gap(0, 0, 0), thumbs, [0, 120])[0])
        self.assertFalse(engine.classify(engine.Gap(1, 1, .04), thumbs, [120, 0])[0])

    def test_interpolation_uses_motion_and_keeps_dimensions(self):
        rng = np.random.default_rng(8)
        left = rng.integers(50, 200, (96, 160, 3), dtype=np.uint8)
        right = np.roll(left, 4, axis=1)
        images = engine.interpolate(left, right, 3)
        self.assertEqual(len(images), 3)
        self.assertTrue(all(x.shape == left.shape for x in images))
        self.assertLess(np.mean(np.abs(images[1][:, 10:-10].astype(float) -
                                      np.roll(left, 2, axis=1)[:, 10:-10].astype(float))), 25)


if __name__ == "__main__":
    unittest.main()
