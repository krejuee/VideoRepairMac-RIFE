import json
import sys
import tempfile
import unittest
from pathlib import Path
import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
from rife_backend import Rife
from test_engine import fixture


class RifeFieldTests(unittest.TestCase):
    def test_native_chroma_fields_are_not_blended(self):
        frame = av.VideoFrame(160, 96, "yuv420p")
        for index, plane in enumerate(frame.planes):
            rows = np.full((plane.height, plane.line_size), 100, np.uint8)
            if index == 1:
                rows[0::2], rows[1::2] = 84, 220
            elif index == 2:
                rows[0::2], rows[1::2] = 220, 84
            plane.update(rows.tobytes())
        fields = engine.split_native_fields(frame)
        self.assertGreater(fields[0][..., 0].mean(), fields[0][..., 2].mean() + 100)
        self.assertGreater(fields[1][..., 2].mean(), fields[1][..., 0].mean() + 100)
        rgb = np.empty((96, 160, 3), np.uint8)
        rgb[0::2], rgb[1::2] = fields
        woven = engine.weave_native_fields(rgb, "yuv420p", 2, 1)
        again = engine.split_native_fields(woven)
        self.assertGreater(again[0][..., 0].mean(), again[0][..., 2].mean() + 100)
        self.assertGreater(again[1][..., 2].mean(), again[1][..., 0].mean() + 100)

    def test_real_rife_quality_mode(self):
        r = Rife()
        rng = np.random.default_rng(17)
        left = rng.integers(40, 190, (48, 160, 3), dtype=np.uint8)
        right = np.roll(left, 4, axis=1)
        output = r.interpolate(left, right, [.5], high_quality=True)
        self.assertEqual(output[0].shape, left.shape)
        self.assertLess(np.abs(output[0][:, 10:-10].astype(float) - np.roll(left, 2, axis=1)[:, 10:-10]).mean(), 15)
        self.assertEqual(r.detail["augmentation_passes"], 16)

    def test_real_rife_interlaced_lossless_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.mov"
            fixture(path)
            scan = engine.scan(path, interlaced="tff")
            output, report = engine.repair(scan, directory, mode="rife", export="lossless")
            with av.open(str(path)) as before, av.open(output) as after:
                for i, (a, b) in enumerate(zip(before.decode(video=0), after.decode(video=0))):
                    if not any(g.approved and g.start <= i <= g.end for g in scan.gaps):
                        self.assertTrue(engine.same_pixels(a, b))
                    else:
                        self.assertGreater(b.to_ndarray(format="gray").mean(), 50)
            quality = json.loads(Path(report).read_text().split("\n", 1)[1])["quality"]
            self.assertTrue(quality["pixel_verified"])
            self.assertEqual(quality["field_order"], "tff")
            self.assertEqual(quality["inference"]["model"], "Practical-RIFE 4.25")


if __name__ == "__main__":
    unittest.main()
