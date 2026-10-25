# Prüfung – Video Repair RIFE 9

Die V9-Tests prüfen insbesondere:

- Vom Prüfclip bis zur vollständigen ProRes/MOV-Datei: Originalton, Bildanzahl
  und keine Bestätigungsabfrage bei aktiver Automatik.
- Automatik aus: genau eine Rückfrage; Nein erhält Prüfclips ohne Gesamtdatei.
  Der Schalterzustand wird beim erneuten Laden wiederhergestellt.
- Einzelnes Punkt-Rendering startet auch bei aktiver Automatik keinen Master.
- V8-MKV-Voreinstellungen wechseln einmalig auf ProRes; explizite V9-Auswahl bleibt.
- Echtes RIFE ersetzt einen nur auf Schwarzframes sichtbaren Titelbereich, auch
  bei alten Titel-Flags. Die Erkennung bleibt erhalten; alte Compositing-Patches
  werden nicht als passend akzeptiert.

macOS-Build, Apple-GPU/MPS und Adobe Media Encoder wurden hier nicht ausgeführt.
Das gemeldete MKV-Wiedergabeproblem ist weiterhin nicht abschließend geklärt;
ProRes/MOV ist deshalb die neue Voreinstellung. Interne verlustfreie Reparatur-
Patches bleiben für Wiederverwendung und Wiederaufnahme erhalten.

---

## Historischer Prüfbericht der V8-Basis

# Prüfung – Video Repair RIFE 8

**72 automatisierte Tests bestanden** am 16.10.2026 (63,442 Sekunden).
Alle 56 bisherigen Tests sowie 16 neue V8-Tests sind enthalten.

## Neue V8-Prüfungen

- Exportwechsel und Rückkehr zu früheren Reparatureinstellungen verwenden
  vorhandene native Reparaturen ohne erneute Inferenz.
- Abbruch nach gespeichertem Patch, beschädigte Vorschau, fehlender Originalclip,
  beschädigter Index und Import eines V7-Fingerprints wurden geprüft.
- Abbruch während der Abschnittscodierung und während des finalen Zusammenfügens:
  passende fertige Abschnitte werden übernommen. Ein gezielt beschädigter Abschnitt
  wird einzeln neu gerechnet. Eine geänderte Reparatur invalidiert nur überlappende
  Abschnitte. Identische fertige Ausgaben werden wiederverwendet.
- Mehrteiliger Export mit variabler Bildrate und verzögertem AAC-Ton erhält
  Bildzeitstempel und Audiopakete; MKV-Zeiten werden mit 1-ms-Toleranz geprüft.
- Mehrteiliger MXF-Export erhält TFF, eingebetteten Quell-Timecode und PCM-Ton.
- 10-Bit 4:2:0 progressiv, 4:2:2 TFF und 4:4:4 BFF: native Ebenen, intakte
  Halbbilder und Zeitstempel nach FFV1-Export geprüft. ProRes/MOV ist 10 Bit
  und enthält eine Timecode-Spur; 10-Bit-XDCAM wird ausdrücklich abgelehnt.
- Echtes RIFE liefert mehr als 8 Bit Ausgabepräzision; ein vollständiger
  interlaced 10-Bit-RIFE-Patch wird ohne zweite Inferenz in den Master übernommen.
- Deutsch/Englisch-Umschaltung erhält interne IDs und Vorschaubilder; Sprache,
  Projektparameter und Zielordner werden wiederhergestellt. Dateiladen startet
  weder Scan noch Rendering; Fortsetzen startet den notwendigen Scan ausdrücklich.
- Oberfläche auf Deutsch und Englisch als Qt-Screenshot geprüft.

Prüfumgebung: Linux, Python 3.12, PyAV 16.1, PySide6 6.10, Torch 2.2.2 CPU.

## Lücken bis zwei Sekunden

- Bei 25 fps werden 50 schwarze Frames vollständig freigegeben, als Prüfclip
  gespeichert, angezeigt und in den verlustfreien Master übernommen.
- Bei 50 fps funktioniert derselbe Ablauf mit 100 schwarzen Frames.
- RIFE Standard/CPU berechnet einen vollständigen 50-Frame-Patch mit dem
  tatsächlichen Modell Practical-RIFE 4.25, nicht mit einer Attrappe.
- Ändern von 12 Frames auf 2 Sekunden und zurück auf 0,5 Sekunden verwendet
  ausschließlich den Cache; erneutes Öffnen der Quelle ist im Test gesperrt.
- 51 Frames bei 25 fps sind länger als zwei Sekunden und bleiben gesperrt,
  auch bei manueller Freigabe. Ein manuell verkürzter 50-Frame-Bereich ist zulässig.
- Variable Bildrate: 60 Frames mit 20-ms-Abständen werden als 1,2 Sekunden
  zugelassen, 30 Frames mit 80-ms-Abständen als 2,4 Sekunden abgelehnt.
- Genehmigte Prüfclips enthalten das vollständige Ereignis und Nachlauf.
  Der Master übernimmt ihre nativen Patchbilder ohne zweite Inferenz.
- Schnitt-Lücken behalten die Bewegungsverlängerung aus sechs intakten Bildern;
  die neue Szene wird nicht eingeblendet. Die Bewegung wurde mit verschobener
  Textur geprüft. TFF/BFF wird aus realen AVFrame-Kennzeichen ausgelesen.
- Manuelle Schnittbereiche/Entscheidungen werden nach erneutem Scan restauriert.
  Bei einem frühen Teilergebnis werden noch nicht gescannte Bereiche zurückgestellt.
- Oberfläche visuell geprüft: Maximaldauer 2 Sekunden und Ereignisdauer sichtbar.
  Die älteren Funktionen für Liste, Navigation, Tastenkürzel und Prüfclips sind enthalten.

## Bestehende Exportprüfungen

Erkennung, VFR-/Audiopaket-Zeitstempel, Source-Schutz, Abbruch und Wiederaufnahme,
Cache-Sensitivität, manuelle Typ-/Methodenänderung, Kontrollclips, native YUV-
Halbbilder, Rec.709-Farbbereiche, echtes RIFE Standard/Qualität und MXF/XDCAM
1080i25/TFF mit Timecode und PCM sind Bestandteil der automatisierten Tests.
Die Warteschlange bleibt während Einzelclip-Rendering interaktiv; der
Gesamtexport wird nach gespeicherten Prüfclips separat bestätigt.

Mac-Installer/Startskript: Bash-Syntax geprüft. Modellprüfsumme unverändert.
macOS-Build, Apple-GPU/MPS und der native XDCAM-Clip des Nutzers wurden hier
nicht geprüft. Lange HD-Lücken wurden in dieser Umgebung mit kleinen
synthetischen Testbildern geprüft; keine Garantie für fehlerfreie Rekonstruktion
verlorener Inhalte. Prüfclips enthalten keinen Ton, der Master kopiert Originalton.
