"""Package the current V9 source and verified model, excluding caches and old ZIPs."""
from pathlib import Path
import hashlib
import zipfile
from prepare_package import main as prepare_model


def main():
    prepare_model()
    root = Path(__file__).resolve().parents[1]
    files = set(root.glob('*.py')) | set(root.glob('*.md')) | set(root.glob('*.command'))
    files |= {root / 'LICENSE', root / 'requirements.txt'}
    for directory in ('tests', 'vendor_rife', 'models', 'Bilder'):
        files.update(p for p in (root / directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.tmp'))
    archive = root / 'VideoRepairMac-RIFE-v9.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        for file in sorted(files):
            package.write(file, str(Path('VideoRepairMac') / file.relative_to(root)))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (root / 'SHA256SUMS-v9').write_text(f'{digest}  {archive.name}\n')
    print(f'Packaged {archive.name}: {len(files)} files, SHA-256 {digest}')


if __name__ == '__main__':
    main()
