"""Reassemble the verified V8 distribution and extract its RIFE model."""
from pathlib import Path
import hashlib
import os
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    checksums = dict(line.split(maxsplit=1)[::-1]
                     for line in (root / "SHA256SUMS").read_text().splitlines())
    archive = root / "VideoRepairMac-RIFE-v8.zip"
    expected = checksums[archive.name]
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        parts = [root / "distribution" / (archive.name + f".part{i:02}")
                 for i in range(1, 4)]
        temporary = archive.with_suffix(".zip.tmp")
        try:
            with temporary.open("wb") as output:
                for part in parts:
                    output.write(part.read_bytes())
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                raise RuntimeError("V8 archive checksum mismatch")
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
    model_path = "models/rife425/flownet.pkl"
    model = root / model_path
    expected_model = checksums[model_path]
    if not model.is_file() or hashlib.sha256(model.read_bytes()).hexdigest() != expected_model:
        with zipfile.ZipFile(archive) as package:
            data = package.read("VideoRepairMac/" + model_path)
        if hashlib.sha256(data).hexdigest() != expected_model:
            raise RuntimeError("RIFE model checksum mismatch")
        model.parent.mkdir(parents=True, exist_ok=True)
        temporary = model.with_suffix(".pkl.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, model)
        finally:
            temporary.unlink(missing_ok=True)
    print("V8 archive and RIFE model verified.")


if __name__ == "__main__":
    main()
