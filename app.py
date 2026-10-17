from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt, QUrl, QTimer, QSettings
from PySide6.QtGui import QDesktopServices, QFont, QImage, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QFileDialog, QCheckBox, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView, QSlider, QScrollArea, QSpinBox)

import engine
import review
import master
import i18n
from i18n import (QLabel, QPushButton, QCheckBox, QComboBox, QTableWidget,
                  QTableWidgetItem, QProgressBar, QMessageBox, QFileDialog)


class Worker(QThread):
    update = Signal(int, str)
    scanned = Signal(object)
    partial = Signal(object)
    finished_job = Signal(str, str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, path, directory, auto, mode, max_gap, result=None, settings=None, export="lossless", clips=False, artifacts=None):
        super().__init__()
        self.path, self.directory, self.auto = path, directory, auto
        self.mode, self.max_gap, self.result = mode, max_gap, result
        self.settings, self.export = settings or {}, export
        self.clips = clips
        self.artifacts = artifacts or {}
        self.stop = threading.Event()

    def run(self):
        try:
            result = self.result or engine.scan(self.path, self.max_gap,
                lambda p, s: self.update.emit(p // 3 if self.auto else p, s), self.stop.is_set,
                on_partial=self.partial.emit, **self.settings)
            self.scanned.emit(result)
            if self.auto and self.result is not None and any(g.approved for g in result.gaps):
                output, report = engine.repair(result, self.directory, self.mode,
                    lambda p, s: self.update.emit(p, s), self.stop.is_set,
                    export=self.export, control_clips=self.clips, artifacts=self.artifacts)
            else:
                output = ""
                report = engine.write_report(result, self.directory, self.mode)
            self.finished_job.emit(output, report)
        except engine.Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class PreviewWorker(QThread):
    update = Signal(int, str)
    ready = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, result, gap, mode, export, rendered=None, original_only=False, artifact=None):
        super().__init__()
        self.result, self.gap, self.mode, self.export, self.rendered = result, gap, mode, export, rendered
        self.original_only = original_only
        self.artifact = artifact
        self.stop = threading.Event()

    def run(self):
        try:
            self.ready.emit(review.preview_artifact(self.result, self.gap, self.artifact, self.stop.is_set)
                if self.artifact else engine.preview(self.result, self.gap, self.mode,
                lambda p, s: self.update.emit(p, s), self.stop.is_set, self.export, self.rendered,
                self.original_only, context=1.0))
        except engine.Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class ClipWorker(QThread):
    update = Signal(int, str)
    ready = Signal(str, object)
    event_started = Signal(str)
    event_progress = Signal(int)
    completed = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, result, events, directory, mode, export):
        super().__init__()
        self.result, self.events, self.directory = result, events, directory
        self.mode, self.export = mode, export
        self.stop = threading.Event()

    def run(self):
        try:
            renderer = engine.Renderer(self.result.settings, cancel=self.stop.is_set)
            for index, gap in enumerate(self.events):
                engine.check(self.stop.is_set)
                self.event_started.emit(review.event_key(gap))
                def progress(p, s):
                    self.event_progress.emit(p)
                    self.update.emit(round((index + p / 100) * 100 / len(self.events)),
                        f"Prüfclip {index + 1}/{len(self.events)} · {s}")
                renderer.progress = progress
                data = review.render_event(self.result, gap, self.directory, self.mode, self.export,
                    progress=progress, cancel=self.stop.is_set, renderer=renderer)
                self.ready.emit(review.event_key(gap), data)
            self.completed.emit()
        except engine.Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class DropArea(QLabel):
    selected = Signal(str)

    def __init__(self):
        super().__init__("↓\n\nVideo hier hineinziehen\noder klicken, um eine Datei auszuwählen")
        self.setObjectName("drop")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(120)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if self.isEnabled() and len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if self.isEnabled() and len(urls) == 1 and urls[0].isLocalFile():
            self.selected.emit(urls[0].toLocalFile())
            event.acceptProposedAction()

    def mousePressEvent(self, event):
        if self.isEnabled():
            path, _ = QFileDialog.getOpenFileName(self, "Video wählen", "",
                "Videos (*.mov *.mxf *.mp4 *.m4v *.mkv *.avi *.mts *.m2ts);;Alle Dateien (*)")
            if path:
                self.selected.emit(path)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        i18n.set_language(i18n.saved_language(), persist=False)
        self.pending_resume = False
        self.worker, self.preview_worker, self.result, self.output, self.report = None, None, None, "", ""
        self.render_worker, self.artifacts, self.jobs = None, {}, set()
        self.rendering_keys, self.ask_after_render = set(), False
        self.closing = False
        self.preview_data, self.loading_event = None, False
        self.loaded_preview_key, self.pending_original = None, False
        self.original_timer = QTimer(self)
        self.original_timer.setSingleShot(True)
        self.original_timer.timeout.connect(self.load_original_selection)
        self.play_timer = QTimer(self)
        self.play_timer.setSingleShot(True)
        self.play_timer.timeout.connect(self.advance_playback)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh_list)
        self.path, self.directory = "", ""
        self.setWindowTitle(i18n.t("Video Repair RIFE 8 · Lücken bis 2 Sekunden"))
        self.resize(1400, 860)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(540)
        left_panel = QWidget()
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setContentsMargins(0, 0, 0, 0)
        left_panel_layout.addWidget(left_scroll, 3)
        left = QWidget()
        left_scroll.setWidget(left)
        outer.addWidget(left_panel, 5)
        layout = QVBoxLayout(left)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(10)
        title = QLabel("Video Repair · RIFE")
        title.setObjectName("title")
        title_row = QHBoxLayout()
        title_row.addWidget(title, 1)
        self.language = QComboBox()
        self.language.addItem("Deutsch", "de")
        self.language.addItem("English", "en")
        self.language.setCurrentIndex(self.language.findData(i18n.saved_language()))
        self.language.currentIndexChanged.connect(self.change_language)
        title_row.addWidget(self.language)
        layout.addLayout(title_row)
        subtitle = QLabel("Datei laden → Scannen → Stellen prüfen → Prüfclips → Gesamtdatei")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.drop = DropArea()
        self.drop.selected.connect(self.choose)
        layout.addWidget(self.drop)
        row = QHBoxLayout()
        row.addWidget(QLabel("Lücken und Schnitte werden getrennt gelistet."))
        row.addStretch()
        row.addWidget(QLabel("Max. Lücke"))
        self.max_gap = QComboBox()
        for n in range(1, 13):
            self.max_gap.addItem(f"{n} Frames", n)
        self.max_gap.setCurrentIndex(11)
        for seconds in (.25, .5, 1, 1.5, 2):
            self.max_gap.addItem(f"{seconds:g} Sekunden", f"{seconds:g}s")
        self.max_gap.setCurrentIndex(self.max_gap.findData("2s"))
        row.addWidget(self.max_gap)
        layout.addLayout(row)
        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(QLabel("Empfindlichkeit"))
        self.sensitivity = QSlider(Qt.Horizontal)
        self.sensitivity.setRange(0, 100)
        self.sensitivity.setValue(50)
        sensitivity_row.addWidget(self.sensitivity, 1)
        self.sensitivity_value = QLabel()
        sensitivity_row.addWidget(self.sensitivity_value)
        layout.addLayout(sensitivity_row)
        self.threshold_info = QLabel()
        self.threshold_info.setObjectName("muted")
        self.threshold_info.setWordWrap(True)
        layout.addWidget(self.threshold_info)
        guard_row = QHBoxLayout()
        self.scene_guard = QCheckBox("Szenen und Blenden prüfen")
        self.scene_guard.setChecked(True)
        self.preserve_overlays = QCheckBox("Titel über Schwarz erhalten")
        self.preserve_overlays.setChecked(True)
        guard_row.addWidget(self.scene_guard)
        guard_row.addWidget(self.preserve_overlays)
        guard_row.addStretch()
        layout.addLayout(guard_row)
        colors = QHBoxLayout()
        self.color_stabilize = QCheckBox("Sanfter Farbabgleich · max. 2/255")
        self.color_stabilize.setChecked(True)
        self.force_cpu = QCheckBox("RIFE auf CPU")
        colors.addWidget(self.color_stabilize)
        colors.addWidget(self.force_cpu)
        layout.addLayout(colors)
        fields_row = QHBoxLayout()
        fields_row.addWidget(QLabel("Interlaced"))
        self.interlaced = QComboBox()
        self.interlaced.addItem("Automatisch · Kennzeichen aus Quelle", "auto")
        self.interlaced.addItem("Aus · progressive Bilder", "off")
        self.interlaced.addItem("TFF · oberes Halbbild zuerst", "tff")
        self.interlaced.addItem("BFF · unteres Halbbild zuerst", "bff")
        fields_row.addWidget(self.interlaced, 1)
        layout.addLayout(fields_row)
        self.field_info = QLabel("Halbbildreihenfolge wird beim Scan aus den Decoder-Kennzeichen gelesen.")
        self.field_info.setWordWrap(True)
        self.field_info.setObjectName("muted")
        layout.addWidget(self.field_info)
        options = QHBoxLayout()
        options.addWidget(QLabel("Reparatur"))
        self.mode = QComboBox()
        self.mode.addItem("RIFE 4.25 · Qualität · 16 Varianten", "rife_hq")
        self.mode.addItem("RIFE 4.25 · Standard · schneller", "rife")
        self.mode.addItem("Optical Flow · Bewegung rekonstruieren", "flow")
        self.mode.addItem("Bewegung verlängern · bis 6 Bilder vor der Lücke", "motion")
        self.mode.addItem("Standbild · letztes gutes Bild halten", "hold")
        self.mode.addItem("Überblendung · beide Nachbarbilder", "blend")
        self.mode.addItem("Nächstes Nachbarbild · ohne Vermischen am Schnitt", "nearest")
        options.addWidget(self.mode, 1)
        self.folder = QPushButton("Zielordner")
        self.folder.clicked.connect(self.pick_folder)
        options.addWidget(self.folder)
        layout.addLayout(options)
        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("Export"))
        self.export = QComboBox()
        self.export.addItem("Verlustfreier Master · FFV1/MKV (z. B. VLC)", "lossless")
        self.export.addItem("Hochwertiges MOV · H.264 8 Bit / ProRes 10 Bit", "compatible")
        self.export.addItem("ProRes MOV · 10 Bit", "prores")
        self.export.addItem("MXF · XDCAM HD422 · 50 Mbit/s", "mxf")
        export_row.addWidget(self.export, 1)
        layout.addLayout(export_row)
        clip_note = QLabel("Prüfclips zuerst · ihre Reparaturbilder werden in die Gesamtdatei übernommen.")
        clip_note.setWordWrap(True)
        layout.addWidget(clip_note)
        self.destination = QLabel("Ausgabe: neben dem Originalvideo")
        self.destination.setObjectName("muted")
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        self.status = QLabel("Bereit. Datei auswählen; erst ‚Scannen‘ startet die Analyse.")
        self.status.setWordWrap(True)
        left_panel_layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        left_panel_layout.addWidget(self.progress)
        self.render_progress = QProgressBar()
        self.render_progress.setFormat("Aktueller Prüfclip · %p %")
        self.render_progress.setVisible(False)
        left_panel_layout.addWidget(self.render_progress)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Timecode", "Typ", "Frames", "Plan / Status"])
        for column in range(3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setMinimumHeight(170)
        self.table.itemSelectionChanged.connect(self.select_event)
        left_panel_layout.addWidget(self.table, 2)
        buttons = QHBoxLayout()
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        left_panel_layout.addWidget(footer)
        self.rescan_button = QPushButton("Scannen")
        self.scan_button = self.rescan_button
        self.rescan_button.setEnabled(False)
        self.rescan_button.clicked.connect(lambda: self.launch(False))
        buttons.addWidget(self.rescan_button)
        self.resume_button = QPushButton("Fortsetzen")
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self.resume_work)
        buttons.addWidget(self.resume_button)
        self.render_all_button = QPushButton("Alle Prüfclips rendern")
        self.render_all_button.setToolTip("Alle vorgemerkten Korrekturen als separate Clips rendern. Erhaltene Stellen können einzeln gerendert werden.")
        self.render_all_button.clicked.connect(self.render_all)
        self.render_all_button.setEnabled(False)
        buttons.addWidget(self.render_all_button)
        self.repair_button = QPushButton("Gesamtdatei erstellen")
        self.repair_button.setObjectName("primary")
        self.repair_button.setEnabled(False)
        self.repair_button.clicked.connect(self.repair_now)
        buttons.addWidget(self.repair_button)
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        footer_layout.addLayout(buttons)
        result_buttons = QHBoxLayout()
        self.show_output = QPushButton("Ergebnis öffnen")
        self.show_output.setEnabled(False)
        self.show_output.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.output)))
        result_buttons.addWidget(self.show_output)
        self.show_report = QPushButton("Bericht öffnen")
        self.show_report.setEnabled(False)
        self.show_report.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.report)))
        result_buttons.addWidget(self.show_report)
        result_buttons.addStretch()
        footer_layout.addLayout(result_buttons)
        note = QLabel("8/10-Bit-SDR · Original und Ton bleiben erhalten · bis 2 Sekunden pro Lücke\n"
                      "Empfindlichkeit aktualisiert die Liste sofort, ohne erneuten Scan.\n"
                      "Bei Interlaced die richtige Halbbildreihenfolge wählen. Längere Lücken können KI-Artefakte erzeugen.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        panel = QWidget()
        outer.addWidget(panel, 7)
        self.build_review(QVBoxLayout(panel))
        for combo in (self.interlaced, self.mode, self.export):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(14)
        self.sensitivity.valueChanged.connect(self.update_sensitivity)
        for control in (self.max_gap, self.interlaced):
            control.currentIndexChanged.connect(self.invalidate_scan)
        for control in (self.scene_guard, self.preserve_overlays, self.color_stabilize, self.force_cpu):
            control.toggled.connect(self.invalidate_scan)
        self.mode.currentIndexChanged.connect(self.invalidate_comparison)
        self.export.currentIndexChanged.connect(self.invalidate_comparison)
        self.build_shortcuts()
        self.update_sensitivity()

    def build_review(self, layout):
        layout.setSpacing(10)
        heading = QLabel("Original prüfen · Korrektur festlegen")
        heading.setObjectName("title")
        heading.setStyleSheet("font-size: 23px;")
        layout.addWidget(heading)
        nav = QHBoxLayout()
        self.previous_event = QPushButton("← Vorige Stelle")
        self.next_event = QPushButton("Nächste Stelle →")
        self.previous_event.clicked.connect(lambda: self.move_event(-1))
        self.next_event.clicked.connect(lambda: self.move_event(1))
        self.event_label = QLabel("Noch keine Analyse")
        self.event_label.setAlignment(Qt.AlignCenter)
        nav.addWidget(self.previous_event); nav.addWidget(self.event_label, 1); nav.addWidget(self.next_event)
        layout.addLayout(nav)
        help_row = QHBoxLayout()
        self.help_button = QPushButton("Tastenkürzel / Hilfe · H")
        self.help_button.clicked.connect(self.show_shortcuts)
        help_row.addWidget(self.help_button)
        self.open_clip_button = QPushButton("Prüfclip extern öffnen")
        self.open_clip_button.clicked.connect(self.open_saved_clip)
        help_row.addWidget(self.open_clip_button)
        help_row.addStretch()
        layout.addLayout(help_row)
        self.event_reason = QLabel("Video wählen. Danach eine Stelle in der Liste auswählen.")
        self.event_reason.setWordWrap(True)
        layout.addWidget(self.event_reason)
        row = QHBoxLayout()
        row.addWidget(QLabel("Korrekturtyp"))
        self.event_type = QComboBox()
        self.event_type.addItem("Automatisch erkannt", "auto")
        for value, label in engine.EVENT_NAMES.items():
            self.event_type.addItem(label, value)
        self.event_type.currentIndexChanged.connect(self.edit_event)
        row.addWidget(self.event_type, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Framebereich · ab 0"))
        self.range_start, self.range_end = QSpinBox(), QSpinBox()
        for label, spin in (("Von", self.range_start), ("Bis", self.range_end)):
            row.addWidget(QLabel(label)); row.addWidget(spin)
            spin.editingFinished.connect(self.edit_range)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addStretch()
        self.mark_start, self.mark_end = QPushButton("Start ← Bild"), QPushButton("Ende ← Bild")
        self.mark_start.clicked.connect(lambda: self.mark_range(True))
        self.mark_end.clicked.connect(lambda: self.mark_range(False))
        row.addWidget(self.mark_start); row.addWidget(self.mark_end)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Diese Stelle"))
        self.decision = QComboBox()
        self.decision.addItem("Automatische Bewertung", "auto")
        self.decision.addItem("Lücke · gezielt reparieren", "repair")
        self.decision.addItem("Überspringen · Original erhalten", "skip")
        self.decision.currentIndexChanged.connect(self.edit_event)
        row.addWidget(self.decision, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Reparatur hier"))
        self.event_mode = QComboBox()
        for label, value in (("Globale Einstellung", "global"), ("RIFE · Qualität", "rife_hq"),
                ("RIFE · Standard", "rife"), ("Optical Flow", "flow"),
                ("Bewegung verlängern · bis 6 Bilder", "motion"),
                ("Standbild", "hold"), ("Überblendung", "blend"), ("Nächstes Nachbarbild am Schnitt", "nearest")):
            self.event_mode.addItem(label, value)
        self.event_mode.currentIndexChanged.connect(self.edit_event)
        row.addWidget(self.event_mode, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Titel hier"))
        self.event_overlays = QComboBox()
        for label, value in (("Globale Einstellung", None), ("Erhalten", True), ("Nicht übernehmen", False)):
            self.event_overlays.addItem(label, value)
        self.event_overlays.currentIndexChanged.connect(self.edit_event)
        row.addWidget(self.event_overlays, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.preview_button = QPushButton("Punkt rendern · Enter")
        self.preview_button.setObjectName("primary")
        self.preview_button.clicked.connect(self.render_selected)
        self.actual_button = QPushButton("Gespeicherten Clip ansehen")
        self.actual_button.clicked.connect(self.load_saved_clip)
        row.addWidget(self.preview_button); row.addWidget(self.actual_button)
        layout.addLayout(row)
        self.clip_path = QLabel("Prüfclips werden einzeln gespeichert und bleiben bei Abbruch erhalten.")
        self.clip_path.setObjectName("muted")
        self.clip_path.setWordWrap(True)
        self.clip_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.clip_path)
        images = QHBoxLayout()
        self.original_view, self.repaired_view = QLabel("Original"), QLabel("Geplante Reparatur")
        for title, view in (("Original", self.original_view), ("Reparatur / Export", self.repaired_view)):
            col = QVBoxLayout()
            label = QLabel(title); label.setAlignment(Qt.AlignCenter); label.setMaximumHeight(24); col.addWidget(label)
            view.setMinimumSize(240, 160)
            view.setAlignment(Qt.AlignCenter)
            view.setStyleSheet("background: #050a10; border: 1px solid #304358;")
            col.addWidget(view)
            images.addLayout(col, 1)
        layout.addLayout(images, 1)
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.valueChanged.connect(self.draw_frame)
        layout.addWidget(self.frame_slider)
        self.frame_label = QLabel("Vorschau zeigt auch den Übergang vor und nach der Lücke.")
        self.frame_label.setWordWrap(True)
        layout.addWidget(self.frame_label)
        row = QHBoxLayout()
        self.back_frame = QPushButton("← Bild")
        self.forward_frame = QPushButton("Bild →")
        self.back_frame.clicked.connect(lambda: self.step_frame(-1))
        self.forward_frame.clicked.connect(lambda: self.step_frame(1))
        self.play_button = QPushButton("Abspielen")
        self.play_button.clicked.connect(self.toggle_playback)
        self.loop_preview = QCheckBox("Schleife")
        self.loop_preview.setChecked(True)
        row.addWidget(self.back_frame); row.addWidget(self.play_button); row.addWidget(self.forward_frame)
        row.addStretch(); row.addWidget(self.loop_preview)
        layout.addLayout(row)
        self.preview_note = QLabel("Vorschau ohne Ton · Bei Interlaced werden Halbbilder in TFF/BFF-Reihenfolge angezeigt.\n"
            "‚Lücke reparieren‘ übergeht die Szenenprüfung nur für diese Stelle. Ausgabe immer prüfen.")
        self.preview_note.setWordWrap(True)
        self.preview_note.setObjectName("muted")
        layout.addWidget(self.preview_note)
        self.enable_review(False)

    def current_gap(self):
        row = self.table.currentRow()
        return self.result.gaps[row] if self.result and 0 <= row < len(self.result.gaps) else None

    def gap_status(self, gap):
        key = review.event_key(gap)
        if key in self.rendering_keys:
            return "Wird gerechnet"
        artifact = self.artifacts.get(key)
        if review.matches(self.result, gap, self.mode.currentData(), self.export.currentData(), artifact):
            return "Korrigiert · Prüfclip" if gap.approved else "Geprüft · erhalten"
        if review.patch_matches(self.result, gap, self.mode.currentData(), artifact):
            return "Reparatur vorhanden · Vorschau fehlt"
        if artifact:
            return "Clip vorhanden · Plan geändert"
        return ("Lücke · manuell" if gap.decision == "repair" else "Erhalten · manuell"
                if gap.decision == "skip" else "Vorgemerkt" if gap.approved else "Erhalten")

    def enable_review(self, enabled):
        gap = self.current_gap()
        active = enabled and gap is not None
        for widget in (self.event_type, self.decision, self.event_mode, self.event_overlays, self.range_start, self.range_end):
            widget.setEnabled(active)
        for widget in (self.mark_start, self.mark_end):
            widget.setEnabled(active and self.preview_data is not None)
        self.preview_button.setEnabled(active and not ({"render", "master"} & self.jobs))
        row = self.table.currentRow()
        self.previous_event.setEnabled(active and row > 0)
        self.next_event.setEnabled(active and row + 1 < len(self.result.gaps))
        artifact = self.artifacts.get(review.event_key(gap)) if gap else None
        self.actual_button.setEnabled(active and bool(artifact and artifact.get("clip") and Path(artifact["clip"]).is_file()))
        self.open_clip_button.setEnabled(self.actual_button.isEnabled())
        for widget in (self.frame_slider, self.back_frame, self.forward_frame, self.play_button):
            widget.setEnabled(enabled and self.preview_data is not None)

    def select_event(self):
        gap = self.current_gap()
        if gap:
            row = self.table.currentRow()
            self.event_label.setText(f"{row + 1} / {len(self.result.gaps)} · {engine.event_timecode(self.result, gap.start)}")
            self.event_reason.setText(f"{engine.EVENT_NAMES[gap.effective_kind]} · "
                + (f"{gap.length} Frames · {float(engine.gap_duration(self.result, gap)):.3f} s · {gap.black_fraction * 100:.1f} % Schwarzanteil" if gap.length else "0 fehlende Frames · mit L einen Reparaturbereich setzen")
                + f"\nAnalyse: {gap.reason}")
            if gap.length and engine.resolved_mode(gap, self.mode.currentData()) == "motion":
                self.event_reason.setText(self.event_reason.text() +
                    f"\nBewegungsreferenzen: {gap.start - engine.motion_history_start(self.result, gap)} von 6 intakten Bildern derselben Szene")
            self.loading_event = True
            self.event_type.setCurrentIndex(self.event_type.findData(gap.type_override))
            for spin in (self.range_start, self.range_end):
                spin.setRange(0, self.result.count - 1)
            self.range_start.setValue(gap.start)
            self.range_end.setValue(max(gap.start, gap.end))
            self.decision.model().item(self.decision.findData("repair")).setEnabled(gap.length > 0 and gap.effective_kind != "cut")
            self.decision.setCurrentIndex(self.decision.findData(gap.decision))
            self.event_mode.setCurrentIndex(self.event_mode.findData(gap.mode))
            for index in range(self.event_mode.count()):
                self.event_mode.model().item(index).setEnabled(gap.effective_kind != "cut_gap" or self.event_mode.itemData(index) in ("global", "motion", "hold"))
            # QVariant bool/int equality must not confuse False with the None default.
            self.event_overlays.setCurrentIndex(0 if gap.overlays is None else 1 if gap.overlays else 2)
            self.loading_event = False
            key = self.preview_key()
            if self.preview_data is not None and self.loaded_preview_key == key:
                pass
            else:
                self.clear_preview()
                self.pending_original = True
                self.original_timer.start(80)
            artifact = self.artifacts.get(review.event_key(gap))
            self.clip_path.setText(str(artifact["clip"]) if artifact and artifact.get("clip") else
                "Noch kein Prüfclip · etwa 1 Sekunde Vor- und Nachlauf · ohne Ton")
        else:
            self.clear_preview()
            self.event_label.setText("Keine erkannten Stellen")
        self.enable_review("master" not in self.jobs)

    def move_event(self, direction):
        if self.result:
            row = min(len(self.result.gaps) - 1, max(0, self.table.currentRow() + direction))
            if row >= 0:
                self.table.selectRow(row)

    def edit_event(self):
        gap = self.current_gap()
        if self.loading_event or not gap or "master" in self.jobs:
            return
        try:
            engine.set_event_type(self.result, gap, self.event_type.currentData())
            # A changed type gets its own default plan; do not keep a stale
            # repair decision when the user reclassifies a gap as a fade/cut.
            if self.sender() == self.event_type:
                engine.set_gap_decision(self.result, gap, "auto")
                self.loading_event = True
                self.decision.setCurrentIndex(self.decision.findData("auto"))
                if gap.effective_kind == "cut_gap":
                    self.event_mode.setCurrentIndex(self.event_mode.findData("motion"))
                self.loading_event = False
            else:
                engine.set_gap_decision(self.result, gap, self.decision.currentData())
        except ValueError as exc:
            self.loading_event = True
            self.decision.setCurrentIndex(self.decision.findData(gap.decision))
            self.event_type.setCurrentIndex(self.event_type.findData(gap.type_override))
            self.loading_event = False
            QMessageBox.warning(self, "Einzelentscheidung", str(exc))
            return
        gap.mode = self.event_mode.currentData()
        gap.overlays = self.event_overlays.currentData()
        self.invalidate_comparison()
        self.populate(self.result, gap.identity)
        self.save_review_decisions()
        self.set_busy(False)

    def edit_range(self):
        gap = self.current_gap()
        if not gap or self.loading_event or "master" in self.jobs:
            return
        try:
            engine.set_event_range(self.result, gap, self.range_start.value(), self.range_end.value())
        except ValueError as exc:
            QMessageBox.warning(self, "Framebereich", str(exc))
        self.invalidate_comparison()
        self.populate(self.result, gap.identity)
        self.save_review_decisions()

    def mark_range(self, start):
        if not self.preview_data:
            return
        before, _ = engine.clip_bounds(self.result, self.current_gap(), 1.0)
        factor = 2 if self.result.settings.get("interlaced") != "off" else 1
        frame = before + self.frame_slider.value() // factor
        (self.range_start if start else self.range_end).setValue(frame)
        if self.range_end.value() < self.range_start.value():
            (self.range_end if start else self.range_start).setValue(frame)
        self.edit_range()
        self.status.setText("Einzelentscheidung übernommen. Vorschau neu berechnen oder exportieren.")

    def clear_preview(self, *_):
        self.original_timer.stop()
        self.pending_original = False
        self.stop_playback()
        self.preview_data = None
        self.loaded_preview_key = None
        if hasattr(self, "original_view"):
            self.clip_path.setText("Prüfclips bleiben nach dem Speichern auch bei Abbruch erhalten.")
            self.original_view.clear(); self.original_view.setText("Original")
            self.repaired_view.clear(); self.repaired_view.setText("Reparaturvorschau bei Bedarf berechnen")
            self.frame_slider.setRange(0, 0)
            self.frame_label.setText("Originalclip wird bei Auswahl einer Stelle geladen.")
            self.enable_review("master" not in self.jobs)

    def preview_key(self):
        gap = self.current_gap()
        return (self.result.source, gap.start, gap.end, self.result.settings.get("interlaced", "off"),
                engine.clip_bounds(self.result, gap, 1.0)) if gap else None

    def invalidate_comparison(self, *_):
        self.stop_playback()
        if self.preview_data:
            self.preview_data.comparison = "original"
            self.preview_data.after = self.preview_data.before
            self.preview_note.setText("Originalclip · Geplante Reparatur noch nicht berechnet.\nOhne Ton · Etwa 1 Sekunde Kontext vor und nach der Stelle.")
            self.draw_frame(self.frame_slider.value())
        self.update_event_statuses()

    def load_original_selection(self):
        if not self.pending_original or not self.current_gap():
            return
        if "master" in self.jobs or "preview" in self.jobs:
            return  # release() restarts the pending selection after completion.
        gap = self.current_gap()
        artifact = self.artifacts.get(review.event_key(gap))
        use = review.export_view(artifact, self.export.currentData()) if review.matches(self.result, gap, self.mode.currentData(), self.export.currentData(), artifact) else None
        self.start_preview(original_only=use is None, artifact=use)

    def start_preview(self, actual=False, original_only=False, artifact=None):
        if self.refresh_timer.isActive():
            self.refresh_timer.stop(); self.refresh_list()
        gap = self.current_gap()
        if not gap or ({"master", "preview"} & self.jobs):
            return
        self.original_timer.stop()
        self.pending_original = False
        self.preview_worker = PreviewWorker(self.result, gap, self.mode.currentData(),
            self.export.currentData(), self.output if actual else None, original_only, artifact)
        worker = self.preview_worker
        worker.selection_key = self.preview_key()
        if not ({"scan", "render"} & self.jobs):
            self.preview_worker.update.connect(lambda p, s: self.update_progress(p, s) if not ({"scan", "render", "master"} & self.jobs) else None)
        self.preview_worker.ready.connect(lambda data: self.display_preview(data)
            if worker.selection_key == self.preview_key() else None)
        self.preview_worker.failed.connect(self.error)
        self.preview_worker.cancelled.connect(self.aborted)
        self.preview_worker.finished.connect(lambda: self.release("preview"))
        self.jobs.add("preview")
        self.set_busy(True)
        if not ({"scan", "render"} & self.jobs):
            self.status.setText("Originalclip wird geladen …" if original_only else
                            "Exportstelle wird geladen …" if actual else "Vorschau wird berechnet …")
        self.preview_worker.start()

    def display_preview(self, data):
        self.preview_data = data
        self.loaded_preview_key = self.preview_key()
        self.frame_slider.setRange(0, len(data.before) - 1)
        self.frame_slider.setValue(data.selected)
        self.draw_frame(data.selected)
        self.preview_note.setText(data.note + "\nOhne Ton · Interlaced wird für die Ansicht als Halbbildfolge dargestellt.")
        if not ({"scan", "render"} & self.jobs):
            self.status.setText("Originalclip bereit. Navigieren und Korrekturtyp/Methode wählen." if data.comparison == "original"
                                else "Gespeicherter Prüfclip bereit. Bilder einzeln prüfen oder abspielen.")

    def draw_frame(self, index):
        if not self.preview_data:
            return
        for view, pixels in ((self.original_view, self.preview_data.before[index]),
                             (self.repaired_view, self.preview_data.after[index])):
            if view == self.repaired_view and self.preview_data.comparison == "original":
                view.clear(); view.setText("Geplante Reparatur\nnoch nicht berechnet")
                continue
            h, w = pixels.shape[:2]
            qimage = QImage(pixels.data, w, h, pixels.strides[0], QImage.Format_RGB888).copy()
            view.setPixmap(QPixmap.fromImage(qimage).scaled(view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.frame_label.setText(self.preview_data.labels[index])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "frame_slider") and self.preview_data:
            self.draw_frame(self.frame_slider.value())

    def step_frame(self, delta):
        self.stop_playback()
        self.frame_slider.setValue(min(self.frame_slider.maximum(), max(0, self.frame_slider.value() + delta)))

    def stop_playback(self):
        self.play_timer.stop()
        if hasattr(self, "play_button"):
            self.play_button.setText("Abspielen")

    def toggle_playback(self):
        if not self.preview_data:
            return
        if self.play_timer.isActive():
            self.stop_playback()
        else:
            self.play_button.setText("Pause")
            self.frame_slider.setValue(0)
            self.schedule_frame()

    def schedule_frame(self):
        index = self.frame_slider.value()
        times = self.preview_data.times
        delay = times[index + 1] - times[index] if index + 1 < len(times) else (times[-1] - times[-2] if len(times) > 1 else .04)
        self.play_timer.start(max(1, round(delay * 1000)))

    def advance_playback(self):
        if not self.preview_data:
            return
        index = self.frame_slider.value() + 1
        if index > self.frame_slider.maximum():
            if not self.loop_preview.isChecked():
                self.stop_playback(); return
            index = 0
        self.frame_slider.setValue(index)
        self.schedule_frame()

    def update_sensitivity(self):
        value = self.sensitivity.value()
        settings = engine.detection_settings(value)
        self.sensitivity_value.setText(f"{value}/100")
        self.threshold_info.setText(f"Erkennt Bilder mit mindestens {settings['min_black_fraction'] * 100:.1f} % Schwarzanteil · "
                                    f"Grauwert bis {settings['pixel_threshold']}/255 · 0 = streng, 100 = tolerant")
        self.invalidate_scan()

    def invalidate_scan(self):
        if self.result and "master" not in self.jobs:
            self.invalidate_comparison()
            self.refresh_timer.start(120)

    def settings(self):
        return {"sensitivity": self.sensitivity.value(), "scene_guard": self.scene_guard.isChecked(),
                "interlaced": self.interlaced.currentData(), "preserve_overlays": self.preserve_overlays.isChecked(),
                "color_stabilize": self.color_stabilize.isChecked(), "force_cpu": self.force_cpu.isChecked()}

    def refresh_list(self):
        if not self.result or "master" in self.jobs:
            return
        current = self.current_gap()
        key = current.identity if current else None
        try:
            engine.refresh_scan(self.result, max_gap=self.max_gap.currentData(), **self.settings())
        except ValueError as exc:
            self.error(str(exc))
            return
        self.populate(self.result, key)
        self.save_review_decisions()
        if not ({"scan", "render"} & self.jobs):
            self.status.setText(f"Liste aktualisiert: {len(self.result.gaps)} Stellen · kein neuer Scan.")
        self.set_busy(False)

    def is_busy(self):
        return bool(self.jobs)

    def change_language(self):
        i18n.set_language(self.language.currentData())
        self.setWindowTitle(i18n.t("Video Repair RIFE 8 · Lücken bis 2 Sekunden"))

    def directory_key(self):
        import hashlib
        return "output/" + hashlib.sha256(str(Path(self.path).resolve()).encode()).hexdigest()

    def remember_directory(self):
        QSettings("VideoRepair", "VideoRepair").setValue(self.directory_key(), self.output_directory())

    def restore_preferences(self):
        # Only read the session index. Loading a file never starts decoding.
        probe = engine.Scan(str(Path(self.path).resolve()), engine.signature(self.path), 0, "25", [], [], [])
        try:
            prefs = review.preferences(probe, self.output_directory())
            settings = prefs.get("settings", {})
            values = ((self.mode, prefs.get("mode")), (self.export, prefs.get("export")),
                      (self.max_gap, settings.get("max_gap_selection")),
                      (self.interlaced, settings.get("interlaced_requested", settings.get("interlaced"))))
            for combo, value in values:
                index = combo.findData(value)
                if index >= 0:
                    combo.blockSignals(True); combo.setCurrentIndex(index); combo.blockSignals(False)
            for control, key in ((self.scene_guard, "scene_guard"), (self.preserve_overlays, "preserve_overlays"),
                                 (self.color_stabilize, "color_stabilize"), (self.force_cpu, "force_cpu")):
                if key in settings:
                    control.blockSignals(True); control.setChecked(settings[key]); control.blockSignals(False)
            if "sensitivity" in settings:
                self.sensitivity.blockSignals(True)
                self.sensitivity.setValue(settings["sensitivity"])
                self.sensitivity.blockSignals(False)
                self.update_sensitivity()
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def resume_work(self):
        if self.jobs or not self.path:
            return
        if self.result is None or self.result.partial:
            self.pending_resume = True
            self.launch(False)
            return
        self.pending_resume = False
        self.save_review_decisions()
        # An existing master checkpoint records an already requested export.
        if master.has_checkpoint(self.result, self.output_directory(), self.export.currentData()):
            self.launch(True, review.frozen(self.result))
        else:
            self.render_all()

    def output_directory(self):
        return self.directory or str(Path(self.path).parent)

    def pick_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Ausgabeordner wählen", self.directory)
        if directory:
            self.directory = directory
            self.destination.setText("Ausgabe: " + directory)
            if self.path:
                self.remember_directory()
            if self.result:
                self.artifacts = review.artifacts(self.result, directory)
                self.refresh_list()

    def choose(self, path):
        if self.is_busy():
            return
        if not Path(path).is_file():
            QMessageBox.warning(self, "Keine Videodatei", "Bitte eine einzelne Videodatei auswählen.")
            return
        self.path, self.result, self.output, self.report = path, None, "", ""
        self.artifacts, self.rendering_keys = {}, set()
        self.pending_resume = False
        saved_directory = QSettings("VideoRepair", "VideoRepair").value(self.directory_key(), "")
        if saved_directory and Path(saved_directory).is_dir():
            self.directory = saved_directory
        self.destination.setText("Ausgabe: " + self.output_directory())
        self.restore_preferences()
        self.clear_preview()
        self.table.setRowCount(0)
        self.show_output.setEnabled(False)
        self.show_report.setEnabled(False)
        self.drop.setText("✓\n\n" + Path(path).name)
        self.progress.setValue(0)
        self.worker = None
        self.set_busy(False)
        self.status.setText("Datei geladen. Einstellungen wählen und ‚Scannen‘ drücken.")

    def launch(self, auto, result=None):
        if not self.path or self.is_busy():
            return
        self.original_timer.stop()
        self.pending_original = False
        directory = self.directory or str(Path(self.path).parent)
        self.refresh_timer.stop()
        if result:
            engine.refresh_scan(result, max_gap=self.max_gap.currentData(), **self.settings())
        else:
            self.result = None
            self.clear_preview()
            self.table.setRowCount(0)
        settings = self.settings()
        self.worker = Worker(self.path, directory, auto, self.mode.currentData(), self.max_gap.currentData(), result,
                             settings, self.export.currentData(), False, self.artifacts)
        self.worker.update.connect(self.update_progress)
        if not result:
            self.worker.partial.connect(self.accept_scan)
            self.worker.scanned.connect(self.accept_scan)
        self.worker.finished_job.connect(self.done)
        self.worker.failed.connect(self.error)
        self.worker.cancelled.connect(self.aborted)
        job = "master" if result else "scan"
        self.worker.finished.connect(lambda: self.release(job))
        self.jobs.add(job)
        self.set_busy(True)
        self.progress.setValue(0)
        self.status.setText("Reparatur startet …" if result else "Video wird analysiert …")
        self.worker.start()

    def set_busy(self, busy):
        busy = bool(self.jobs)
        if "master" in self.jobs:
            self.stop_playback()
        for widget in [self.drop, self.mode, self.max_gap, self.folder, self.sensitivity,
                       self.scene_guard, self.preserve_overlays, self.interlaced, self.export,
                       self.color_stabilize, self.force_cpu]:
            widget.setEnabled(not busy)
        self.sensitivity.setEnabled("master" not in self.jobs)
        self.table.setEnabled("master" not in self.jobs)
        self.cancel_button.setEnabled(busy)
        self.rescan_button.setEnabled(not busy and bool(self.path))
        self.resume_button.setEnabled(not busy and bool(self.path))
        complete = self.result is not None and not self.result.partial
        self.repair_button.setEnabled(not busy and complete and any(g.approved for g in self.result.gaps))
        self.render_all_button.setEnabled(not ({"scan", "render", "master"} & self.jobs) and complete and bool(self.result.gaps))
        self.enable_review("master" not in self.jobs)

    def accept_scan(self, result):
        current = self.current_gap()
        key = current.identity if current else None
        self.artifacts = review.restore_decisions(result, self.output_directory())
        if self.result:
            result.review_history.update(self.result.review_history)
            result.review_history.update({g.identity: g for g in self.result.gaps})
        engine.refresh_scan(result, max_gap=self.max_gap.currentData(), **self.settings())
        self.interlaced.blockSignals(True)
        self.interlaced.setItemText(0, "Automatisch · " + ("progressiv" if result.detected_field_order == "off" else result.detected_field_order.upper()))
        self.interlaced.blockSignals(False)
        self.field_info.setText(result.field_evidence)
        self.populate(result, key)
        self.set_busy(True)
        if not result.partial:
            self.save_review_decisions()

    def update_progress(self, percent, text):
        self.progress.setValue(percent)
        self.status.setText(text)

    def populate(self, result, key=None):
        self.result = result
        self.table.blockSignals(True)
        self.table.setRowCount(len(result.gaps))
        for row, gap in enumerate(result.gaps):
            values = [engine.event_timecode(result, gap.start), engine.EVENT_NAMES[gap.effective_kind], str(gap.length),
                      self.gap_status(gap)]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(engine.EVENT_NAMES[gap.effective_kind] + "\n" + gap.reason +
                    f"\nSchwarzanteil: {gap.black_fraction * 100:.1f} %")
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        if result.gaps:
            selected = next((i for i, g in enumerate(result.gaps) if g.identity == key), 0)
            self.table.selectRow(selected)
        self.select_event()

    def done(self, output, report):
        self.output, self.report = output, report
        self.progress.setValue(100)
        count = sum(g.approved for g in self.result.gaps)
        skipped = len(self.result.gaps) - count
        holes = sum(g.length > 0 for g in self.result.gaps)
        cuts = sum(g.effective_kind in ("cut", "cut_gap") for g in self.result.gaps)
        self.status.setText(f"Fertig: {count} Stellen repariert, {skipped} unverändert."
                            if output else f"Analyse fertig: {holes} Schwarzstellen, {cuts} Schnitt-Einträge. {count} Reparaturen geplant.")
        if output:
            for row, gap in enumerate(self.result.gaps):
                if gap.approved:
                    self.table.item(row, 3).setText("Repariert")
        self.show_output.setEnabled(bool(output))
        self.show_report.setEnabled(bool(report))

    def save_review_decisions(self):
        if self.result:
            try:
                review.save_decisions(self.result, self.output_directory())
                review.save_preferences(self.result, self.output_directory(), self.mode.currentData(), self.export.currentData())
                self.remember_directory()
            except OSError as exc:
                self.status.setText("Entscheidungen konnten nicht gespeichert werden: " + str(exc))

    def update_event_statuses(self):
        if not self.result:
            return
        for row, gap in enumerate(self.result.gaps):
            item = self.table.item(row, 3)
            if item:
                item.setText(self.gap_status(gap))

    def render_selected(self):
        gap = self.current_gap()
        if gap and not ({"render", "master"} & self.jobs):
            self.start_clip_job([gap], False)

    def render_all(self):
        if self.result and not self.result.partial and not ({"render", "master"} & self.jobs):
            # Cuts and preserved fades can be rendered individually for inspection.
            # The batch computes all corrections; it does not fill preserved events.
            events = [g for g in self.result.gaps if g.approved]
            if events:
                self.start_clip_job(events, True)
            else:
                self.status.setText("Keine Reparatur vorgemerkt. Erhaltene Stellen können einzeln als Prüfclip gespeichert werden.")

    def start_clip_job(self, gaps, ask):
        if self.refresh_timer.isActive():
            self.refresh_timer.stop()
            self.refresh_list()
            keys = {review.event_key(g) for g in gaps}
            gaps = [g for g in self.result.gaps if review.event_key(g) in keys]
        if not gaps:
            return
        self.save_review_decisions()
        snapshot = review.frozen(self.result)
        keys = {review.event_key(g) for g in gaps}
        selected = [g for g in snapshot.gaps if review.event_key(g) in keys]
        self.render_worker = ClipWorker(snapshot, selected, self.output_directory(),
            self.mode.currentData(), self.export.currentData())
        worker = self.render_worker
        worker.update.connect(self.update_progress)
        worker.event_progress.connect(self.render_progress.setValue)
        worker.event_started.connect(self.clip_started)
        worker.ready.connect(self.clip_ready)
        worker.failed.connect(self.error)
        worker.cancelled.connect(self.aborted)
        worker.completed.connect(lambda: self.clip_job_complete(ask))
        worker.finished.connect(lambda: self.release("render"))
        self.jobs.add("render")
        self.progress.setValue(0)
        self.progress.setFormat("Alle Prüfclips · %p %")
        self.render_progress.setValue(0)
        self.render_progress.setVisible(True)
        self.set_busy(True)
        worker.start()

    def clip_started(self, key):
        self.render_progress.setValue(0)
        self.rendering_keys = {key}
        self.update_event_statuses()

    def clip_ready(self, key, artifact):
        self.artifacts[key] = artifact
        self.rendering_keys.discard(key)
        self.update_event_statuses()
        gap = self.current_gap()
        if gap and review.event_key(gap) == key:
            self.clip_path.setText(artifact["clip"])
            self.pending_original = True
            self.original_timer.start(30)
        self.enable_review("master" not in self.jobs)

    def clip_job_complete(self, ask):
        self.ask_after_render = ask
        self.status.setText("Prüfclips gespeichert. Auswählen und begutachten; Gesamtdatei wird separat bestätigt.")

    def offer_master(self):
        if not self.result or self.result.partial or ({"render", "master", "scan"} & self.jobs):
            return
        approved = [g for g in self.result.gaps if g.approved]
        if not all(review.patch_matches(self.result, g, self.mode.currentData(),
                   self.artifacts.get(review.event_key(g))) for g in approved):
            self.status.setText("Prüfclips gespeichert. Der Plan wurde inzwischen geändert; betroffene Clips erneut rendern.")
            return
        # Let a pending preview finish before starting the complete export.
        if "preview" in self.jobs or self.pending_original:
            QTimer.singleShot(150, self.offer_master)
            return
        self.repair_now()

    def load_saved_clip(self):
        gap = self.current_gap()
        artifact = self.artifacts.get(review.event_key(gap)) if gap else None
        if artifact and artifact.get("clip") and Path(artifact["clip"]).is_file():
            self.start_preview(artifact=review.export_view(artifact, self.export.currentData()) or artifact)

    def open_saved_clip(self):
        gap = self.current_gap()
        artifact = self.artifacts.get(review.event_key(gap)) if gap else None
        if artifact and artifact.get("clip") and Path(artifact["clip"]).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(artifact["clip"]))

    def build_shortcuts(self):
        mapping = {"U": self.skip_event, "C": lambda: self.shortcut_type("cut_gap"),
            "L": lambda: self.shortcut_type("gap"),
            "A": lambda: self.move_event(-1), "D": lambda: self.move_event(1),
            "I": lambda: self.shortcut_mode("blend"), "O": lambda: self.shortcut_mode("flow"),
            "N": lambda: self.shortcut_mode("rife"), "Q": lambda: self.shortcut_mode("rife_hq"),
            "B": lambda: self.shortcut_type("fade"), "Return": self.render_selected,
            "Space": self.toggle_playback, "Left": lambda: self.step_frame(-1),
            "Right": lambda: self.step_frame(1), "H": self.show_shortcuts, "F1": self.show_shortcuts}
        self.shortcuts = []
        for key, action in mapping.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(action)
            self.shortcuts.append(shortcut)

    def skip_event(self):
        if self.decision.isEnabled():
            self.decision.setCurrentIndex(self.decision.findData("skip"))
            self.move_event(1)

    def shortcut_type(self, kind):
        gap = self.current_gap()
        if not gap or not self.event_type.isEnabled():
            return
        self.event_type.setCurrentIndex(self.event_type.findData(kind))
        if kind == "cut_gap":
            self.event_mode.setCurrentIndex(self.event_mode.findData("motion"))
            self.decision.setCurrentIndex(self.decision.findData("repair"))
        elif kind == "gap":
            self.decision.setCurrentIndex(self.decision.findData("repair"))
        else:
            self.decision.setCurrentIndex(self.decision.findData("skip"))

    def shortcut_mode(self, mode):
        gap = self.current_gap()
        if not gap or not self.event_mode.isEnabled():
            return
        self.event_type.setCurrentIndex(self.event_type.findData("gap"))
        self.event_mode.setCurrentIndex(self.event_mode.findData(mode))
        self.decision.setCurrentIndex(self.decision.findData("repair"))

    def show_shortcuts(self):
        QMessageBox.information(self, "Tastenkürzel / Ablauf",
            "Datei laden → Scannen → Ereignisse prüfen → Prüfclips rendern → Gesamtdatei bestätigen\n\n"
            "U   Ereignis überspringen und weiter\nL   Lücke setzen / reparieren\nC   Schnitt mit schwarzer Lücke · Bewegung verlängern\n"
            "A / D   Voriges / nächstes Ereignis\nI   Überblendung\nO   Optical Flow\n"
            "N   RIFE Standard\nQ   RIFE Qualität\nB   Blende / Schwarzphase erhalten\n"
            "Enter   Gewählten Punkt rendern und speichern\nLeertaste   Abspielen / Pause\n"
            "← / →   Bildweise navigieren\nH / F1   Dieses Infofenster\n\n"
            "Normale Schnitte haben 0 fehlende Frames. L setzt zunächst 1 Frame; den Bereich mit Von/Bis oder Start/Ende ← Bild anpassen.\n"
            "Maximale Lücke: bis 2 Sekunden. Änderung aktualisiert nur die Liste, ohne erneuten Scan.\n"
            "Am Schnitt: Bewegung aus bis zu 6 intakten Bildern derselben Szene weiterführen, dann harter Schnitt.\n"
            "Während Scan und Prüfclip-Rendering bleibt die Liste anklickbar. Die Warteschlange nutzt den Plan beim Start.\n"
            "Spätere Änderungen werden als ‚Plan geändert‘ markiert. Fertige Clips bleiben bei Abbruch erhalten.\n"
            "Fortsetzen nutzt dieselbe Quelle und denselben Zielordner. Nach Neustart wird einmal gescannt; fertige Exportabschnitte bleiben erhalten.\n"
            "10-Bit-SDR: FFV1/MKV oder ProRes/MOV. XDCAM-MXF bleibt 8 Bit; HDR wird nicht konvertiert.\n"
            "Prüfclips ohne Ton, mit ca. 1 Sekunde Vor-/Nachlauf. TC = Quell-Timecode; LaufzeitTC = Position ab Clipbeginn.")

    def release(self, job):
        self.jobs.discard(job)
        if job == "render":
            self.render_progress.setVisible(False)
            self.rendering_keys.clear()
            self.update_event_statuses()
        self.set_busy(False)
        if self.pending_resume and not self.closing and not self.jobs:
            QTimer.singleShot(50, self.resume_work)
        if not self.closing and self.pending_original and self.current_gap():
            self.original_timer.start(30)
        if job == "render" and self.ask_after_render:
            self.ask_after_render = False
            QTimer.singleShot(150, self.offer_master)

    def error(self, message):
        self.pending_resume = False
        self.ask_after_render = False
        self.status.setText("Verarbeitung fehlgeschlagen.")
        QMessageBox.warning(self, "Video Repair", message)

    def aborted(self):
        self.pending_resume = False
        self.ask_after_render = False
        self.status.setText("Abgebrochen. Fertige Prüfclips und Exportabschnitte bleiben gespeichert. Mit Fortsetzen weiterarbeiten.")

    def repair_now(self):
        if self.result and not self.result.partial and not self.is_busy():
            if not any(g.approved for g in self.result.gaps):
                self.status.setText("Keine Korrektur für die Gesamtdatei vorgemerkt.")
                return
            missing = [g for g in self.result.gaps if g.approved and not review.patch_matches(self.result, g,
                self.mode.currentData(), self.artifacts.get(review.event_key(g)))]
            if missing:
                self.start_clip_job(missing, True)
                return
            response = QMessageBox.question(self, "Gesamtdatei erstellen?",
                "Jetzt die vollständige Datei mit dem aktuellen Reparaturplan erstellen?\n"
                "Bereits gespeicherte Prüfclips bleiben unabhängig davon erhalten.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if response == QMessageBox.Yes:
                self.save_review_decisions()
                self.launch(True, review.frozen(self.result))

    def cancel(self):
        self.pending_resume = False
        self.ask_after_render = False
        for worker in (self.worker, self.preview_worker, self.render_worker):
            if worker and worker.isRunning():
                worker.stop.set()
            self.status.setText("Abbruch wird ausgeführt …")
            self.cancel_button.setEnabled(False)

    def closeEvent(self, event):
        if self.is_busy():
            self.closing = True
            self.original_timer.stop()
            self.refresh_timer.stop()
            self.pending_original = False
            self.cancel()
            for worker in (self.worker, self.preview_worker, self.render_worker):
                if worker and worker.isRunning():
                    worker.finished.connect(self.close)
            event.ignore()
        else:
            self.original_timer.stop()
            self.refresh_timer.stop()
            self.stop_playback()
            event.accept()


STYLE = """
QWidget { background: #111923; color: #e9eef4; font-size: 13px; }
QLabel#title { font-size: 32px; font-weight: 700; }
QLabel#muted { color: #97a8bc; font-size: 12px; }
QLabel#drop { border: 2px dashed #45718c; border-radius: 16px; background: #192936;
              font-size: 17px; color: #c4e7ed; padding: 12px; }
QPushButton { background: #253448; border: 1px solid #3e5067; border-radius: 8px; padding: 10px 16px; }
QPushButton:hover { background: #324962; }
QPushButton#primary { background: #2cc6aa; color: #072c26; font-weight: 700; }
QPushButton:disabled { color: #6b7d90; background: #1b2633; }
QPushButton#primary:disabled { color: #6b7d90; background: #1b2633; }
QComboBox { background: #253448; padding: 8px; border-radius: 7px; }
QProgressBar { border: 0; background: #253448; border-radius: 6px; text-align: center; height: 20px; }
QProgressBar::chunk { background: #219e90; border-radius: 6px; }
QTableWidget { background: #15202e; alternate-background-color: #192736; border: 1px solid #304358; }
QHeaderView::section { background: #253448; padding: 9px; border: 0; font-weight: 600; }
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Video Repair")
    app.setFont(QFont("Arial", 13))
    app.setStyleSheet(STYLE)
    window = Window()
    window.show()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        QTimer.singleShot(100, lambda: window.choose(sys.argv[1]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
