# Drittanbieter und Herkunft

Die [MIT-Lizenz im Hauptordner](LICENSE) enthält den Projekt-Hinweis
`Copyright (c) 2026 krejuee`. Drittanbieter behalten ihre eigenen Lizenz- und
Copyright-Hinweise; die Projektlizenz ersetzt diese nicht.

## Mitgelieferte Bestandteile

- **Practical-RIFE / hzwer:** angepasster Modellcode unter `vendor_rife/`,
  Modellgewichte unter `models/rife425/flownet.pkl`.
  [Originale MIT-Lizenz](vendor_rife/LICENSE),
  [Herkunft, Anpassung und Modellprüfsumme](vendor_rife/HERKUNFT.md),
  [Upstream-Projekt](https://github.com/hzwer/Practical-RIFE).
- Historische ZIPs enthalten eigene Kopien dieser Hinweise. Bei Weitergabe
  müssen die zugehörigen Hinweise erhalten bleiben.

## Vom Installer geladene Pakete

Die genauen Versionen stehen in [requirements.txt](requirements.txt):

| Bestandteil | Projekt / Lizenzinformationen |
| --- | --- |
| PyAV und dessen FFmpeg-Laufzeit | https://github.com/PyAV-Org/PyAV und https://ffmpeg.org/legal.html |
| NumPy | https://numpy.org/about/ |
| OpenCV | https://github.com/opencv/opencv und https://github.com/opencv/opencv-python |
| PyTorch | https://github.com/pytorch/pytorch |
| PySide6 / Qt | https://doc.qt.io/qtforpython-6/licenses.html |
| PyInstaller | https://pyinstaller.org/en/stable/license.html |

Bei der Installation können weitere transitive Pakete hinzukommen. Maßgeblich
sind die Lizenzdateien der tatsächlich installierten Versionen und der konkret
gebündelten Binärdateien. Dieses Repository enthält keine vollständige
Lizenzinventur eines fertigen macOS-App-Bundles. Vor dessen separater Weitergabe
müssen auch die enthaltenen Qt-/FFmpeg-Bibliotheken und weiteren Komponenten
einschließlich ihrer Hinweise berücksichtigt werden.
