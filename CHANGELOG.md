# Versionsübersicht

## Version 9 – 25.10.2026

- Titelübernahme aus Oberfläche und Reparatur entfernt; Titelerkennung unverändert.
- Alte Titel-Flags ignorieren; keine Wiederverwendung von Titel-Compositing-Patches.
- Standardmäßig aktiver Automatikschalter für den Gesamtexport nach allen Prüfclips.
  Nur bei ausgeschaltetem Schalter vor der Gesamtdatei nachfragen.
- ProRes/MOV als Standard; alte MKV-Voreinstellungen einmalig migrieren.
- V9-Paket direkt aus aktuellem Quellcode und geprüftem Modell erstellen.


## Repository-Aktualisierung – 17.10.2026

- Version 8 aus dem vom Projektverantwortlichen bereitgestellten ZIP übernommen.
- Vollständigen Quellcode, RIFE-Modell, Tests und deutsch/englische Dokumentation
  im Hauptordner zugänglich gemacht; Bilder der tatsächlichen Oberfläche ergänzt.
- README, Beitragshinweise, Drittanbieterübersicht und Git-Ausschlüsse ergänzt.
- Automatische Syntax-, Modell- und Testprüfungen eingerichtet.
- Vorhandene MIT-Lizenz mit Copyright krejuee und historische ZIPs beibehalten.
- Projektlizenz und Drittanbieterhinweise zusätzlich ins V8-Downloadpaket gelegt;
  SHA-256-Prüfsummen für dieses Paket und das Modell veröffentlicht.

Die Reparaturimplementierung entspricht unverändert dem bereitgestellten V8-Paket.
Der Installer im Git-Checkout stellt bei Bedarf das Modell aus den Binärteilen
bereit. GitHub Actions baut daraus das einzelne ZIP für den Release-Download.

## Version 8

- Reparatur-Patches unabhängig von Exportformat und Sprache wiederverwenden;
  frühere Einstellungsvarianten bleiben verfügbar.
- Fortsetzbarer Gesamtexport mit geprüften Abschnitten und erneut startbarem
  Zusammenfügen; beschädigte oder geänderte Teile werden neu berechnet.
- Deutsch/Englisch während der Sitzung und gespeicherte Projekteinstellungen.
- 10-Bit-SDR progressiv/TFF/BFF, FFV1-Master und ProRes/MOV; XDCAM bleibt 8 Bit.

Der mitgelieferte Prüfbericht beschreibt 72 erfolgreiche Tests am 16.10.2026.
Dieser historische Bericht ist von den Prüfungen des Repository-Imports zu
unterscheiden.

## Version 7

- Lücken bis zwei Sekunden anhand der tatsächlichen Bildzeitstempel.
- Vollständige Reparaturlänge in Prüfclips und Übernahme ihrer Patches.

## Version 6

- Manuelle Typänderung mit L und editierbarer Framebereich.
- Bewegungsverlängerung aus bis zu sechs intakten Bildern für Schnitt-Lücken.
- Zurückhaltendere Schnittheuristik und automatische Halbbild-Erkennung.
- Prüfclips/Patches vor dem Gesamtexport, Fortschritt und Metadaten/TC.

## Historische Pakete

Die bisherigen ZIPs bleiben als Archiv verfügbar. Für den aktuellen Stand
`VideoRepairMac-RIFE-v8.zip` verwenden. Ein separates V7-ZIP liegt nicht vor.
