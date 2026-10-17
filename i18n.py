"""German/English presentation layer; internal IDs and render plans stay stable."""
from PySide6 import QtWidgets as W
from PySide6.QtCore import QSettings, QTranslator, QLibraryInfo
import re

_language = 'de'
_translator = None
# Full labels plus reusable phrases for live progress and event descriptions.
CATALOG = {
'Deutsch':'Deutsch', 'English':'English',
'Video Repair RIFE 8 · Lücken bis 2 Sekunden':'Video Repair RIFE 8 · Gaps up to 2 seconds',
'↓\n\nVideo hier hineinziehen\noder klicken, um eine Datei auszuwählen':'↓\n\nDrop a video here\nor click to select a file',
'Datei laden → Scannen → Stellen prüfen → Prüfclips → Gesamtdatei':'Load file → Scan → Review events → Review clips → Complete file',
'Lücken und Schnitte werden getrennt gelistet.':'Gaps and cuts are listed separately.',
'Max. Lücke':'Max. gap', 'Empfindlichkeit':'Sensitivity', 'Sekunden':'seconds',
'Szenen und Blenden prüfen':'Check scenes and fades', 'Titel über Schwarz erhalten':'Preserve titles over black',
'Sanfter Farbabgleich · max. 2/255':'Gentle colour matching · max. 2/255', 'RIFE auf CPU':'RIFE on CPU',
'Automatisch · Kennzeichen aus Quelle':'Automatic · source flags', 'Aus · progressive Bilder':'Off · progressive frames',
'TFF · oberes Halbbild zuerst':'TFF · upper field first', 'BFF · unteres Halbbild zuerst':'BFF · lower field first',
'Halbbildreihenfolge wird beim Scan aus den Decoder-Kennzeichen gelesen.':'The scan reads field order from decoder flags.',
'RIFE 4.25 · Qualität · 16 Varianten':'RIFE 4.25 · Quality · 16 variants', 'RIFE 4.25 · Standard · schneller':'RIFE 4.25 · Standard · faster',
'Optical Flow · Bewegung rekonstruieren':'Optical Flow · reconstruct motion',
'Bewegung verlängern · bis 6 Bilder vor der Lücke':'Extend motion · up to 6 frames before the gap',
'Standbild · letztes gutes Bild halten':'Freeze · hold last good frame',
'Überblendung · beide Nachbarbilder':'Blend · both neighbouring frames',
'Nächstes Nachbarbild · ohne Vermischen am Schnitt':'Nearest frame · no blending at a cut',
'Verlustfreier Master · FFV1/MKV (z. B. VLC)':'Lossless master · FFV1/MKV (e.g. VLC)',
'Hochwertiges MOV · H.264 8 Bit / ProRes 10 Bit':'High quality MOV · H.264 8 bit / ProRes 10 bit',
'ProRes MOV · 10 Bit':'ProRes MOV · 10 bit', 'MXF · XDCAM HD422 · 50 Mbit/s':'MXF · XDCAM HD422 · 50 Mbit/s',
'Prüfclips zuerst · ihre Reparaturbilder werden in die Gesamtdatei übernommen.':'Review clips first · their repair frames are reused in the complete file.',
'Ausgabe: neben dem Originalvideo':'Output: next to the original video',
'Bereit. Datei auswählen; erst ‚Scannen‘ startet die Analyse.':'Ready. Select a file; press Scan to start analysis.',
'Aktueller Prüfclip':'Current review clip', 'Alle Prüfclips rendern':'Render all review clips',
'Alle vorgemerkten Korrekturen als separate Clips rendern. Erhaltene Stellen können einzeln gerendert werden.':'Render all planned repairs as separate clips. Preserved events can be rendered individually.',
'Gesamtdatei erstellen':'Create complete file', 'Ergebnis öffnen':'Open result', 'Bericht öffnen':'Open report',
'8/10-Bit-SDR · Original und Ton bleiben erhalten · bis 2 Sekunden pro Lücke\nEmpfindlichkeit aktualisiert die Liste sofort, ohne erneuten Scan.\nBei Interlaced die richtige Halbbildreihenfolge wählen. Längere Lücken können KI-Artefakte erzeugen.':'8/10-bit SDR · Original and audio are preserved · gaps up to 2 seconds\nSensitivity updates the list immediately without scanning again.\nSelect the correct field order for interlaced video. Longer gaps can cause AI artefacts.',
'Original prüfen · Korrektur festlegen':'Review original · choose repair', '← Vorige Stelle':'← Previous event',
'Nächste Stelle →':'Next event →', 'Noch keine Analyse':'No analysis yet', 'Tastenkürzel / Hilfe · H':'Shortcuts / Help · H',
'Prüfclip extern öffnen':'Open review clip externally', 'Video wählen. Danach eine Stelle in der Liste auswählen.':'Select a video, then select an event in the list.',
'Automatisch erkannt':'Automatically detected', 'Automatische Bewertung':'Automatic decision', 'Lücke · gezielt reparieren':'Gap · repair this event',
'Überspringen · Original erhalten':'Skip · preserve original', 'Punkt rendern · Enter':'Render event · Enter',
'Gespeicherten Clip ansehen':'View saved clip', 'Prüfclips werden einzeln gespeichert und bleiben bei Abbruch erhalten.':'Review clips are saved individually and retained when cancelled.',
'Vorschau zeigt auch den Übergang vor und nach der Lücke.':'Preview includes the transition before and after the gap.',
'← Bild':'← Frame', 'Bild →':'Frame →', 'Abspielen':'Play', 'Pause':'Pause', 'Schleife':'Loop',
'Vorschau ohne Ton · Bei Interlaced werden Halbbilder in TFF/BFF-Reihenfolge angezeigt.\n‚Lücke reparieren‘ übergeht die Szenenprüfung nur für diese Stelle. Ausgabe immer prüfen.':'Silent preview · interlaced fields are displayed in TFF/BFF order.\nRepair gap overrides scene protection for this event. Always check the output.',
'Wird gerechnet':'Rendering', 'Clip vorhanden · Plan geändert':'Clip exists · plan changed', 'Lücke · manuell':'Gap · manual',
'Einzelentscheidung übernommen. Vorschau neu berechnen oder exportieren.':'Event decision saved. Render the preview or export.',
'Ausgabeordner wählen':'Select output folder', 'Zielordner':'Output folder',
'Datei geladen. Einstellungen wählen und ‚Scannen‘ drücken.':'File loaded. Choose settings and press Scan.',
'Alle Prüfclips':'All review clips',
'Prüfclips gespeichert. Auswählen und begutachten; Gesamtdatei wird separat bestätigt.':'Review clips saved. Select and inspect them; complete-file export needs separate confirmation.',
'Tastenkürzel / Ablauf':'Shortcuts / Workflow', 'Verarbeitung fehlgeschlagen.':'Processing failed.',
'Abgebrochen. Fertige Prüfclips und Exportabschnitte bleiben gespeichert. Mit Fortsetzen weiterarbeiten.':'Cancelled. Completed review clips and export parts are saved. Use Resume to continue.',
'Video wählen':'Select video', 'Alle Dateien':'All files', 'Reparatur':'Repair', 'Scannen':'Scan', 'Fortsetzen':'Resume',
'Abbrechen':'Cancel', 'Typ':'Type', 'Plan / Status':'Plan / Status', 'Korrekturtyp':'Event type',
'Framebereich · ab 0':'Frame range · zero based', 'Von':'From', 'Bis':'To', 'Start ← Bild':'Start ← Frame', 'Ende ← Bild':'End ← Frame',
'Diese Stelle':'This event', 'Reparatur hier':'Repair method', 'Globale Einstellung':'Global setting', 'RIFE · Qualität':'RIFE · Quality',
'RIFE · Standard':'RIFE · Standard', 'Bewegung verlängern · bis 6 Bilder':'Extend motion · up to 6 frames',
'Nächstes Nachbarbild am Schnitt':'Nearest frame at cut', 'Titel hier':'Titles here', 'Nicht übernehmen':'Do not preserve',
'Geplante Reparatur':'Planned repair', 'Reparatur / Export':'Repair / Export', 'Standbild':'Freeze', 'Überblendung':'Blend',
'Korrigiert · Prüfclip':'Repaired · review clip', 'Geprüft · erhalten':'Reviewed · preserved', 'Erhalten · manuell':'Preserved · manual',
'Vorgemerkt':'Planned', 'Erhalten':'Preserved', 'Repariert':'Repaired',
'Reparatur vorhanden · Vorschau fehlt':'Repair saved · preview missing', 'Keine erkannten Stellen':'No detected events',
'Prüfclips bleiben nach dem Speichern auch bei Abbruch erhalten.':'Saved review clips remain available after cancellation.',
'Reparaturvorschau bei Bedarf berechnen':'Render repair preview when needed',
'Originalclip wird bei Auswahl einer Stelle geladen.':'Select an event to load the original clip.',
'Originalclip · Geplante Reparatur noch nicht berechnet.\nOhne Ton · Etwa 1 Sekunde Kontext vor und nach der Stelle.':'Original clip · planned repair has not been rendered yet.\nSilent · about 1 second of context before and after the event.',
'Ohne Ton · Interlaced wird für die Ansicht als Halbbildfolge dargestellt.':'Silent · interlaced video is displayed as a sequence of fields.',
'Erkennt Bilder mit mindestens':'Detects frames with at least', 'Schwarzanteil':'black pixels', 'Grauwert bis':'grey value up to',
'0 = streng, 100 = tolerant':'0 = strict, 100 = tolerant', 'Keine Videodatei':'No video file',
'Bitte eine einzelne Videodatei auswählen.':'Please select a single video file.', 'Reparatur startet …':'Starting repair …',
'Video wird analysiert …':'Analysing video …', 'Automatisch':'Automatic', 'progressiv':'progressive',
'Prüfclips gespeichert. Der Plan wurde inzwischen geändert; betroffene Clips erneut rendern.':'Review clips saved. The plan has changed; render the affected clips again.',
'Gesamtdatei erstellen?':'Create complete file?',
'Jetzt die vollständige Datei mit dem aktuellen Reparaturplan erstellen?\nBereits gespeicherte Prüfclips bleiben unabhängig davon erhalten.':'Create the complete file using the current repair plan?\nSaved review clips will remain available.',
'Abbruch wird ausgeführt …':'Cancelling …',
'Noch kein Prüfclip · etwa 1 Sekunde Vor- und Nachlauf · ohne Ton':'No review clip yet · about 1 second of handles · silent',
'Originalclip wird geladen …':'Loading original clip …',
'Originalclip bereit. Navigieren und Korrekturtyp/Methode wählen.':'Original clip ready. Navigate and choose the repair type and method.',
'Gespeicherter Prüfclip bereit. Bilder einzeln prüfen oder abspielen.':'Saved review clip ready. Step through frames or play.',
'noch nicht berechnet':'not rendered yet', 'Liste aktualisiert:':'List updated:', 'Stellen · kein neuer Scan.':'events · no new scan.',
'Ausgabe:':'Output:', 'Fertig:':'Done:', 'Stellen repariert,':'events repaired,', 'unverändert.':'unchanged.',
'Analyse fertig:':'Analysis complete:', 'Schwarzstellen,':'black events,', 'Schnitt-Einträge.':'cut events.', 'Reparaturen geplant.':'repairs planned.',
'Keine Reparatur vorgemerkt. Erhaltene Stellen können einzeln als Prüfclip gespeichert werden.':'No repair planned. Preserved events can be saved individually as review clips.',
'Keine Korrektur für die Gesamtdatei vorgemerkt.':'No repair planned for the complete file.', 'Analyse:':'Analysis:',
'Exportstelle wird geladen …':'Loading exported event …', 'Vorschau wird berechnet …':'Rendering preview …', 'Prüfclip':'Review clip',
'0 fehlende Frames · mit L einen Reparaturbereich setzen':'0 missing frames · use L to set a repair range',
'Bewegungsreferenzen:':'Motion references:', 'von 6 intakten Bildern derselben Szene':'of 6 intact frames from the same scene',
'Entscheidungen konnten nicht gespeichert werden:':'Could not save decisions:',
'Schnitt mit schwarzer Lücke':'Cut with black gap', 'Schwarze Lücke':'Black gap', 'Lücke':'Gap',
'Schnitt':'Cut', 'Blende / Schwarzphase':'Fade / black section', 'Blende':'Fade',
'Dekodierte Bilder: progressiv':'Decoded frames: progressive', 'Decoder-Kennzeichen:':'Decoder flags:', '(Halbbildreihenfolge)':'(field order)',
'Bild':'Frame', 'Halbbild':'Field', 'vorher':'before', 'nachher':'after',
'Exportabschnitt':'Export part', 'bereits vorhanden':'already saved', 'Füge zusammen':'Joining',
'Prüfe Export':'Verifying export', 'Reparaturbilder':'Repair frames', 'speichern':'save',
'Unterstützt werden SDR-Videos mit 8 oder 10 Bit.':'SDR videos with 8 or 10 bits are supported.',
'HDR/PQ/HLG wird nicht konvertiert. Unterstützt wird SDR mit 8 oder 10 Bit.':'HDR/PQ/HLG is not converted. Supported: 8-bit and 10-bit SDR.',
'Das XDCAM-MXF-Profil ist 8 Bit. Für 10 Bit bitte FFV1/MKV oder ProRes/MOV wählen.':'This XDCAM MXF profile is 8 bit. For 10 bit, choose FFV1/MKV or ProRes/MOV.',
'Die Datei enthält keine Videospur.':'The file contains no video track.',
'Bitte eine Datei mit genau einer Videospur verwenden.':'Please use a file with exactly one video track.',
'Die Quelldatei wurde seit der Analyse verändert. Bitte erneut scannen.':'The source has changed since analysis. Please scan again.',
'Unvollständige Bildfolge beim Export.':'Incomplete frame sequence during export.',
'Bildfolge hat sich seit der Analyse verändert.':'The frame sequence has changed since analysis.',
'Nicht aufsteigende Paket-Zeitstempel zwischen Exportabschnitten.':'Packet timestamps do not increase between export parts.',
'Exportabschnitt enthält fehlende Pakete.':'An export part is missing packets.',
'Der Prüfclip-Index gehört zu einer anderen Quelldatei.':'The review clip index belongs to another source file.',
'Das Pixelformat wechselt innerhalb der Quelle.':'The pixel format changes within the source.',
'Fehlende Bild-Zeitstempel; sichere Synchronität ist nicht möglich.':'Missing frame timestamps; reliable synchronisation is not possible.',
'Nicht aufsteigende Bild-Zeitstempel.':'Frame timestamps do not increase.',
'Keine zu reparierenden Lücken.':'No gaps to repair.',
'Wechselnde TFF/BFF-Kennzeichen. Bitte Halbbildreihenfolge manuell wählen.':'Changing TFF/BFF flags. Please select the field order manually.',
'MXF unterstützt hier PCM-Ton mit 48 kHz (16/24 Bit), wie bei XDCAM. Für anderen Ton MOV/MKV wählen.':'MXF supports 48 kHz PCM audio (16/24 bit), as used by XDCAM. Choose MOV/MKV for other audio.',
'Gesamtdatei ist bereits vorhanden und geprüft.':'Complete file already exists and has been verified.',
}
HELP = """Load → Scan → Review events → Render review clips → Confirm complete file

U   Skip event and continue
L   Mark / repair gap
C   Cut with black gap · extend motion
A / D   Previous / next event
I   Blend
O   Optical Flow
N   RIFE Standard
Q   RIFE Quality
B   Preserve fade / black section
Enter   Render and save selected event
Space   Play / pause
← / →   Step through frames
H / F1   This help window

Ordinary cuts have 0 missing frames. L starts a one-frame repair range; adjust From/To or Start/End ← Frame.
Maximum gap: 2 seconds. Sensitivity updates the list without scanning again.
At a cut, extend motion using up to 6 intact frames of the same scene, then make a hard cut.
The event list stays interactive during scanning and review-clip rendering. Each queue uses the plan at its start.
Later changes are marked as Plan changed. Completed clips and export parts survive cancellation.
Resume uses the same source and output folder; it scans again after restarting, then reuses matching saved repairs and parts.
Review clips are silent, with about 1 second of handles. TC = source timecode; elapsed TC = position from clip start.
10-bit SDR: FFV1/MKV or ProRes/MOV. XDCAM MXF is 8 bit. HDR is not converted."""
CATALOG.update({'Maximale Lücke muss zwischen 0 und 2 Sekunden liegen.': 'Maximum gap must be between 0 and 2 seconds.',
 'Maximale Lücke darf 2 Sekunden nicht überschreiten.': 'Maximum gap must not exceed 2 seconds.',
 'Die Stelle ist länger als die eingestellte Maximallücke.': 'This event exceeds the selected maximum gap length.',
 'Die Stelle ist länger als die eingestellte Maximallücke (höchstens 2 Sekunden).': 'This event exceeds the '
                                                                                    'selected maximum gap length '
                                                                                    '(up to 2 seconds).',
 'Empfindlichkeit muss zwischen 0 und 100 liegen.': 'Sensitivity must be between 0 and 100.',
 'Analysewerte fehlen. Dieses Video muss einmal analysiert werden.': 'Analysis data is missing. Scan this video '
                                                                     'once.',
 'Ungültige Erkennungseinstellungen.': 'Invalid detection settings.',
 'Interlaced benötigt eine gerade Bildhöhe.': 'Interlaced video needs an even frame height.',
 'MXF/XDCAM HD422 benötigt 1920×1080 oder 1280×720. Bitte MOV oder MKV wählen.': 'MXF/XDCAM HD422 needs 1920×1080 '
                                                                                 'or 1280×720. Choose MOV or MKV.',
 'MXF benötigt eine konstante Bildrate. Für variable Bildraten MOV/MKV verwenden.': 'MXF needs a constant frame '
                                                                                    'rate. For variable frame '
                                                                                    'rates use MOV/MKV.',
 'Diese Bildrate wird vom MXF/XDCAM-Profil nicht unterstützt.': 'This frame rate is not supported by the '
                                                                'MXF/XDCAM profile.',
 'MXF/XDCAM 1080 unterstützt hier bis 30 Vollbilder/s. 1080i25 mit TFF ist möglich.': 'MXF/XDCAM 1080 supports up '
                                                                                      'to 30 full frames/s. '
                                                                                      '1080i25 with TFF is '
                                                                                      'supported.',
 '720-Zeilen-MXF wird nur progressiv unterstützt.': '720-line MXF is supported only as progressive video.',
 'Unbekannte Halbbildreihenfolge.': 'Unknown field order.',
 'Gedrehtes Video: bitte zuerst die Ausrichtung fest ins Bild exportieren.': 'Rotated video: first export the '
                                                                             'rotation into the image.',
 'Keine dekodierbaren Videobilder gefunden.': 'No decodable video frames found.',
 'Die Quelldatei wurde während der Analyse verändert.': 'The source file changed during analysis.',
 'Interlaced benötigt gleich große Bilder mit gerader Höhe.': 'Interlaced video needs equally sized frames with '
                                                              'even height.',
 'Ungültige Halbbild-Zeitstempel.': 'Invalid field timestamps.',
 'Falsche Anzahl rekonstruierter Halbbilder.': 'Incorrect number of reconstructed fields.',
 'Interlaced 4:2:0 benötigt eine durch vier teilbare Bildhöhe.': 'Interlaced 4:2:0 needs a frame height divisible '
                                                                 'by four.',
 'Interlaced-Export benötigt ein planares YUV-Pixelformat.': 'Interlaced export needs a planar YUV pixel format.',
 'Unbekannte Einzelentscheidung.': 'Unknown event decision.',
 'Unbekannter Korrekturtyp.': 'Unknown event type.',
 'Bitte einen gültigen Framebereich innerhalb der Quelle wählen.': 'Choose a valid frame range within the source.',
 'Dieser Bereich überschneidet sich mit einer anderen vorgemerkten Reparatur.': 'This range overlaps another '
                                                                                'planned repair.',
 'Ein normaler Schnitt enthält keine fehlenden Bilder und braucht keine Reparatur.': 'An ordinary cut has no '
                                                                                     'missing frames and needs no '
                                                                                     'repair.',
 'Für die Reparatur fehlen intakte Referenzbilder auf beiden Seiten.': 'The repair needs intact reference frames '
                                                                       'on both sides.',
 'Bewegung verlängern benötigt mindestens zwei intakte Bilder derselben Szene vor der Lücke. Bitte Bereich prüfen oder Standbild wählen.': 'Motion '
                                                                                                                                           'extension '
                                                                                                                                           'needs '
                                                                                                                                           'at '
                                                                                                                                           'least '
                                                                                                                                           'two '
                                                                                                                                           'intact '
                                                                                                                                           'frames '
                                                                                                                                           'of '
                                                                                                                                           'the '
                                                                                                                                           'same '
                                                                                                                                           'scene '
                                                                                                                                           'before '
                                                                                                                                           'the '
                                                                                                                                           'gap. '
                                                                                                                                           'Check '
                                                                                                                                           'the '
                                                                                                                                           'range '
                                                                                                                                           'or '
                                                                                                                                           'choose '
                                                                                                                                           'Freeze.',
 'Unbekannter Reparaturmodus.': 'Unknown repair method.',
 'Bewegungsreferenzen fehlen. Bitte den Prüfclip zuerst rechnen.': 'Motion references are missing. Render the '
                                                                   'review clip first.',
 'Vorschaubilder stimmen nicht mit der analysierten Bildfolge überein.': 'Preview frames do not match the '
                                                                         'analysed frame sequence.',
 'Vorschauausschnitt konnte nicht vollständig gelesen werden.': 'Could not read the complete preview range.',
 'Die Quelldatei hat sich geändert. Bitte erneut analysieren.': 'The source file changed. Please scan again.',
 'Die Quelldatei hat sich geändert. Bitte neu analysieren.': 'The source file changed. Please scan again.',
 'Die Quelldatei hat sich verändert. Bitte erneut scannen.': 'The source file changed. Please scan again.',
 'Keine sicher reparierbaren Lücken gefunden.': 'No safely repairable gaps found.',
 'Reparaturbereiche überschneiden sich. Bitte Framebereiche korrigieren.': 'Repair ranges overlap. Adjust the '
                                                                           'frame ranges.',
 'Unbekannter Export- oder Reparaturmodus.': 'Unknown export format or repair method.',
 'Unbekanntes Exportformat.': 'Unknown export format.',
 'Die Quelldatei wurde während des Exports verändert.': 'The source file changed during export.',
 'Zeitstempelprüfung fehlgeschlagen.': 'Timestamp verification failed.',
 'Verlustfreie Bildprüfung fehlgeschlagen bei Bild': 'Lossless pixel verification failed at frame',
 'Bildanzahlprüfung fehlgeschlagen.': 'Frame count verification failed.',
 'Encoder liefert fehlende Paket-Zeitstempel.': 'The encoder produced packets without timestamps.',
 'Exportabschnitt enthält eine unvollständige Bildfolge.': 'An export part contains an incomplete frame sequence.',
 'Exportabschnitt hat unerwartete Pakete.': 'An export part contains unexpected packets.',
 'Prüfclip-Bildprüfung fehlgeschlagen.': 'Review clip pixel verification failed.',
 'Prüfclip enthält eine unvollständige Bildfolge.': 'Review clip contains an incomplete frame sequence.',
 'Die Quelldatei wurde während der Clipberechnung verändert. Bitte erneut scannen.': 'The source file changed '
                                                                                     'during clip rendering. '
                                                                                     'Please scan again.',
 'Gespeicherte Reparatur enthält nicht die erwartete Bildanzahl.': 'Saved repair has an unexpected frame count.',
 'Der gespeicherte Prüfclip ist unvollständig.': 'The saved review clip is incomplete.',
 'Prüfclip und Original haben unterschiedliche Bildfolgen. Interlaced-Einstellung prüfen.': 'Review clip and '
                                                                                            'original have '
                                                                                            'different frame '
                                                                                            'sequences. Check the '
                                                                                            'interlaced setting.',
 'Dieses Projekt wird bereits in einem anderen Fenster berechnet.': 'This project is already rendering in another '
                                                                    'window.',
 'Unterschiedliche Bittiefe der Referenzbilder.': 'Reference frames have different bit depths.',
 'Ungültige Zwischenbild-Position.': 'Invalid interpolation position.',
 'RIFE liefert ungültige Bildwerte.': 'RIFE produced invalid pixel values.',
 'RIFE lieferte ein unerwartet schwarzes/farbloses Bild. Diese Stelle bitte mit einem anderen Modus prüfen.': 'RIFE '
                                                                                                              'produced '
                                                                                                              'an '
                                                                                                              'unexpectedly '
                                                                                                              'black '
                                                                                                              'or '
                                                                                                              'colourless '
                                                                                                              'frame. '
                                                                                                              'Check '
                                                                                                              'this '
                                                                                                              'event '
                                                                                                              'with '
                                                                                                              'another '
                                                                                                              'method.',
 'RIFE rechnet auf CPU weiter:': 'RIFE continues on CPU:',
 'MPS-Berechnungsfehler': 'MPS computation error',
 'Nicht endliche GPU-Bildwerte': 'Non-finite GPU pixel values',
 'Unerwartet schwarzes oder farbloses Zwischenbild': 'Unexpectedly black or colourless intermediate frame',
 'Berechne Reparaturbilder …': 'Computing repair frames …',
 'Analysiere Bild': 'Analysing frame',
 'reparierbare Lücken gefunden': 'repairable gaps found',
 'Vorschau bereit': 'Preview ready',
 'Kontrollclips': 'Review clips',
 'Prüfe gespeicherten Gesamtexport …': 'Checking saved complete export …',
 'Gesamtdatei bereits vorhanden': 'Complete file already saved',
 'Prüfe exportierte Bildfolge …': 'Verifying exported frame sequence …',
 'Reparatur abgeschlossen': 'Repair complete',
 'Gespeicherte Reparatur wird übernommen · keine neue Berechnung': 'Reusing saved repair · no new computation',
 'Prüfclip gespeichert': 'Review clip saved',
 'Speichere': 'Saving',
 'Bewegung aus': 'Extending motion from',
 '/6 intakten Bildern verlängern …': '/6 intact frames …',
 'oberes Halbbild': 'upper field',
 'unteres Halbbild': 'lower field',
 'Originalclip': 'Original clip',
 'Fertiges Exportbild': 'Completed export frame',
 'Geplante Reparatur · MOV-Kompression erst beim Export': 'Planned repair · MOV compression applied on export',
 'Diese Stelle bleibt erhalten': 'This event is preserved',
 'Anfang einer längeren Schwarzphase': 'Start of a longer black section',
 'Einzelentscheidung': 'Event decision',
 'Framebereich': 'Frame range',
 'Starker Bildwechsel vor/nach Schwarzphase → Schnitt mit schwarzer Lücke': 'Strong change before/after black '
                                                                            'section → cut with black gap',
 'Längere Schwarzphase · überschreitet die eingestellte Maximallücke': 'Long black section · exceeds the selected '
                                                                       'maximum gap',
 'Starker Bildwechsel zwischen benachbarten Bildern · Schnitt bleibt erhalten': 'Strong change between adjacent '
                                                                                'frames · cut is preserved',
 'Kein intaktes Bild auf beiden Seiten': 'No intact frame on both sides',
 'Längere Schwarzphase': 'Long black section',
 'Freigegeben: Szenenprüfung ausgeschaltet': 'Approved: scene protection is off',
 'Dunkle Nachbarbilder / mögliche Schwarzblende': 'Dark neighbours / possible fade to black',
 'Möglicher Szenenwechsel oder starke Bewegung': 'Possible scene change or strong motion',
 'Mögliche Ab- oder Aufblende': 'Possible fade in or fade out',
 'Kurze Lücke, Nachbarbilder ähnlich': 'Short gap, similar neighbouring frames',
 'Gespeicherter Prüfclip': 'Saved review clip',
 'Frames und Halbbilder': 'Frames and fields',
 'Sprache': 'Language'})
