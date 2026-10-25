# Video Repair RIFE 9 – Fortsetzen, Deutsch/Englisch und 10 Bit

## Neu in V9

- **ProRes/MOV ist vorausgewählt**, auch für 8-Bit-Quellen.
- **Gesamtdatei nach Prüfclips automatisch erstellen** ist standardmäßig aktiv.
  Nach „Alle Prüfclips rendern“ läuft der Gesamtexport ohne Rückfrage weiter.
  Bei ausgeschaltetem Schalter erscheint die bisherige Ja/Nein-Abfrage.
- **Titel erhalten** und die Einstellung pro Stelle sind entfernt. Titel über
  Schwarz werden unverändert erkannt; die gesamte beschädigte Stelle wird mit
  der gewählten Methode rekonstruiert. Alte Titel-Einstellungen werden ignoriert.
  Alte Reparaturen mit aktivierter Titelübernahme werden nicht wiederverwendet.
- Alte MKV-Standardauswahlen aus V8 werden einmalig auf ProRes umgestellt. Eine
  danach in V9 ausdrücklich gespeicherte MKV-Auswahl bleibt erhalten.

Über Nacht: laden → scannen → Plan/Einstellungen prüfen → Automatik aktiv lassen
→ **Alle Prüfclips rendern**. Das Laden allein startet weiterhin nichts.
Einzelne Punkte werden weiterhin separat gerendert.

## Beibehalten aus V8

**Fortsetzen:** dieselbe unveränderte Quelldatei und denselben Zielordner verwenden.
Nach einem Programmneustart lädt die Dateiauswahl nur das Projekt; **Fortsetzen**
startet ausdrücklich einen Scan und setzt danach die Arbeit fort. Innerhalb der
laufenden Sitzung wird der vorhandene Scan verwendet. Empfindlichkeit und
Maximallänge aktualisieren weiterhin nur die Liste, ohne neuen Scan.

Reparaturbilder, Prüfclip-Exporte und die Abschnitte der Gesamtdatei werden getrennt
verwaltet. Passende Reparaturen werden anhand der tatsächlich bildwirksamen
Einstellungen wiederverwendet. Ein anderer Container oder eine andere Sprache
startet keine neue KI-Berechnung. Frühere Einstellungsvarianten bleiben erhalten:
Zurückwechseln kann einen bereits gerechneten Patch wieder verwenden. Passende
V7-Patches im gleichen Prüfungsordner werden übernommen.

Die Gesamtdatei wird in ungefähr zehn Sekunden langen Videoabschnitten codiert.
Jeder fertige Abschnitt wird geprüft und gespeichert. Bei Abbruch bleiben diese
Abschnitte erhalten. Nach einer geänderten Korrektur müssen nur betroffene
Abschnitte neu codiert werden. Beim abschließenden Zusammenfügen werden die
codierten Pakete mit dem Originalton übernommen. Ein Abbruch in diesem letzten
Schritt startet beim Fortsetzen nur das Zusammenfügen neu. Eine identische,
bereits fertige Gesamtdatei wird nach Prüfung wieder geöffnet.

Dateien werden auf Größe und SHA-256 geprüft; beschädigte Dateien werden gezielt
neu erstellt. Einzelne Ereignis-Journale können einen beschädigten Hauptindex
wiederherstellen. Einstellungen, Entscheidungen und Zielordner werden gespeichert.
Den gesamten `*_PRUEFUNG_*`-Ordner behalten, bis das Projekt abgeschlossen ist.
Die zusätzlichen Exportabschnitte benötigen Platz auf dem Datenträger.

**Deutsch / English:** oben rechts umschalten. Oberfläche, Hilfe, Ereignistypen,
Fortschritt und Meldungen wechseln während der Sitzung. Die Sprache bleibt gespeichert.
Tastenkürzel und interne Reparatur-IDs bleiben gleich.

**8- und 10-Bit-SDR:** 10-Bit-YUV 4:2:0, 4:2:2 und 4:4:4 werden verarbeitet,
auch mit TFF/BFF. Rekonstruktion und native Patches bleiben in hoher Präzision;
die Bildschirmvorschau wird für die Anzeige auf 8 Bit umgesetzt. Das RIFE-Modell
rechnet in Float32 und gibt 16-Bit-RGB-Arbeitswerte für den 10-Bit-Export zurück.
Das Modell bleibt eine Schätzung verlorener Inhalte. HDR/PQ/HLG wird abgelehnt.

