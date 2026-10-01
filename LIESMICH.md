# Video Repair RIFE – Mac, Version 2

Lokale Desktop-App zur Reparatur kurzer schwarzer Bildlücken in 8-Bit-SDR-Videos.
Für HD und Full HD: kein Hochskalieren, keine Änderung der Bildanzahl.
Das offizielle **Practical-RIFE-4.25-Modell ist im Paket enthalten**.

## Installation

1. ZIP entpacken und den Ordner nach Downloads oder Dokumente verschieben.
2. Falls nötig **Python 3.12** für macOS installieren:
   https://www.python.org/downloads/macos/ (Python 3.11 wird ebenfalls unterstützt).
3. `Installieren.command` doppelklicken. Der Installer lädt die Laufzeitpakete,
   prüft das RIFE-Modell und erstellt `~/Applications/Video Repair RIFE.app`.
4. App öffnen und Video ins Fenster ziehen.

Voraussetzungen: **macOS 14 oder neuer**, Apple Silicon oder Intel, Internet für
die einmalige Installation. Etwa 4 GB Platz für Installation/Build einplanen,
zusätzlich genügend Platz für die Videoausgabe. Die neue App erhält einen eigenen
Namen und überschreibt die alte Version nicht.

Das Paket enthält Installer, Modell und Quellcode. Es ist **kein vorgebautes,
von Apple notarisiertes App-Bundle**. Mac-Build, Gatekeeper und Apple-GPU wurden
in der Linux-Erstellungsumgebung nicht auf einem Mac getestet.

Bei einer macOS-Sperre die angebotene Freigabe unter Systemeinstellungen →
Datenschutz & Sicherheit verwenden. Wenn `.command` nicht ausführbar ist:
im Terminal `chmod +x ` eingeben, die Datei hineinziehen, Enter drücken.
Bei Python-Zertifikatsfehlern `Install Certificates.command` im Python-Ordner
ausführen. `Starten.command` dient nach Installation als Start ohne App-Bundle.

## Qualitätseinstellungen

Die App startet mit **RIFE 4.25 · Qualität** und **verlustfreiem FFV1/MKV-Master**.
Das Qualitätsverfahren berechnet acht räumliche Varianten (Drehungen/Spiegelungen)
jeweils in beiden Zeitrichtungen, richtet die Ergebnisse wieder aus und mittelt
sie vor der Umwandlung zurück auf 8 Bit. Volle Auflösung und Float32-Präzision;
keine verkleinerten HD-Bilder, keine FP16-Berechnung.

Diese 16 Durchläufe können richtungsabhängige Fehler reduzieren, dauern aber
länger als eine Standard-Inferenz. Sie garantieren nicht für jede Szene eine
Verbesserung; Mittelung kann auch feine Details glätten. Zum Vergleich gibt es
**RIFE 4.25 · Standard** ohne Mittelung. Die Qualitätsstufe ist die aufwendigste
Einstellung dieser App, keine Garantie eines weltweit besten Verfahrens.

Bei Unterstützung wird die Apple-GPU über MPS genutzt. Intel Macs rechnen auf
CPU. Falls die MPS-Berechnung scheitert, wird dieselbe RIFE-Berechnung auf CPU
versucht. Kein stiller Rückfall auf klassischen Optical Flow. Modell und Video
bleiben lokal; keine Video-Uploads. Optical Flow und Standbild bleiben ausdrücklich
auswählbare Alternativen.

## Export und Ausgabe

**FFV1/MKV – Qualitätsmaster:** keine weitere verlustbehaftete Bildkompression.
Jedes intakte dekodierte Bild wird nach dem Export pixelweise mit der Quelle
verglichen. Die Dateien können sehr groß werden. Einen FFV1-fähigen Player,
beispielsweise VLC, verwenden; QuickTime ist dafür nicht vorgesehen.

**H.264/MOV – QuickTime-kompatibel:** CRF 12, langsames Encoder-Preset,
kleinere Dateien. Gute Bilder werden erneut verlustbehaftet komprimiert; kein
verlustfreier Master. Exotische kopierte Audio-Codecs können die Wiedergabe
in QuickTime einschränken.

Beide Modi kopieren vorhandene Audiospuren ohne Neu-Kodierung. Originaldateien
bleiben unverändert. Bildanzahl und Zeitstempel werden vollständig geprüft.
MKV speichert Zeitstempel in Millisekunden: Rundung unter einer Millisekunde
ist zulässig, ohne Frames zu entfernen oder hinzuzufügen.

Ausgabe neben der Quelle oder im gewählten Zielordner:

- `name_REPAIRED.mkv` bzw. `.mov`
- `name_repair_report.txt` und `.json`: Timecodes, übersprungene Stellen,
  Modellversion, Präzision, tatsächlich verwendetes Gerät und Exportmodus.

Vorhandene Dateien werden nicht überschrieben. Bei Abbruch werden temporäre
Videodateien entfernt; der aktuelle Inferenzdurchlauf muss gegebenenfalls
zuerst enden. Die abschließende Prüfung benötigt zusätzlich Zeit.

## Erkennung und Grenzen

Schwarzframe: mindestens 99,5 % der Pixel mit Grauwert höchstens 12/255.
Maximal sechs Frames zwischen ausreichend hellen intakten Bildern. Struktur,
Helligkeitsverteilung und bis zu zehn Nachbarbilder beiderseits werden auf
auffällige Schnitte/Blenden geprüft. Unsichere Stellen bleiben erhalten.

Diese Erkennung ist heuristisch: absichtliche kurze Schwarzbilder zwischen
ähnlichen Szenen sind nicht sicher von Defekten zu unterscheiden. Auch RIFE
kann bei schnellen Bewegungen, Verdeckungen oder Schnitten verzogene Konturen
erzeugen. Verlorene Originalbilder sind nicht exakt wiederherstellbar.
Ausgabe an den Bericht-Timecodes kontrollieren.

Genau eine Videospur, SDR mit 8 Bit. HDR/höhere Bittiefen werden abgelehnt.
Rotationsmetadaten müssen vorher ins Bild eingerechnet werden. Untertitel,
Kapitel und Timecode-/Datenspuren werden nicht übernommen. Containerfremde
Audiospuren führen zu einem Fehler statt stiller Neukodierung.

## Herkunft und Entwicklung

RIFE 4.25 stammt von Practical-RIFE (hzwer), das diese Version für die meisten
Szenen als Standard empfiehlt: https://github.com/hzwer/Practical-RIFE
MIT-Lizenz und genaue Herkunft: `vendor_rife/LICENSE`, `vendor_rife/HERKUNFT.md`.
Gewichte stammen vom offiziellen Modell-Link; Prüfsumme wird vor Modellstart
kontrolliert. PyTorch 2.2.2 erlaubt eine gemeinsame Laufzeit für Intel und
Apple Silicon; neuere Versionen liefern keine offiziellen Intel-Mac-Wheels.

Entwicklung: `python -m pip install -r requirements.txt`, `python app.py`.
Tests: `python -m unittest discover -s tests -v`.
Der Prüfbericht erläutert, was tatsächlich getestet wurde.
