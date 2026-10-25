import json
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from fractions import Fraction

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
from test_engine import audio_info, fixture


def title_fixture(path, length=12, only_gap=False):
    with av.open(str(path), "w") as out:
        s = out.add_stream("libx264", rate=25)
        s.width, s.height, s.pix_fmt = 160, 96, "yuv420p"
        s.options = {"crf": "12"}
        for i in range(50):
            image = np.full((96, 160, 3), 110, np.uint8)
            image[20:40, 30:50] = 170
            if 15 <= i < 15 + length:
                image[...] = 0
            if not only_gap or 15 <= i < 15 + length:
                image[70:80, 30:130] = 255  # title occupies about 6.5 percent
            f = av.VideoFrame.from_ndarray(image, format="rgb24")
            f.pts, f.time_base = i, Fraction(1, 25)
            out.mux(s.encode(f))
        out.mux(s.encode(None))


class OptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_title_sensitivity_and_twelve_frames(self):
        source = self.root / "title.mov"
        title_fixture(source)
        self.assertEqual([g for g in engine.scan(source, max_gap=12, sensitivity=0).gaps if g.length], [])
        scan = engine.scan(source, max_gap=12, sensitivity=50)
        self.assertEqual([(g.length, g.approved) for g in scan.gaps], [(12, True)])
        self.assertFalse(engine.scan(source, max_gap=6, sensitivity=50).gaps[0].approved)
        output, report = engine.repair(scan, self.root, "hold", export="lossless")
        with av.open(output) as c:
            images = [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]
        self.assertEqual(len(images), 50)
        self.assertGreater(images[20][0:50].mean(), 90)
        self.assertGreater(images[20][72:78, 40:120].mean(), 245)
        data = json.loads(Path(report).read_text().split("\n", 1)[1])
        self.assertEqual(data["detection"]["sensitivity"], 50)
        self.assertEqual(data["detection"]["max_gap"], 12)

    def test_scene_guard_can_be_disabled_explicitly(self):
        source = self.root / "scenes.mov"
        fixture(source)
        guarded = engine.scan(source, max_gap=12)
        unguarded = engine.scan(source, max_gap=12, scene_guard=False)
        self.assertTrue(guarded.gaps[-1].approved)
        self.assertEqual(unguarded.gaps[-1].kind, "cut_gap")
        self.assertTrue(unguarded.gaps[-1].approved)

    def test_single_damaged_field_is_detected(self):
        with av.open(str(self.root / "onefield.mkv"), "w") as out:
            s=out.add_stream("ffv1",rate=25);s.width=160;s.height=96;s.pix_fmt="yuv444p"
            for i in range(5):
                rgb=np.full((96,160,3),120,np.uint8)
                if i==2: rgb[0::2]=0
                f=av.VideoFrame.from_ndarray(rgb,format="rgb24");f.pts=i
                out.mux(s.encode(f))
            out.mux(s.encode(None))
        scan=engine.scan(self.root / "onefield.mkv",sensitivity=100,interlaced="tff")
        self.assertEqual([(g.start, g.end, g.approved) for g in scan.gaps],[(2,2,True)])

    def test_field_repair_never_mix_parities(self):
        left=np.empty((96,160,3),np.uint8);right=np.empty_like(left)
        left[0::2]=[200,10,10];left[1::2]=[10,10,200]
        right[0::2]=[160,10,10];right[1::2]=[10,10,160]
        generate=lambda a,b,positions: [np.rint(a*(1-t)+b*t).astype(np.uint8) for t in positions]
        for order in ("tff","bff"):
            image=engine.reconstruct(left,right,[.5],generate,order)[0]
            self.assertTrue(np.all(image[0::2]==[180,10,10]))
            self.assertTrue(np.all(image[1::2]==[10,10,180]))

    def test_interlaced_export_metadata_and_audio(self):
        source=self.root / "original.mov";fixture(source)
        for order in ("tff","bff"):
            scan=engine.scan(source,interlaced=order)
            output,_=engine.repair(scan,self.root,"hold",export="compatible")
            info=json.loads(subprocess.check_output(["ffprobe","-v","error","-select_streams","v:0",
                 "-show_entries","stream=field_order","-of","json",output]))
            self.assertEqual(info["streams"][0]["field_order"],"tt" if order=="tff" else "bb")
            self.assertEqual(audio_info(source),audio_info(output))
            with av.open(output) as c:
                self.assertEqual(sum(1 for _ in c.decode(video=0)),scan.count)


if __name__ == "__main__":
    unittest.main()