Maximale Lücke: 1–12 Frames oder 0,25 / 0,5 / 1 / 1,5 / 2 Sekunden, standardmäßig
2 Sekunden. Lange Lücken benötigen mehr Zeit und Speicher und können deutliche
KI-Artefakte enthalten. Immer zunächst den Prüfclip kontrollieren.

![V9 auf Deutsch mit einem synthetischen 10-Bit-Testclip](Bilder/Oberflaeche.png)

Ansicht aus der Linux-Qt-Prüfung; die native Mac-Darstellung kann abweichen.
English documentation: [README_EN.md](README_EN.md).

## Ablauf

1. Datei laden: kein automatischer Scan oder Renderstart.
2. Einstellungen wählen, dann **Scannen**. Die wachsende Liste bleibt anklickbar.
  **Interlaced: Automatisch** liest progressiv/TFF/BFF aus den Decoder-Kennzeichen;
  falsch markierte Quellen können manuell übersteuert werden.
3. Ereignis auswählen und im Originalclip mit Slider, Bildtasten oder Wiedergabe
  prüfen. Das lädt keine RIFE-Berechnung.
4. Typ, Entscheidung, Methode und Framebereich ändern. **L** setzt eine Lücke
  auch an einem erkannten Schnitt. Ein normaler Schnitt hat zunächst 0 fehlende
  Frames; L wählt zuerst 1 Frame. **Von/Bis** sind Frameindizes ab 0. **Start/Ende
  ← Bild** übernehmen die Vorschauposition. Überschneidende Reparaturen sind gesperrt.
5. **Punkt rendern / Enter** speichert Original, Ergebnis und verlustfreien Patch.
  Das gespeicherte Ergebnis wird rechts angezeigt. Prüfclips enthalten keinen Ton.
6. **Alle Prüfclips rendern** arbeitet die vorgemerkten Korrekturen ab. Fertige
  Clips bleiben anklickbar. Balken zeigen Warteschlange und aktuellen Prüfclip.
7. **Gesamtdatei erstellen** berechnet zuerst fehlende passende Prüfclips, übernimmt
  bei aktiver Automatik ohne Rückfrage deren Reparaturbilder ohne zweite Inferenz.

Die Warteschlange nutzt die Einstellungen beim Start. Spätere Änderungen werden
als **Plan geändert** markiert. Bei Abbruch bleiben fertige Clips und Entscheidungen
bestehen. Im Gesamtexport sind Einstellungen gesperrt; Fortschritt und Abbruch
bleiben verfügbar. Eine laufende KI-Operation muss vor einem Abbruch enden.

## Methoden

**RIFE 4.25** ist eine neuronale Zwischenbildberechnung. Standard nutzt eine
Variante, Qualität 16 Varianten in Float32. Das Modell liegt bei. Weitere
Methoden: Optical Flow, Bewegungsverlängerung, Standbild, Überblendung und
nächstes Nachbarbild. Alles läuft lokal. MPS wird verwendet, falls verfügbar;
bei Fehlern oder schwarzen/farblosen Ergebnissen erfolgt eine CPU-Prüfung.

**Schnitt mit schwarzer Lücke:** Bewegung aus bis zu **sechs intakten Bildern
derselben ausgehenden Szene** verlängern, anschließend harter Schnitt. Mehrere
Optical-Flow-Schätzungen werden auf das letzte Bild übertragen; ihr Median liefert
die Geschwindigkeit. Die neue Szene wird nicht in die erzeugten Bilder gemischt.
Mindestens zwei intakte Bilder sind notwendig. Beschleunigungen und Verdeckungen
können Artefakte verursachen; freigelegte Ränder werden aus Randpixeln fortgesetzt.
Am Schnitt bleibt ausdrücklich gewähltes Standbild als Alternative möglich.
Für andere Methoden den Typ auf **Lücke** ändern.

Normale Schnitte und Blenden werden automatisch erhalten. Manuelle Umklassifizierung
und Reparatur sind innerhalb des gewählten Limits möglich. Die Schnittheuristik
berücksichtigt flankierende Bewegung, kann aber falsche Einträge erzeugen.

TFF/BFF wird getrennt rekonstruiert und im Export signalisiert. Intakte Halbbilder
bleiben erhalten. Die Ansicht zeigt vergrößerte Halbbilder in zeitlicher Reihenfolge;
die Ausgabe bleibt interlaced. Interlaced-4:2:0 braucht durch vier teilbare Höhe.
YUV-Matrix und Farbbereich werden explizit berücksichtigt. Sanfter Farbabgleich
korrigiert bei ähnlichen Referenzen höchstens 2/255 pro RGB-Kanal.

## Tastenkürzel

