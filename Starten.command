#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "Bitte zuerst Installieren.command öffnen."
    read -r -p "Enter zum Schließen …" answer
    exit 1
fi
.venv/bin/python app.py "$@"
