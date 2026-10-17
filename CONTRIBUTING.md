# Beiträge und Fehlerberichte

## Fehler melden

Bitte ein [GitHub-Issue](https://github.com/krejuee/VideoRepairMac-RIFE/issues)
mit folgenden Angaben erstellen:

- App-Version, macOS-Version und Intel-/Apple-Silicon-Modell.
- Quellformat, Auflösung, Bildrate, Bit-Tiefe und progressiv/TFF/BFF.
- Gewählte Erkennung, Ereignistyp, Methode und Exportprofil.
- Schritte zum Nachstellen, erwartetes und tatsächliches Ergebnis.
- Fehlermeldung, relevante Berichtsauszüge und gegebenenfalls ein kurzer Clip,
  der öffentlich geteilt werden darf. Berichte vor Upload auf persönliche Pfade
  und vertrauliche Metadaten prüfen.

## Änderungen prüfen

Python 3.11/3.12 und `requirements.txt` verwenden. Nach einem Git-Checkout einmal
`python scripts/prepare_package.py` ausführen, um das Modell bereitzustellen. Für Metadatentests muss
`ffprobe` installiert sein. Vom Hauptordner aus:

```bash
python -m unittest discover -s tests -v
```

Unter Linux zusätzlich `QT_QPA_PLATFORM=offscreen` setzen. Die Tests enthalten
echte CPU-RIFE-Inferenz und Videoexporte und benötigen entsprechend Laufzeit.
Tests auf Linux ersetzen keine Prüfung des Mac-Installers oder der MPS-Ausführung.

Änderungen möglichst mit reproduzierbarem Beispiel und passenden Tests als
Pull Request einreichen. Originale Lizenz- und Herkunftshinweise beibehalten.
Neue Abhängigkeiten und Änderungen an unterstützten Formaten dokumentieren.