| Taste | Aktion |
| --- | --- |
| U | Überspringen und weiter |
| L | Lücke setzen / reparieren |
| C | Schnitt-Lücke mit Bewegungsverlängerung |
| A / D | Voriges / nächstes Ereignis |
| I / O | Überblendung / Optical Flow |
| N / Q | RIFE Standard / Qualität |
| B | Blende erhalten |
| Enter | Punkt rendern |
| Leertaste | Abspielen / Pause |
| Pfeile links / rechts | Bildweise navigieren |
| H / F1 | Hilfe |

## Dateien, Metadaten und Timecode

Im Zielordner: `name_PRUEFUNG_<Kennung>/` mit Original-, Ergebnis- und `_patch.mkv`-
Dateien. `INDEX.json` ordnet sie zu und speichert Entscheidungen. Den Ordner
zusammenhalten. Nach neuem Laden und Scannen derselben unveränderten Quelle
werden passende Clips und Entscheidungen wieder zugeordnet. Nach Programmneustart
ist einmal Scannen nötig; der Analysecache liegt im Arbeitsspeicher.

Dateinamen enthalten Quell-TC oder LaufzeitTC. Eingebetteter Prüfclip-TC beginnt
am ersten Vorlaufbild. Drop-Frame wird berücksichtigt. MKV speichert TC als Tag,
MOV als Timecode-Spur und MXF als Container-Timecode. Keine LTC-Tonspur.
Der Gesamtexport übernimmt die Originalaudiospuren ohne Neukodierung.

Container-/Video-/Audio-Tags werden übernommen, soweit das Zielformat sie schreiben
kann. Quell-TC, Farbkennzeichen, Seitenverhältnis und TFF/BFF werden übertragen.
Lesbare Original-Tags stehen zusätzlich im TXT-/JSON-Bericht. Encoder und MXF-
Dateikennungen werden neu erzeugt. VANC, Untertitel, Kapitel und sonstige zusätzliche
Datenspuren werden nicht übernommen. Keine bitidentische Kopie aller MXF-Metadaten.

## Export und Installation

- **FFV1/MKV:** verlustfrei. Intakte dekodierte Bilder werden pixelweise geprüft.
- **Hochwertiges MOV:** bei 8 Bit H.264 CRF 12; bei 10 Bit automatisch ProRes.
- **ProRes/MOV:** 10-Bit 4:2:2 HQ; bei 4:4:4/RGB-Quellen ProRes 4444 mit
  10-Bit-Eingabe. Hochwertig, aber verlustbehaftet.
- **MXF/XDCAM HD422:** MPEG-2, 8-Bit 4:2:2, 50 Mbit/s. 10-Bit-Quellen
 benötigen FFV1/MKV oder ProRes/MOV; keine stille Herabsetzung auf 8 Bit. 1920×1080 bei 25/29,97/30
 Vollbildern/s, auch TFF/BFF; oder progressives 1280×720 bei 25/29,97/30/50/59,94.
 Konstante Bildrate und PCM-Ton mit 48 kHz, 16/24 Bit erforderlich.

MOV/MXF werden in den Exportabschnitten neu kodiert; die Kompression kann leicht vom
Prüfclip abweichen. Originale werden nicht überschrieben. Bildanzahl und Zeitstempel
werden geprüft. 8/10-Bit-SDR, genau eine Videospur; HDR und Rotation abgelehnt.
MKV speichert Zeitstempel auf einem Millisekundenraster; Audiopakete bleiben
inhaltlich unverändert, ihre Zeitstempel können dabei um weniger als 1 ms runden.

ZIP entpacken, **Installieren.command** öffnen. macOS 14+, Python 3.12 (3.11 möglich),
einmalig Internet zum Laden der Pakete. Python: https://www.python.org/downloads/macos/
Installationsziel: `~/Applications/Video Repair RIFE 9.app`. Installer, Quellcode und
Modell enthalten; keine vorkompilierte/notarisierte Mac-App. Ältere Apps bleiben
erhalten. Mac-Build und Apple-GPU wurden hier nicht geprüft.

Nach einem Git-Checkout zuerst `python scripts/prepare_package.py` ausführen;
dies stellt ZIP und Modell aus den Binärteilen bereit. Im Release-ZIP ist das
Modell bereits enthalten. Entwicklung: `python -m pip install -r requirements.txt`,
dann `python app.py`.
Tests: `python -m unittest discover -s tests -v`; MXF-Tests brauchen ffprobe.
RIFE: https://github.com/hzwer/Practical-RIFE (MIT, Hinweise in `vendor_rife`).
Prüfumfang: `PRUEFUNG.md`.
