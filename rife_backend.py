"""Full-resolution Float32 inference with official Practical-RIFE 4.25 weights."""
import os
import hashlib
from pathlib import Path
import numpy as np

MODEL_SHA256 = "6615790efd627772917205db291f51cd392528a157ecbb2ecaeec3bff8eb6de2"


class Rife:
    def __init__(self, status=lambda text: None, force_cpu=False):
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        import torch
        from vendor_rife.ifnet import IFNet
        self.torch, self.status = torch, status
        path = Path(__file__).parent / "models/rife425/flownet.pkl"
        status("Lade und prüfe RIFE 4.25 …")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise RuntimeError("RIFE-Modell fehlt oder ist beschädigt. Bitte vollständiges Paket installieren.")
        state = torch.load(path, map_location="cpu", weights_only=True)
        state = {k.removeprefix("module."): v for k, v in state.items()}
        self.net = IFNet()
        expected = set(self.net.state_dict())
        self.net.load_state_dict({k: v for k, v in state.items() if k in expected}, strict=True)
        self.device = torch.device("mps" if torch.backends.mps.is_available() and not force_cpu else "cpu")
        torch.set_num_threads(min(8, os.cpu_count() or 1))
        self.net.eval().to(self.device)
        self.detail = {"model": "Practical-RIFE 4.25", "precision": "float32",
                       "device": self.device.type, "weights_sha256": MODEL_SHA256}

    def use_cpu(self, reason):
        self.device = self.torch.device("cpu")
        self.net.to(self.device)
        from vendor_rife.warplayer import backwarp_tenGrid
        backwarp_tenGrid.clear()
        self.detail["device"] = "cpu"
        self.detail["fallback_reason"] = reason
        self.status("RIFE rechnet auf CPU weiter: " + reason)

    def predict(self, a, b, alpha):
        torch = self.torch
        h, w = a.shape[-2:]
        pad = (0, (-w) % 128, 0, (-h) % 128)
        a = torch.nn.functional.pad(a, pad, mode="replicate").to(self.device)
        b = torch.nn.functional.pad(b, pad, mode="replicate").to(self.device)
        try:
            _, _, merged = self.net(torch.cat((a, b), 1), timestep=alpha,
                                    scale_list=[16, 8, 4, 2, 1], fastmode=True, ensemble=False)
        except RuntimeError:
            if self.device.type != "mps":
                raise
            self.use_cpu("MPS-Berechnungsfehler")
            _, _, merged = self.net(torch.cat((a.cpu(), b.cpu()), 1), timestep=alpha,
                                    scale_list=[16, 8, 4, 2, 1], fastmode=True, ensemble=False)
        output = merged[-1][..., :h, :w]
        if not torch.isfinite(output).all().item():
            if self.device.type == "mps":
                self.use_cpu("Nicht endliche GPU-Bildwerte")
                return self.predict(a.cpu()[..., :h, :w], b.cpu()[..., :h, :w], alpha)
            raise RuntimeError("RIFE liefert ungültige Bildwerte.")
        return output

    def interpolate(self, left, right, alphas, high_quality=True, cancel=lambda: False, progress=lambda p: None):
        from engine import Cancelled
        torch = self.torch
        maximum = float(np.iinfo(left.dtype).max)
        if left.dtype != right.dtype:
            raise ValueError("Unterschiedliche Bittiefe der Referenzbilder.")
        tensors = [torch.from_numpy(np.ascontiguousarray(x.transpose(2, 0, 1), dtype=np.float32)).unsqueeze(0)
                   .to(self.device, dtype=torch.float32).div_(maximum) for x in (left, right)]
        outputs = []
        self.detail["augmentation_passes"] = 16 if high_quality else 1
        with torch.inference_mode():
            for i, alpha in enumerate(alphas):
                if not 0 < alpha < 1:
                    raise ValueError("Ungültige Zwischenbild-Position.")
                accumulator, count = None, 0
                for k, flip in ([(k, f) for k in range(4) for f in (False, True)] if high_quality else [(0, False)]):
                    a, b = [torch.rot90(x, k, (-2, -1)) for x in tensors]
                    if flip:
                        a, b = a.flip(-1), b.flip(-1)
                    for reverse in ((False, True) if high_quality else (False,)):
                        if cancel():
                            raise Cancelled("Abgebrochen")
                        self.status(f"RIFE · Bild {i + 1}/{len(alphas)} · Variante {count + 1}/{16 if high_quality else 1}")
                        image = self.predict(b, a, 1 - alpha) if reverse else self.predict(a, b, alpha)
                        if flip:
                            image = image.flip(-1)
                        image = torch.rot90(image, -k, (-2, -1))
                        if not torch.isfinite(image).all().item():
                            raise RuntimeError("Ungültige RIFE-Bildwerte.")
                        accumulator = image.clone() if accumulator is None else accumulator.to(image.device).add_(image)
                        count += 1
                        progress((i + count / (16 if high_quality else 1)) / len(alphas))
                output = accumulator.div_(count).clamp_(0, 1).mul_(maximum).round()
                outputs.append(output[0].permute(1, 2, 0).cpu().numpy().astype(left.dtype))
        def suspicious(image):
            means = [float(x.mean()) * 255 / maximum for x in (left, right, image)]
            if min(means[:2]) > 20 and means[2] < min(means[:2]) * .1:
                return True
            def chroma(x):
                x = x[::4, ::4].astype(np.float32)
                return float(np.mean(np.abs(x - x.mean(axis=2, keepdims=True)))) * 255 / maximum
            return min(chroma(left), chroma(right)) > 8 and chroma(image) < .3
        if any(suspicious(image) for image in outputs):
            if self.device.type == "mps":
                self.use_cpu("Unerwartet schwarzes oder farbloses Zwischenbild")
                return self.interpolate(left, right, alphas, high_quality, cancel, progress)
            raise RuntimeError("RIFE lieferte ein unerwartet schwarzes/farbloses Bild. Diese Stelle bitte mit einem anderen Modus prüfen.")
        return outputs
