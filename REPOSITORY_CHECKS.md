# Prüfung des Repository-Imports – 17.10.2026

Der bereitgestellte V8-Quellstand wurde unter Linux/Python 3.12 mit PyAV 16.1,
PySide6 6.10, NumPy 1.26.4 und PyTorch 2.2.2 CPU geprüft:

- `python -m unittest discover -s tests -v`: **72 Tests bestanden**, 65,868 Sekunden.
- Python-Syntax und Bash-Syntax der beiden Start-/Installationsskripte geprüft.
- ZIP-CRC und SHA-256 von Modell und Downloadpaket geprüft.
- Reparaturmodule, Modell, Bilder und ursprüngliche Paketdateien mit dem Anhang
  verglichen. Der Download ergänzt Projektlizenz und Drittanbieterhinweise.
- Relative Dokumentationslinks geprüft.

Im Git-Checkout ergänzt der Installer einen Aufruf des Vorbereitungsskripts.
Dieses setzt das Paket aus drei Binärteilen zusammen, prüft seine SHA-256-Summe
und extrahiert ausschließlich das ebenfalls geprüfte RIFE-Modell. Der
vollständige Download enthält das Modell bereits und benötigt diesen Schritt nicht.

Die 72 Tests betreffen den importierten Softwarestand. Der GitHub-Workflow wird
zusätzlich auf GitHub ausgeführt. macOS-Build, Gatekeeper, MPS und die native
XDCAM-Quelle wurden bei diesem Import nicht getestet.
