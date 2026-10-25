# V8-Downloadpaket

Die drei Binärteile ergeben zusammen das vollständige V8-ZIP. Sie ermöglichen
die Übertragung über die größenbegrenzte GitHub-Schnittstelle.

Im Repository-Hauptordner `python scripts/prepare_package.py` ausführen.
Das Skript prüft die SHA-256-Summe, erzeugt `VideoRepairMac-RIFE-v8.zip` und
stellt das mitgelieferte RIFE-Modell für den Quellcode bereit.

Für die normale Installation das einzelne ZIP unter
[Releases](https://github.com/krejuee/VideoRepairMac-RIFE/releases) verwenden.

Ab V9 enthalten diese Teile das unveränderte V8-Basispaket für die
Modellbereitstellung. `python scripts/build_release.py` baut danach V9 aus dem
aktuellen Quellcode im Hauptordner.