_pattern = re.compile('|'.join(re.escape(k) for k in sorted(CATALOG, key=len, reverse=True)))


def t(value):
    value = str(value)
    if _language == 'de' or value.startswith(('/', '✓\n')):
        return value
    if value.startswith('Datei laden → Scannen → Ereignisse prüfen'):
        return HELP
    if value in CATALOG:
        return CATALOG[value]
    return _pattern.sub(lambda m: CATALOG[m.group()], value)


def set_language(code, persist=True):
    global _language, _translator
    _language = code if code in ('de', 'en') else 'de'
    app = W.QApplication.instance()
    if app and _translator:
        app.removeTranslator(_translator)
    _translator = None
    if app and _language == 'de':
        translator = QTranslator(app)
        if translator.load('qtbase_de', QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(translator)
            _translator = translator
    if persist:
        QSettings('VideoRepair', 'VideoRepair').setValue('language', _language)
    if app:
        for widget in app.allWidgets():
            if hasattr(widget, 'retranslate'):
                widget.retranslate()


def saved_language():
    return QSettings('VideoRepair', 'VideoRepair').value('language', 'de')


class Text:
    def setText(self, text):
        self._source_text = text
        super().setText(t(text))
    def setToolTip(self, text):
        self._source_tip = text
        super().setToolTip(t(text))
    def retranslate(self):
        if getattr(self, '_source_text', None) is not None:
            super().setText(t(self._source_text))
        if hasattr(self, '_source_tip'):
            super().setToolTip(t(self._source_tip))


class QLabel(Text, W.QLabel):
    def __init__(self, text='', *args, **kw):
        super().__init__('', *args, **kw)
        self.setText(text)
    def setPixmap(self, pixmap):
        self._source_text = None
        super().setPixmap(pixmap)
    def clear(self):
        self._source_text = None
        super().clear()


class QPushButton(Text, W.QPushButton):
    def __init__(self, text='', *args, **kw):
        super().__init__('', *args, **kw)
        self.setText(text)


class QCheckBox(Text, W.QCheckBox):
    def __init__(self, text='', *args, **kw):
        super().__init__('', *args, **kw)
        self.setText(text)


class QComboBox(W.QComboBox):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._labels = {}
    def addItem(self, text, userData=None):
        self._labels[self.count()] = text
        super().addItem(t(text), userData)
    def setItemText(self, index, text):
        self._labels[index] = text
        super().setItemText(index, t(text))
    def retranslate(self):
        blocked = self.blockSignals(True)
        for index, text in self._labels.items():
            super().setItemText(index, t(text))
        self.blockSignals(blocked)


class QTableWidgetItem(Text, W.QTableWidgetItem):
    def __init__(self, text=''):
        super().__init__('')
        self.setText(text)


class QTableWidget(W.QTableWidget):
    def setHorizontalHeaderLabels(self, labels):
        self._headers = labels
        super().setHorizontalHeaderLabels([t(s) for s in labels])
    def retranslate(self):
        self.setHorizontalHeaderLabels(self._headers)
        for row in range(self.rowCount()):
            for col in range(self.columnCount()):
                item = self.item(row, col)
                if item and hasattr(item, 'retranslate'):
                    item.retranslate()


class QProgressBar(W.QProgressBar):
    def setFormat(self, text):
        self._source_format = text
        super().setFormat(t(text))
    def retranslate(self):
        if hasattr(self, '_source_format'):
            super().setFormat(t(self._source_format))


class QMessageBox(W.QMessageBox):
    @staticmethod
    def information(parent, title, text, *args):
        return W.QMessageBox.information(parent, t(title), t(text), *args)
    @staticmethod
    def warning(parent, title, text, *args):
        return W.QMessageBox.warning(parent, t(title), t(text), *args)
    @staticmethod
    def question(parent, title, text, *args):
        return W.QMessageBox.question(parent, t(title), t(text), *args)


class QFileDialog(W.QFileDialog):
    @staticmethod
    def getOpenFileName(parent, caption='', directory='', filter=''):
        return W.QFileDialog.getOpenFileName(parent, t(caption), directory, t(filter))
    @staticmethod
    def getExistingDirectory(parent, caption='', directory=''):
        return W.QFileDialog.getExistingDirectory(parent, t(caption), directory)
