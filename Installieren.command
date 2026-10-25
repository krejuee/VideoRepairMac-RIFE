#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
finish() {
    status=$?
    if [ "$status" -ne 0 ]; then
        echo ""
        echo "Installation fehlgeschlagen. Bitte die Fehlermeldung oben beachten."
        read -r -p "Enter zum Schließen …" answer || true
    fi
}
trap finish EXIT
if [ "$(uname -s)" != "Darwin" ]; then
    echo "Dieser Installer ist für macOS bestimmt."
    exit 1
fi
if [ "$(sw_vers -productVersion | cut -d. -f1)" -lt 14 ]; then
    echo "Video Repair RIFE benötigt macOS 14 oder neuer."
    exit 1
fi
echo "Video Repair – Installation"
echo "Die benötigten Pakete werden einmalig heruntergeladen."
PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert (3,11) <= sys.version_info[:2] <= (3,12)' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "Bitte zuerst Python 3.12 für macOS von https://www.python.org/downloads/macos/ installieren."
    echo "Danach Installieren.command erneut öffnen."
    exit 1
fi
if [ -f scripts/prepare_package.py ]; then
    "$PYTHON" scripts/prepare_package.py
fi
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
.venv/bin/python -c 'from rife_backend import Rife; Rife(); print("RIFE-Modell geprüft.")'
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --onedir \
    --name "Video Repair RIFE 9" --osx-bundle-identifier "local.videorepair.rife9.mac" \
    --add-data "models:models" --add-data "vendor_rife/LICENSE:licenses" \
    --collect-all av --collect-all cv2 --collect-all torch --exclude-module PySide6.QtWebEngineCore \
    --exclude-module PySide6.QtWebEngineWidgets app.py
DEST="$HOME/Applications"
mkdir -p "$DEST"
APP="$DEST/Video Repair RIFE 9.app"
if [ -e "$APP" ]; then
    APP="$DEST/Video Repair RIFE 9 $(date +%Y%m%d-%H%M%S).app"
fi
ditto "dist/Video Repair RIFE 9.app" "$APP"
echo "Fertig: $APP"
open "$APP"
