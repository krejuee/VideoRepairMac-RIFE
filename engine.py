"""Local black-gap repair. Original timestamps and compressed audio are preserved."""
from __future__ import annotations

import json
import os
import errno
import tempfile
import bisect
import shutil
import time
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from fractions import Fraction
from typing import Callable

import av
import cv2
import numpy as np
import media

cv2.setNumThreads(min(4, os.cpu_count() or 1))
MAX_GAP_SECONDS = Fraction(2)

# libswscale: BILINEAR | ACCURATE_RND | FULL_CHR_H_INT | FULL_CHR_H_INP.
# Accurate rounding and full horizontal chroma avoid the default conversion's
# systematic RGB darkening; no artistic brightness/gamma correction is applied.
COLOR_FLAGS = 2 | (1 << 18) | (1 << 13) | (1 << 14)
EVENT_NAMES = {"gap": "Lücke", "cut_gap": "Schnitt mit schwarzer Lücke",
               "cut": "Schnitt", "fade": "Blende / Schwarzphase"}


class Cancelled(Exception):
    pass


@dataclass
class Gap:
    start: int
    end: int
    seconds: float
    approved: bool = False
    reason: str = ""
    black_fraction: float = 1.0
    decision: str = "auto"
    mode: str = "global"
    auto_approved: bool | None = None
    overlays: bool | None = None
    kind: str = "gap"
    type_override: str = "auto"
    change_score: float = 0.0
    detected_start: int | None = None
    detected_end: int | None = None
    range_override: bool = False

    def __post_init__(self):
        if self.auto_approved is None:
            self.auto_approved = self.approved
        if self.detected_start is None:
            self.detected_start, self.detected_end = self.start, self.end

    @property
    def identity(self):
        return self.detected_start, self.detected_end

    @property
    def length(self):
        return self.end - self.start + 1

    @property
    def effective_kind(self):
        return self.kind if self.type_override == "auto" else self.type_override


@dataclass
class DetectionCache:
    thumbs: np.ndarray
    means: np.ndarray
    fractions: np.ndarray
    field_fractions: np.ndarray
    height: int
    color_thumbs: np.ndarray | None = None
    changes: np.ndarray | None = None


@dataclass
class Scan:
    source: str
    signature: tuple
    count: int
    rate: str
    gaps: list[Gap]
    timestamps: list[Fraction]
    durations: list[Fraction]
    settings: dict | None = None
    cache: DetectionCache | None = None
    review_history: dict = field(default_factory=dict)
    source_timecode: str | None = None
    partial: bool = False
    detected_field_order: str = "off"
    field_evidence: str = ""
    source_metadata: dict = field(default_factory=dict)
    bit_depth: int = 8
    pixel_format: str = ""


def gap_limit(rate, selection):
    if isinstance(selection, str) and selection.endswith("s"):
        seconds = Fraction(selection[:-1])
        if not 0 < seconds <= MAX_GAP_SECONDS:
            raise ValueError("Maximale Lücke muss zwischen 0 und 2 Sekunden liegen.")
        return max(1, int(Fraction(rate) * seconds)), float(seconds)
    frames = int(selection)
    if frames != selection or not 1 <= frames <= int(Fraction(rate) * MAX_GAP_SECONDS):
        raise ValueError("Maximale Lücke darf 2 Sekunden nicht überschreiten.")
    return frames, None


def gap_duration(result, gap):
    if gap.length <= 0:
        return Fraction(0)
    end = (result.timestamps[gap.end + 1] if gap.end + 1 < result.count else
           result.timestamps[gap.end] + result.durations[gap.end])
    return end - result.timestamps[gap.start]


def validate_length(result, gap):
    settings = result.settings or {}
    seconds = settings.get("max_gap_seconds")
    if gap.length < 1 or (seconds is None and gap.length > settings.get("max_gap", 12)):
        raise ValueError("Die Stelle ist länger als die eingestellte Maximallücke.")
    duration_limit = Fraction(str(seconds)) if seconds is not None else MAX_GAP_SECONDS
    if gap_duration(result, gap) > duration_limit:
        raise ValueError("Die Stelle ist länger als die eingestellte Maximallücke (höchstens 2 Sekunden).")


def detection_settings(sensitivity=0):
    """0 preserves the original strict detector; 100 tolerates title overlays."""
    if not 0 <= sensitivity <= 100:
        raise ValueError("Empfindlichkeit muss zwischen 0 und 100 liegen.")
    return {"sensitivity": sensitivity, "pixel_threshold": round(12 + 20 * sensitivity / 100),
            "min_black_fraction": .995 - .245 * sensitivity / 100}


def black_fraction(gray, settings):
    return float(np.count_nonzero(gray <= settings["pixel_threshold"]) / gray.size)


def threshold_fractions(gray):
    # One histogram supplies all 21 slider thresholds, without storing HD frames.
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
    return (np.cumsum(hist)[12:33] / gray.size).astype(np.float32)


def change_metrics(a, b, color_a=None, color_b=None):
    mae = float(np.mean(np.abs(a.astype(np.float32) - b.astype(np.float32))))
    ha = cv2.calcHist([a], [0], None, [32], [0, 256])
    hb = cv2.calcHist([b], [0], None, [32], [0, 256])
    corr = float(cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL))
    color_mae = (float(np.mean(np.abs(color_a.astype(np.float32) - color_b.astype(np.float32))))
                 if color_a is not None and color_b is not None else mae)
    return mae, corr, color_mae


def strong_change(metrics):
    mae, corr, color_mae = metrics
    distance = max(mae, color_mae)
    return (distance >= 50 and corr < .15) or (distance >= 60 and corr < .55) or (distance >= 90 and corr < .8) or (color_mae >= 65 and mae < 15)


def likely_cut(cache, a, b):
    metrics = pair_metrics(cache, a, b)
    if not strong_change(metrics):
        return False
    flank = [pair_metrics(cache, i, i + 1) for i in (a - 1, b) if 0 <= i < len(cache.thumbs) - 1]
    baseline = max((max(m[0], m[2]) for m in flank), default=0)
    return max(metrics[0], metrics[2]) >= max(50 if metrics[1] < .15 else 60, baseline * 2.5 * min(4, b - a))


def pair_metrics(cache, a, b):
    colors = cache.color_thumbs
    return change_metrics(cache.thumbs[a], cache.thumbs[b],
        colors[a] if colors is not None else None, colors[b] if colors is not None else None)


def apply_decision(result, gap):
    gap.approved = False
    if gap.length == 0 or gap.effective_kind == "cut" or gap.decision == "skip":
        return
    if gap.decision == "repair" or (gap.decision == "auto" and gap.type_override in ("gap", "cut_gap")):
        try:
            validate_gap(result, gap)
        except ValueError:
            return
        gap.approved = True
    elif gap.decision == "auto" and gap.type_override == "auto":
        try:
            validate_gap(result, gap)
            gap.approved = bool(gap.auto_approved)
        except ValueError:
            pass


def refresh_scan(result, **updates):
    """Rebuild detected runs and heuristic decisions using only cached statistics."""
    if result.cache is None:
        raise ValueError("Analysewerte fehlen. Dieses Video muss einmal analysiert werden.")
    settings = dict(result.settings or {}) | updates
    settings.update(detection_settings(settings.get("sensitivity", 0)))
    selection = updates.get("max_gap", settings.get("max_gap_selection", settings.get("max_gap", 6)))
    max_gap, seconds = gap_limit(result.rate, selection)
    settings.update(max_gap=max_gap, max_gap_seconds=seconds, max_gap_selection=selection)
    count_limit = result.count if seconds is not None else max_gap
    field_order = settings.get("interlaced", "off")
    if field_order == "auto":
        field_order = result.detected_field_order
    settings["interlaced_requested"] = updates.get("interlaced", settings.get("interlaced_requested", field_order))
    settings["interlaced"] = field_order
    if field_order not in ("off", "tff", "bff"):
        raise ValueError("Ungültige Erkennungseinstellungen.")
    if field_order != "off" and result.cache.height % 2:
        raise ValueError("Interlaced benötigt eine gerade Bildhöhe.")
    fractions = (result.cache.fractions if field_order == "off" else result.cache.field_fractions)
    values = fractions[:, settings["pixel_threshold"] - 12]
    black = values >= settings["min_black_fraction"]
    edges = np.diff(np.r_[False, black, False].astype(np.int8))
    result.review_history.update({gap.identity: gap for gap in result.gaps})
    old = result.review_history
    result.settings = settings
    gaps = []
    for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1) - 1):
        start, end = int(start), int(end)
        if result.partial and end >= result.count - 10:
            continue  # Wait for the reference and fade context, never expose an open run.
        gap = Gap(start, end, float(result.timestamps[start] - result.timestamps[0]),
                  black_fraction=float(values[start:end + 1].min()))
        gap.approved, gap.reason = classify(gap, result.cache.thumbs, result.cache.means,
                                           count_limit, settings.get("scene_guard", True))
        if start > 0 and end + 1 < result.count:
            metrics = pair_metrics(result.cache, start - 1, end + 1)
            gap.change_score = max(metrics[0], metrics[2]) / 255
            if likely_cut(result.cache, start - 1, end + 1):
                gap.kind = "cut_gap"
                gap.reason = "Starker Bildwechsel vor/nach Schwarzphase → Schnitt mit schwarzer Lücke"
                gap.approved = gap.length <= count_limit and start >= 2
            elif "blende" in gap.reason.lower() or gap.length > count_limit:
                gap.kind = "fade"
        try:
            validate_length(result, gap)
        except ValueError:
            gap.approved, gap.kind, gap.reason = False, "fade", "Längere Schwarzphase · überschreitet die eingestellte Maximallücke"
        gap.auto_approved = gap.approved
        previous = old.get((start, end))
        if previous:
            gap.mode, gap.overlays = previous.mode, previous.overlays
            gap.decision, gap.type_override = previous.decision, previous.type_override
            if previous.range_override and previous.end < result.count:
                gap.start, gap.end, gap.range_override = previous.start, previous.end, True
                gap.seconds = float(result.timestamps[gap.start] - result.timestamps[0])
        # Validation uses the newly selected maximum length.
        gap.approved = False
        apply_decision(result, gap)
        gaps.append(gap)
    changes = result.cache.changes
    if changes is None:
        changes = np.asarray([(0., 1., 0.)] + [pair_metrics(result.cache, i - 1, i)
                                              for i in range(1, result.count)], np.float32)
        result.cache.changes = changes
    for index in range(1, result.count):
        if black[index - 1] or black[index] or not likely_cut(result.cache, index - 1, index):
            continue
        # A cut is a boundary: start > end, zero replacement frames.
        event = Gap(index, index - 1, float(result.timestamps[index] - result.timestamps[0]),
            reason="Starker Bildwechsel zwischen benachbarten Bildern · Schnitt bleibt erhalten",
            black_fraction=float(max(values[index - 1], values[index])), kind="cut",
            change_score=float(max(changes[index][0], changes[index][2]) / 255))
        previous = old.get((event.start, event.end))
        if previous:
            event.decision, event.type_override = previous.decision, previous.type_override
            event.mode, event.overlays = previous.mode, previous.overlays
            if previous.range_override and previous.end < result.count:
                event.start, event.end, event.range_override = previous.start, previous.end, True
                event.seconds = float(result.timestamps[event.start] - result.timestamps[0])
            apply_decision(result, event)
        gaps.append(event)
    gaps.sort(key=lambda event: (event.start, event.end))
    result.settings, result.gaps = settings, gaps
    return result


def signature(path):
    st = Path(path).stat()
    return st.st_size, st.st_mtime_ns


def check(cancel):
    if cancel():
        raise Cancelled("Abgebrochen")


def timecode(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}"


def valid_timecode(value, rate):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}:\d{2}[:;]\d{2}", value):
        return False
    h, m, s, f = map(int, re.split("[:;]", value))
    nominal = round(float(Fraction(rate)))
    if h >= 24 or m >= 60 or s >= 60 or f >= nominal:
        return False
    if ";" in value:
        if Fraction(rate) not in (Fraction(30000, 1001), Fraction(60000, 1001)):
            return False
        if m % 10 and s == 0 and f < (2 if nominal == 30 else 4):
            return False
    return True


def smpte_timecode(rate, frame, origin="00:00:00:00"):
    """Add frame offsets, including 29.97/59.94 drop-frame and 24h wrap."""
    rate = Fraction(rate)
    if not valid_timecode(origin, rate):
        origin = "00:00:00:00"
    nominal = round(float(rate))
    h, m, s, f = map(int, re.split("[:;]", origin))
    drop = (2 if nominal == 30 else 4) if ";" in origin else 0
    start = ((h * 3600 + m * 60 + s) * nominal + f) - drop * (h * 60 + m - (h * 60 + m) // 10)
    frames = start + frame
    if drop:
        minute = nominal * 60 - drop
        ten_minutes = nominal * 600 - drop * 9
        frames %= ten_minutes * 6 * 24
        blocks, rest = divmod(frames, ten_minutes)
        frames += drop * 9 * blocks + drop * max(0, (rest - drop) // minute)
    else:
        frames %= nominal * 86400
    seconds, f = divmod(frames, nominal)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}{';' if drop else ':'}{f:02}"


def event_timecode(result, frame):
    return smpte_timecode(result.rate, frame, result.source_timecode or "00:00:00:00")


def clone_frame(frame):
    copy = av.VideoFrame(frame.width, frame.height, frame.format.name)
    for src, dst in zip(frame.planes, copy.planes):
        rows = np.frombuffer(src, np.uint8).reshape(src.height, src.line_size)
        target = np.zeros((dst.height, dst.line_size), np.uint8)
        target[:, :min(src.line_size, dst.line_size)] = rows[:, :min(src.line_size, dst.line_size)]
        dst.update(target.tobytes())
    for attr in ("pts", "time_base", "duration", "colorspace", "color_range"):
        setattr(copy, attr, getattr(frame, attr))
    return copy


def validate_mxf(result, width, height):
    if (width, height) not in ((1920, 1080), (1280, 720)):
        raise ValueError("MXF/XDCAM HD422 benötigt 1920×1080 oder 1280×720. Bitte MOV oder MKV wählen.")
    rate = Fraction(result.rate)
    step = 1 / rate
    if any(abs((b - a) - step) > Fraction(1, 1000000) for a, b in zip(result.timestamps, result.timestamps[1:])):
        raise ValueError("MXF benötigt eine konstante Bildrate. Für variable Bildraten MOV/MKV verwenden.")
    if rate not in map(Fraction, ("25", "30", "30000/1001", "50", "60000/1001")):
        raise ValueError("Diese Bildrate wird vom MXF/XDCAM-Profil nicht unterstützt.")
    if height == 1080 and rate > 30:
        raise ValueError("MXF/XDCAM 1080 unterstützt hier bis 30 Vollbilder/s. 1080i25 mit TFF ist möglich.")
    if height == 720 and (result.settings or {}).get("interlaced", "off") != "off":
        raise ValueError("720-Zeilen-MXF wird nur progressiv unterstützt.")


def configure_mxf(stream, field_order):
    stream.pix_fmt = "yuv422p"
    stream.bit_rate = 50_000_000
    stream.codec_context.time_base = 1 / stream.average_rate
    stream.options = {"minrate": "50000000", "maxrate": "50000000", "bufsize": "17825792",
                      "g": "12", "bf": "2", "profile": "0", "qmin": "1", "qmax": "12"}
    if field_order != "off":
        stream.options |= {"flags": "+ildct+ilme", "field_order": "tt" if field_order == "tff" else "bb",
                           "top": "1" if field_order == "tff" else "0"}


class FieldTagger:
    """setfield changes AVFrame flags only; it never moves or blends pixels."""
    def __init__(self, order):
        self.order, self.graph = order, None

    def __call__(self, frame):
        if self.graph is None:
            self.graph = av.filter.Graph()
            source = self.graph.add_buffer(template=frame)
            field_filter = self.graph.add("setfield", "prog" if self.order == "off" else self.order)
            source.link_to(field_filter)
            field_filter.link_to(self.graph.add("buffersink"))
            self.graph.configure()
        self.graph.push(frame)
        return self.graph.pull()


def classify(gap, thumbs, means, max_gap=6, scene_guard=True):
    a, b = gap.start - 1, gap.end + 1
    if a < 0 or b >= len(thumbs):
        return False, "Kein intaktes Bild auf beiden Seiten"
    if gap.length > max_gap:
        return False, "Längere Schwarzphase"
    if not scene_guard:
        return True, "Freigegeben: Szenenprüfung ausgeschaltet"
    if min(means[a], means[b]) < 20:
        return False, "Dunkle Nachbarbilder / mögliche Schwarzblende"
    left = np.asarray(thumbs[a], np.float32)
    right = np.asarray(thumbs[b], np.float32)
    # Both structure and luminance distributions must be plausible.
    mae = float(np.mean(np.abs(left - right)))
    ha = cv2.calcHist([thumbs[a]], [0], None, [32], [0, 256])
    hb = cv2.calcHist([thumbs[b]], [0], None, [32], [0, 256])
    corr = cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL)
    if strong_change((mae, corr, mae)):
        return False, "Möglicher Szenenwechsel oder starke Bewegung"
    # Look ten frames into both flanks; strong monotonic fading is ambiguous.
    before = np.array(means[max(0, a - 9):a + 1])
    after = np.array(means[b:min(len(means), b + 10)])
    if (len(before) > 3 and before[0] - before[-1] > 18) or (
        len(after) > 3 and after[-1] - after[0] > 18
    ):
        return False, "Mögliche Ab- oder Aufblende"
    return True, "Kurze Lücke, Nachbarbilder ähnlich"


def frame_field_order(frame):
    if not frame.interlaced_frame:
        return "off"
    graph = av.filter.Graph()
    source = graph.add_buffer(template=frame)
    select = graph.add("select", "eq(interlace_type,TOPFIRST)")
    source.link_to(select); select.link_to(graph.add("buffersink"))
    graph.configure(); graph.push(frame); graph.push(None)
    try:
        graph.pull()
        return "tff"
    except (av.error.BlockingIOError, av.error.EOFError):
        return "bff"


def metadata_snapshot(container):
    return {"container": dict(container.metadata), "streams": [
        {"index": s.index, "type": s.type, "metadata": dict(s.metadata),
         "codec": getattr(s.codec_context, "name", None), "time_base": str(s.time_base)} for s in container.streams]}


def copy_tags(metadata):
    generated = {"encoder", "duration", "nb_frames", "major_brand", "minor_version", "compatible_brands",
                 "file_package_umid", "material_package_umid", "instance_uid"}
    return {k: v for k, v in metadata.items() if k.lower() not in generated}


def scan(path, max_gap=6, progress=lambda p, s: None, cancel=lambda: False,
         sensitivity=0, scene_guard=True, interlaced="auto", preserve_overlays=False,
         color_stabilize=True, force_cpu=False, on_partial=None):
    if interlaced not in ("auto", "off", "tff", "bff"):
        raise ValueError("Unbekannte Halbbildreihenfolge.")
    settings = detection_settings(sensitivity) | {"max_gap": max_gap,
        "scene_guard": scene_guard, "interlaced": interlaced, "preserve_overlays": False,
        "color_stabilize": color_stabilize, "force_cpu": force_cpu}
    path = str(Path(path).resolve())
    original_signature = signature(path)
    thumbs, means, times, durations, fractions, field_fractions, color_thumbs, changes = [], [], [], [], [], [], [], []
    with av.open(path) as container:
        if not container.streams.video:
            raise ValueError("Die Datei enthält keine Videospur.")
        if len(container.streams.video) != 1:
            raise ValueError("Bitte eine Datei mit genau einer Videospur verwenden.")
        stream = container.streams.video[0]
        rate = stream.average_rate or stream.guessed_rate or Fraction(25)
        gap_limit(rate, max_gap)
        metadata = [{key.lower(): value for key, value in m.items()} for m in
                    [stream.metadata, container.metadata, *[s.metadata for s in container.streams]]]
        source_tc = next((m.get("timecode") for m in metadata if valid_timecode(m.get("timecode"), rate)), None)
        source_meta = metadata_snapshot(container)
        detected_order, evidence, seen_orders = "off", "Dekodierte Bilder: progressiv", set()
        source_depth, source_format = 8, ""
        total = stream.frames or int((container.duration or 0) / av.time_base * float(rate))
        previous = None
        last_partial = time.monotonic()

        def snapshot(partial):
            value = Scan(path, original_signature, len(times), str(rate), [], list(times), list(durations), dict(settings),
                DetectionCache(np.asarray(thumbs), np.asarray(means), np.asarray(fractions), np.asarray(field_fractions),
                               gray.shape[0], np.asarray(color_thumbs), np.asarray(changes, np.float32)),
                source_timecode=source_tc, partial=partial, detected_field_order=detected_order,
                field_evidence=evidence, source_metadata=source_meta,
                bit_depth=source_depth, pixel_format=source_format)
            return refresh_scan(value)
        for index, frame in enumerate(container.decode(stream)):
            check(cancel)
            if index < 12 or (frame.interlaced_frame and not seen_orders) or index % 250 == 0:
                order = frame_field_order(frame)
                if order != "off":
                    seen_orders.add(order); detected_order = order
                    evidence = "Decoder-Kennzeichen: " + order.upper() + " (Halbbildreihenfolge)"
                if len(seen_orders) > 1 and interlaced == "auto":
                    raise ValueError("Wechselnde TFF/BFF-Kennzeichen. Bitte Halbbildreihenfolge manuell wählen.")
            if stream.metadata.get("rotate", "0") not in ("0", "0.0", "") or any(
                "DISPLAYMATRIX" in str(side.type) for side in frame.side_data
            ):
                raise ValueError("Gedrehtes Video: bitte zuerst die Ausrichtung fest ins Bild exportieren.")
            depth = media.bit_depth(frame)
            if depth not in (8, 10):
                raise ValueError("Unterstützt werden SDR-Videos mit 8 oder 10 Bit.")
            if stream.codec_context.color_trc in (16, 18):
                raise ValueError("HDR/PQ/HLG wird nicht konvertiert. Unterstützt wird SDR mit 8 oder 10 Bit.")
            if source_format and (source_format != frame.format.name or source_depth != depth):
                raise ValueError("Das Pixelformat wechselt innerhalb der Quelle.")
            source_depth, source_format = depth, frame.format.name
            if frame.pts is None:
                raise ValueError("Fehlende Bild-Zeitstempel; sichere Synchronität ist nicht möglich.")
            timestamp = frame.pts * frame.time_base
            if previous is not None and timestamp <= previous:
                raise ValueError("Nicht aufsteigende Bild-Zeitstempel.")
            previous = timestamp
            times.append(timestamp)
            durations.append(frame.duration * frame.time_base if frame.duration else Fraction(1, 1) / rate)
            gray = frame.to_ndarray(format="gray")
            if (detected_order if interlaced == "auto" else interlaced) != "off" and gray.shape[0] % 2:
                raise ValueError("Interlaced benötigt eine gerade Bildhöhe.")
            fractions.append(threshold_fractions(gray))
            # An isolated damaged field must also be part of the run. Otherwise
            # it would become a black reference for the next missing field.
            field_fractions.append(np.maximum(threshold_fractions(gray[0::2]), threshold_fractions(gray[1::2])))
            thumbs.append(cv2.resize(gray, (48, 27), interpolation=cv2.INTER_AREA))
            matrix, value_range = color_spec(frame, stream.codec_context)
            color_thumbs.append(frame.reformat(width=32, height=18, format="rgb24", src_colorspace=matrix,
                dst_colorspace=5 if matrix != 5 else 1, src_color_range=value_range,
                dst_color_range=2, interpolation=COLOR_FLAGS).to_ndarray())
            changes.append(change_metrics(thumbs[-2], thumbs[-1], color_thumbs[-2], color_thumbs[-1])
                           if index else (0., 1., 0.))
            means.append(float(gray.mean()))
            if on_partial and index >= 24 and (index == 24 or time.monotonic() - last_partial >= .75):
                on_partial(snapshot(True))
                last_partial = time.monotonic()
            if index % 50 == 0:
                progress(min(99, int(100 * index / max(1, total))), f"Analysiere Bild {index + 1:,}")
        if not times:
            raise ValueError("Keine dekodierbaren Videobilder gefunden.")
    if signature(path) != original_signature:
        raise ValueError("Die Quelldatei wurde während der Analyse verändert.")
    result = snapshot(False)
    progress(100, f"{sum(g.approved for g in result.gaps)} reparierbare Lücken gefunden")
    return result


def interpolate(left, right, count, mode="flow", alphas=None):
    """Bidirectional Farneback interpolation; no model download or generative AI."""
    if mode == "hold":
        return [left.copy() for _ in range(count)]
    if mode == "nearest":
        return [(left if t < .5 else right).copy() for t in
                (alphas if alphas is not None else [k / (count + 1) for k in range(1, count + 1)])]
    if mode == "blend":
        return [cv2.addWeighted(left, 1 - t, right, t, 0)
                for t in (alphas if alphas is not None else [k / (count + 1) for k in range(1, count + 1)])]
    h, w = left.shape[:2]
    if np.mean(np.abs(left.astype(np.float32) - right.astype(np.float32))) < 1:
        return [left.copy() for _ in range(count)]
    scale = min(1., 960 / max(h, w))
    size = (max(16, round(w * scale)), max(16, round(h * scale)))
    gl = cv2.resize(media.gray8(left), size)
    gr = cv2.resize(media.gray8(right), size)
    fwd = cv2.calcOpticalFlowFarneback(gl, gr, None, .5, 5, 25, 4, 7, 1.5, 0)
    bwd = cv2.calcOpticalFlowFarneback(gr, gl, None, .5, 5, 25, 4, 7, 1.5, 0)
    fwd = cv2.resize(fwd, (w, h)) * np.array([w / size[0], h / size[1]], np.float32)
    bwd = cv2.resize(bwd, (w, h)) * np.array([w / size[0], h / size[1]], np.float32)
    xx, yy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    result = []
    for alpha in (alphas if alphas is not None else [k / (count + 1) for k in range(1, count + 1)]):
        la = cv2.remap(left, xx - alpha * fwd[..., 0], yy - alpha * fwd[..., 1],
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        rb = cv2.remap(right, xx - (1 - alpha) * bwd[..., 0], yy - (1 - alpha) * bwd[..., 1],
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        result.append(cv2.addWeighted(la, 1 - alpha, rb, alpha, 0))
    return result


def reconstruct(left, right, alphas, generate, field_order="off", left_duration=0,
                right_duration=0, durations=None, span=1, left_fields=None, right_fields=None):
    """Interpolate equal-parity fields independently, then weave them back."""
    if field_order == "off":
        return generate(left, right, alphas)
    if left.shape[0] % 2 or right.shape != left.shape:
        raise ValueError("Interlaced benötigt gleich große Bilder mit gerader Höhe.")
    first = 0 if field_order == "tff" else 1
    result = [np.empty_like(left) for _ in alphas]
    for parity in (0, 1):
        positions = alphas
        if parity != first and durations:
            # The second field occurs half a frame later; preserve that on VFR inputs.
            denominator = span + (right_duration - left_duration) / 2
            positions = [(alpha * span + (duration - left_duration) / 2) / denominator
                         for alpha, duration in zip(alphas, durations)]
        if any(not 0 < pos < 1 for pos in positions):
            raise ValueError("Ungültige Halbbild-Zeitstempel.")
        a = left_fields[parity] if left_fields is not None else np.ascontiguousarray(left[parity::2])
        b = right_fields[parity] if right_fields is not None else np.ascontiguousarray(right[parity::2])
        images = generate(a, b, positions)
        if len(images) != len(result):
            raise ValueError("Falsche Anzahl rekonstruierter Halbbilder.")
        for dst, image in zip(result, images):
            dst[parity::2] = image
    return result


def color_spec(frame, context=None):
    """Keep declared matrix/range; unspecified matrix follows swscale's 601 default.

    Use the SAME fallback in both directions, never infer a different HD matrix
    only for replacement frames. Full-range YUVJ and RGB must stay full-range.
    """
    matrix = frame.colorspace
    if matrix in (0, 2):
        matrix = getattr(context, "colorspace", 2)
    if matrix in (0, 2):
        matrix = 5
    value_range = frame.color_range or getattr(context, "color_range", 0)
    if not value_range:
        value_range = 2 if frame.format.name.startswith(("yuvj", "rgb", "bgr")) else 1
    return matrix, value_range


def frame_rgb(frame, field_order="off", context=None):
    if field_order != "off":
        fields = split_native_fields(frame, context)
        rgb = np.empty((frame.height, frame.width, 3), fields[0].dtype)
        rgb[0::2], rgb[1::2] = fields
        return rgb
    matrix, value_range = color_spec(frame, context)
    target = media.rgb_target(frame)
    if frame.format.name == target:
        return frame.to_ndarray()
    # PyAV 16 skips sws_setColorspaceDetails when both requested matrix/range
    # are equal. On the RGB side the matrix is irrelevant; make it different
    # to ensure the declared YUV matrix AND range are actually installed.
    return frame.reformat(format=target, src_colorspace=matrix,
        dst_colorspace=5 if matrix != 5 else 1,
        src_color_range=value_range, dst_color_range=2, interpolation=COLOR_FLAGS).to_ndarray()


def split_native_fields(frame, context=None):
    """Split luma AND chroma before RGB conversion (essential for interlaced 4:2:0)."""
    fmt = frame.format.name
    if fmt not in media.YUV:
        rgb = frame_rgb(frame, context=context)
        return [np.ascontiguousarray(rgb[p::2]) for p in (0, 1)]
    if "420" in fmt and frame.height % 4:
        raise ValueError("Interlaced 4:2:0 benötigt eine durch vier teilbare Bildhöhe.")
    fields = []
    for parity in (0, 1):
        field = av.VideoFrame(frame.width, frame.height // 2, fmt)
        for src, dst in zip(frame.planes, field.planes):
            rows = np.frombuffer(src, np.uint8).reshape(src.height, src.line_size)
            target = np.zeros((dst.height, dst.line_size), np.uint8)
            width = media.active_bytes(field, dst)
            target[:, :width] = rows[parity::2, :width]
            dst.update(target.tobytes())
        matrix, value_range = color_spec(frame, context)
        field.colorspace, field.color_range = matrix, value_range
        rgb = field.reformat(format=media.rgb_target(frame), src_colorspace=matrix,
            dst_colorspace=5 if matrix != 5 else 1,
            src_color_range=value_range, dst_color_range=2, interpolation=COLOR_FLAGS)
        fields.append(rgb.to_ndarray())
    return fields


def weave_native_fields(pixels, fmt, colorspace, color_range):
    """Convert each RGB field separately, preserving interlaced chroma placement."""
    if fmt not in media.YUV:
        raise ValueError("Interlaced-Export benötigt ein planares YUV-Pixelformat.")
    frame = av.VideoFrame(pixels.shape[1], pixels.shape[0], fmt)
    for parity in (0, 1):
        field = av.VideoFrame.from_ndarray(np.ascontiguousarray(pixels[parity::2]), format=media.rgb_format(pixels))
        field = field.reformat(format=fmt, src_colorspace=5 if colorspace != 5 else 1,
                               dst_colorspace=colorspace,
                               src_color_range=2, dst_color_range=color_range or 1, interpolation=COLOR_FLAGS)
        for src, dst in zip(field.planes, frame.planes):
            rows = np.frombuffer(src, np.uint8).reshape(src.height, src.line_size)
            target = np.frombuffer(dst, np.uint8).reshape(dst.height, dst.line_size).copy()
            width = media.active_bytes(field, src)
            target[parity::2, :width] = rows[:, :width]
            dst.update(target.tobytes())
    frame.colorspace, frame.color_range = colorspace, color_range or 1
    return frame


def replacement_frame(original, pixels, fmt, field_order="off", context=None, untouched_fields=()):
    matrix, value_range = color_spec(original, context)
    if field_order != "off":
        replacement = weave_native_fields(pixels, fmt, matrix, value_range)
    else:
        replacement = av.VideoFrame.from_ndarray(np.ascontiguousarray(pixels), format=media.rgb_format(pixels))
        replacement = replacement.reformat(format=fmt, dst_colorspace=matrix,
            src_colorspace=5 if matrix != 5 else 1,
            src_color_range=2, dst_color_range=value_range, interpolation=COLOR_FLAGS)
    if untouched_fields and fmt == original.format.name:
        for src, dst in zip(original.planes, replacement.planes):
            source = np.frombuffer(src, np.uint8).reshape(src.height, src.line_size)
            target = np.frombuffer(dst, np.uint8).reshape(dst.height, dst.line_size).copy()
            for parity in untouched_fields:
                width = media.active_bytes(original, src)
                target[parity::2, :width] = source[parity::2, :width]
            dst.update(target.tobytes())
    replacement.colorspace, replacement.color_range = matrix, value_range
    replacement.pts, replacement.time_base, replacement.duration = original.pts, original.time_base, original.duration
    return replacement


def intact_fields(original, settings):
    if (settings or {}).get("interlaced", "off") == "off":
        return ()
    gray = original.to_ndarray(format="gray")
    settings = settings or detection_settings()
    return tuple(p for p in (0, 1) if black_fraction(gray[p::2], settings) < settings.get("min_black_fraction", .995))


def set_gap_decision(result, gap, decision):
    if decision not in ("auto", "repair", "skip"):
        raise ValueError("Unbekannte Einzelentscheidung.")
    if decision == "repair":
        validate_gap(result, gap)
    gap.decision = decision
    apply_decision(result, gap)


def set_event_type(result, gap, kind):
    if kind not in ("auto", *EVENT_NAMES):
        raise ValueError("Unbekannter Korrekturtyp.")
    if gap.length == 0 and kind not in ("auto", "cut"):
        set_event_range(result, gap, gap.start, gap.start)
    if kind == "gap" and gap.decision != "skip":
        validate_gap(result, gap)
    gap.type_override = kind
    apply_decision(result, gap)


def set_event_range(result, gap, start, end):
    if not 0 <= start <= end < result.count:
        raise ValueError("Bitte einen gültigen Framebereich innerhalb der Quelle wählen.")
    candidate = Gap(start, end, 0)
    validate_length(result, candidate)
    if any(other is not gap and other.approved and start <= other.end and end >= other.start for other in result.gaps):
        raise ValueError("Dieser Bereich überschneidet sich mit einer anderen vorgemerkten Reparatur.")
    gap.start, gap.end, gap.range_override = start, end, True
    gap.seconds = float(result.timestamps[start] - result.timestamps[0])
    apply_decision(result, gap)


def validate_gap(result, gap):
    if gap.length == 0:
        raise ValueError("Ein normaler Schnitt enthält keine fehlenden Bilder und braucht keine Reparatur.")
    if gap.start < 1 or gap.end >= result.count - 1:
        raise ValueError("Für die Reparatur fehlen intakte Referenzbilder auf beiden Seiten.")
    validate_length(result, gap)


def resolved_mode(gap, global_mode):
    if gap.effective_kind == "cut_gap":
        return "hold" if gap.mode == "hold" else "motion"
    return global_mode if gap.mode == "global" else gap.mode


def motion_history_start(result, gap):
    start = gap.start - 1
    while start > max(0, gap.start - 6):
        index = start - 1
        if result.cache is not None:
            fractions = result.cache.field_fractions if result.settings.get("interlaced") != "off" else result.cache.fractions
            if fractions[index, result.settings.get("pixel_threshold", 12) - 12] >= result.settings.get("min_black_fraction", .995) or likely_cut(result.cache, index, index + 1):
                break
        start -= 1
    return start


def motion_history(result, gap, cancel=lambda: False):
    frames = read_window(result.source, result.timestamps, motion_history_start(result, gap), gap.start - 1, cancel)
    if len(frames) < 2:
        raise ValueError("Bewegung verlängern benötigt mindestens zwei intakte Bilder derselben Szene vor der Lücke. Bitte Bereich prüfen oder Standbild wählen.")
    return frames


def extend_motion(history, left, positions, intervals, cancel=lambda: False, progress=lambda p: None):
    h, w = left.shape[:2]
    scale = min(1., 960 / max(h, w))
    size = (max(16, round(w * scale)), max(16, round(h * scale)))
    gray = [cv2.resize(media.gray8(x), size) for x in history]
    xx, yy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    mx, my = xx.copy(), yy.copy()
    samples = []
    for index in range(len(gray) - 1, 0, -1):
        check(cancel)
        flow = cv2.calcOpticalFlowFarneback(gray[index], gray[index - 1], None, .5, 5, 25, 4, 7, 1.5, 0)
        flow = cv2.resize(flow, (w, h)) * np.array([w / size[0], h / size[1]], np.float32)
        sampled = cv2.remap(flow, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        samples.append(sampled / intervals[index - 1])
        mx += sampled[..., 0]; my += sampled[..., 1]
    velocity = np.median(np.stack(samples), axis=0).astype(np.float32)
    images = []
    for i, distance in enumerate(positions):
        check(cancel)
        images.append(cv2.remap(left, xx + velocity[..., 0] * distance, yy + velocity[..., 1] * distance,
            cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
        progress((i + 1) / len(positions))
    return images


def gentle_color_match(image, left, right, alpha):
    """Only small uniform chroma offsets, gated on consistent flanking colours.

    No brightness/contrast/gain correction, no grading of intact frames.
    """
    def mean(rgb):
        sample = rgb[::4, ::4].astype(np.float32)
        return sample.mean(axis=(0, 1)) * (255 / media.maximum(rgb))
    weights = np.array([.2126, .7152, .0722], np.float32)
    a, b, m = mean(left), mean(right), mean(image)
    flank_delta = b - a
    if abs(flank_delta @ weights) > 12 or np.max(np.abs(flank_delta - flank_delta @ weights)) > 8:
        return image
    delta = a * (1 - alpha) + b * alpha - m
    delta -= delta @ weights
    if np.max(np.abs(delta)) < .6:
        return image
    delta *= min(.75, 2. / max(float(np.max(np.abs(delta))), .001))
    maximum = media.maximum(image)
    return np.rint(np.clip(image.astype(np.float32) + delta * (maximum / 255), 0, maximum)).astype(image.dtype)


class Renderer:
    """Shared inference path for a single preview and the complete export."""
    def __init__(self, settings=None, progress=lambda p, s: None, cancel=lambda: False):
        self.settings, self.progress, self.cancel = settings or {}, progress, cancel
        self.rife = None
        self.motion_frames = []
        self.inference_percent = 5

    def render(self, left_frame, right_frame, pending, gap, global_mode):
        mode = resolved_mode(gap, global_mode)
        if mode not in ("rife", "rife_hq", "flow", "hold", "blend", "nearest", "motion"):
            raise ValueError("Unbekannter Reparaturmodus.")
        field_order = self.settings.get("interlaced", "off")
        t0, t1 = left_frame.pts * left_frame.time_base, right_frame.pts * right_frame.time_base
        alphas = [float((f.pts * f.time_base - t0) / (t1 - t0)) for f in pending]
        left, right = frame_rgb(left_frame, field_order), frame_rgb(right_frame, field_order)
        completed, total = 0, len(pending) * (2 if field_order != "off" else 1)
        self.inference_percent = 5
        self.progress(5, "Berechne Reparaturbilder …")
        if mode.startswith("rife") and self.rife is None:
            from rife_backend import Rife
            self.rife = Rife(lambda msg: self.progress(self.inference_percent, msg), force_cpu=self.settings.get("force_cpu", False))

        def generate(a, b, positions):
            nonlocal completed
            check(self.cancel)
            def advance(fraction):
                self.inference_percent = 5 + round(75 * (completed + len(positions) * fraction) / total)
                self.progress(self.inference_percent, "Berechne Reparaturbilder …")
            if mode == "motion":
                if len(self.motion_frames) < 2:
                    raise ValueError("Bewegungsreferenzen fehlen. Bitte den Prüfclip zuerst rechnen.")
                history = [frame_rgb(f, field_order) for f in self.motion_frames]
                history_times = [f.pts * f.time_base for f in self.motion_frames]
                motion_span = float(t1 - t0)
                if field_order != "off":
                    parity = 0 if completed == 0 else 1
                    history = [x[parity::2] for x in history]
                    if parity != (0 if field_order == "tff" else 1):
                        history_times = [t + f.duration * f.time_base / 2 for t, f in zip(history_times, self.motion_frames)]
                        motion_span += float(right_frame.duration * right_frame.time_base - left_frame.duration * left_frame.time_base) / 2
                intervals = [float(y - x) for x, y in zip(history_times, history_times[1:])]
                self.progress(self.inference_percent, f"Bewegung aus {len(history)}/6 intakten Bildern verlängern …")
                images = extend_motion(history, a, [p * motion_span for p in positions], intervals, self.cancel, advance)
            else:
                images = (self.rife.interpolate(a, b, positions, mode == "rife_hq", self.cancel, advance)
                    if mode.startswith("rife") else interpolate(a, b, len(positions), mode, positions))
            advance(1); completed += len(positions)
            if mode not in ("motion", "hold", "nearest") and self.settings.get("color_stabilize", True):
                images = [gentle_color_match(image, a, b, pos) for image, pos in zip(images, positions)]
            return images

        pixels = reconstruct(left, right, alphas, generate, field_order,
            float(left_frame.duration * left_frame.time_base), float(right_frame.duration * right_frame.time_base),
            [float(f.duration * f.time_base) for f in pending], float(t1 - t0),
            [left[p::2].copy() for p in (0, 1)] if field_order != "off" else None,
            [right[p::2].copy() for p in (0, 1)] if field_order != "off" else None)
        output = []
        for original, image in zip(pending, pixels):
            rgb = frame_rgb(original, field_order)
            for parity in intact_fields(original, self.settings):
                image[parity::2] = rgb[parity::2]
            output.append(image)
        return output


def clip_bounds(result, gap, context=.6):
    # Repairs always include the complete gap; preserved long phases remain bounded.
    limit = bisect.bisect_left(result.timestamps, result.timestamps[gap.start] + MAX_GAP_SECONDS) - 1
    last = max(gap.start, gap.end if gap.approved else min(gap.end, limit))
    start = max(0, bisect.bisect_left(result.timestamps, result.timestamps[gap.start] - Fraction(str(context))))
    end = min(result.count - 1, bisect.bisect_right(result.timestamps,
        result.timestamps[last] + Fraction(str(context))) - 1)
    return min(start, gap.start - 1) if gap.start else 0, max(end, min(last + 1, result.count - 1))


def read_window(path, timestamps, start, end, cancel=lambda: False, relative=False):
    """Seek to a preceding keyframe, then match decoded timestamps, including VFR."""
    frames = []
    expected = timestamps[start:end + 1]
    tolerance = Fraction(1, 1000)
    with av.open(str(path)) as src:
        stream = src.streams.video[0]
        if relative:
            shift = (stream.start_time or 0) * stream.time_base - timestamps[0]
            expected = [t + shift for t in expected]
        src.seek(max(0, int((expected[0] - tolerance) / stream.time_base)), stream=stream, backward=True)
        for frame in src.decode(stream):
            check(cancel)
            timestamp = frame.pts * frame.time_base
            if timestamp < expected[0] - tolerance:
                continue
            if timestamp > expected[-1] + tolerance:
                break
            if len(frames) >= len(expected) or abs(timestamp - expected[len(frames)]) > tolerance:
                raise ValueError("Vorschaubilder stimmen nicht mit der analysierten Bildfolge überein.")
            frames.append(frame)
    if len(frames) != len(expected):
        raise ValueError("Vorschauausschnitt konnte nicht vollständig gelesen werden.")
    return frames


@dataclass
class Preview:
    before: list[np.ndarray]
    after: list[np.ndarray]
    times: list[float]
    labels: list[str]
    selected: int
    note: str
    comparison: str = "planned"


def preview(result, gap, mode, progress=lambda p, s: None, cancel=lambda: False,
            export="lossless", rendered=None, original_only=False, context=.6):
    if signature(result.source) != result.signature:
        raise ValueError("Die Quelldatei hat sich geändert. Bitte erneut analysieren.")
    start, end = clip_bounds(result, gap, context)
    originals = read_window(result.source, result.timestamps, start, end, cancel)
    field_order = (result.settings or {}).get("interlaced", "off")
    changed = {}
    if rendered:
        rendered_frames = read_window(rendered, result.timestamps, start, end, cancel, relative=export == "mxf")
    elif gap.approved and not original_only:
        validate_gap(result, gap)
        renderer = Renderer(result.settings, progress, cancel)
        if resolved_mode(gap, mode) == "motion":
            renderer.motion_frames = motion_history(result, gap, cancel)
        a, b = gap.start - start, gap.end - start
        generated = renderer.render(originals[a - 1], originals[b + 1], originals[a:b + 1], gap, mode)
        fmt = media.native_format(originals[0].format.name)
        changed = {a + i: replacement_frame(originals[a + i], image, fmt, field_order,
                   untouched_fields=intact_fields(originals[a + i], result.settings))
                   for i, image in enumerate(generated)}
    before, after, times, labels = [], [], [], []
    first = 0 if field_order == "tff" else 1
    for i, original in enumerate(originals):
        check(cancel)
        replacement = rendered_frames[i] if rendered else changed.get(i, original)
        t = float(result.timestamps[start + i] - result.timestamps[start])
        label = f"Bild {start + i} · {timecode(float(result.timestamps[start + i] - result.timestamps[0]))}"
        if gap.start <= start + i <= gap.end or (gap.length == 0 and start + i == gap.start):
            label += " · " + EVENT_NAMES[gap.effective_kind]
        # Bob each native field for inspection; do not blend temporally separate fields.
        if field_order != "off":
            aa, bb = split_native_fields(original), split_native_fields(replacement)
            for order, parity in enumerate((first, 1 - first)):
                h, w = original.height, original.width
                aa_field = cv2.resize(aa[parity], (w, h), interpolation=cv2.INTER_LINEAR)
                bb_field = cv2.resize(bb[parity], (w, h), interpolation=cv2.INTER_LINEAR)
                before.append(preview_size(aa_field)); after.append(preview_size(bb_field))
                times.append(t + order * float(result.durations[start + i]) / 2)
                labels.append(label + (" · oberes Halbbild" if parity == 0 else " · unteres Halbbild"))
        else:
            before.append(preview_size(frame_rgb(original))); after.append(preview_size(frame_rgb(replacement)))
            times.append(t); labels.append(label)
    selected = (gap.start - start) * (2 if field_order != "off" else 1)
    note = ("Originalclip · Reparaturvorschau bei Bedarf berechnen" if original_only else
            "Fertiges Exportbild" if rendered else "Geplante Reparatur · MOV-Kompression erst beim Export")
    if not gap.approved:
        note += " · Diese Stelle bleibt erhalten"
    if gap.end >= end:
        note += " · Anfang einer längeren Schwarzphase"
    progress(100, "Vorschau bereit")
    return Preview(before, after, times, labels, selected, note,
                   "original" if original_only else "export" if rendered else "planned")


def preview_size(rgb):
    if rgb.dtype == np.uint16:
        rgb = ((rgb.astype(np.uint32) + 128) // 257).astype(np.uint8)
    h, w = rgb.shape[:2]
    scale = min(1., 640 / w, 360 / h)
    return np.ascontiguousarray(cv2.resize(rgb, (max(1, round(w * scale)), max(1, round(h * scale))),
                                         interpolation=cv2.INTER_AREA))


def write_clip(frames, path, rate, field_order, lossless, cancel, metadata=None, export=None, tc=None, progress=lambda p: None):
    export = export or ("lossless" if lossless else "compatible")
    metadata = dict(metadata or {})
    with av.open(str(path), "w", options={"movflags": "use_metadata_tags"} if export in ("compatible", "prores") else {}) as out:
        stream = media.create_stream(out, export, rate, frames[0].format.name,
            frames[0].width, frames[0].height, field_order, metadata)
        out.metadata.update(copy_tags(metadata.get("container_tags", {})))
        stream.metadata.update(copy_tags(metadata.get("video_tags", {})))
        if tc:
            out.metadata["timecode"] = tc
            stream.metadata["timecode"] = tc
        origin = frames[0].pts * frames[0].time_base
        tag = FieldTagger(field_order)
        for index, original in enumerate(frames):
            check(cancel)
            frame = media.prepare_frame(original, stream.pix_fmt, field_order, origin,
                stream.codec_context.time_base, index if export == "mxf" else None)
            out.mux(stream.encode(tag(frame)))
            progress((index + 1) / len(frames))
        out.mux(stream.encode(None))


def export_control_clips(result, output, progress=lambda p, s: None, cancel=lambda: False):
    """Extract from the actual finished export; do not run inference a second time."""
    output = Path(output)
    directory = Path(str(output.with_suffix("")) + "_KONTROLLE")
    directory.mkdir()  # output name is unique; never overwrite an earlier review.
    field_order = (result.settings or {}).get("interlaced", "off")
    entries = []
    try:
        for i, gap in enumerate(result.gaps):
            check(cancel)
            progress(99, f"Kontrollclips {i + 1}/{len(result.gaps)} …")
            start, end = clip_bounds(result, gap)
            basename = f"{i + 1:03}_{timecode(gap.seconds).replace(':', '-')}"
            paths = []
            for path, suffix in ((result.source, "original"), (output, "ergebnis")):
                with av.open(str(path)) as info:
                    context = info.streams.video[0].codec_context
                    metadata = {attr: getattr(context, attr) for attr in
                        ("color_range", "colorspace", "color_primaries", "color_trc", "sample_aspect_ratio")}
                frames = read_window(path, result.timestamps, start, end, cancel,
                                     relative=output.suffix == ".mxf" and suffix == "ergebnis")
                destination = directory / (basename + "_" + suffix + output.suffix)
                write_clip(frames, destination, result.rate, field_order, output.suffix == ".mkv", cancel, metadata,
                           export="mxf" if output.suffix == ".mxf" else None, tc=event_timecode(result, start))
                paths.append(destination.name)
            entries.append({"event": i + 1, "timecode": timecode(gap.seconds), "start_frame": start,
                "end_frame": end, "gap_start": gap.start, "gap_end": gap.end,
                "decision": gap.decision, "type": gap.effective_kind, "repaired": gap.approved, "mode": gap.mode,
                "truncated": gap.end > end, "original": paths[0], "result": paths[1]})
        (directory / "INDEX.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "LIESMICH.txt").write_text("Kontrollclips ohne Ton: Original und fertiges Ergebnis, mit etwa 0,6 Sekunden Kontext.\n"
            "Interlaced bleibt Interlaced. Bei langen Schwarzphasen wird nur der Anfang gezeigt.\n", encoding="utf-8")
        return str(directory)
    except BaseException:
        shutil.rmtree(directory)
        raise


def same_pixels(a, b):
    if a.format.name == b.format.name and (a.format.is_planar or a.format.name.startswith("gray")):
        return all(np.array_equal(np.frombuffer(pa, np.uint8).reshape(pa.height, pa.line_size)[:, :media.active_bytes(a, pa)],
                                  np.frombuffer(pb, np.uint8).reshape(pb.height, pb.line_size)[:, :media.active_bytes(b, pb)])
                   for pa, pb in zip(a.planes, b.planes))
    return np.array_equal(a.to_ndarray(format=media.rgb_target(a)), b.to_ndarray(format=media.rgb_target(b)))


def write_report(result, output_dir, mode, repaired=False, output=None, quality=None):
    source = Path(result.source)
    name = source.stem + ("_repair_report" if repaired else "_scan_report")
    # Reserve a unique report pair without overwriting earlier runs.
    serial = 0
    while True:
        suffix = "" if not serial else f"_{serial}"
        base = Path(output_dir) / (name + suffix)
        try:
            fd = os.open(str(base) + ".txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            serial += 1
    data = {"source": result.source, "output": str(output) if output else None,
            "frames": result.count, "mode": mode, "repaired": repaired,
            "detection": result.settings, "quality": quality,
            "source_timecode": result.source_timecode, "bit_depth": result.bit_depth, "pixel_format": result.pixel_format,
            "detected_field_order": result.detected_field_order,
            "field_evidence": result.field_evidence, "source_metadata": result.source_metadata,
            "gaps": [asdict(g) | {"frames": g.length, "event_type": EVENT_NAMES[g.effective_kind], "timecode": timecode(g.seconds),
                     "action": "repariert" if repaired and g.approved else
                     "vorgemerkt" if g.approved else "übersprungen"} for g in result.gaps]}
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("Video Repair – Bericht\n" + json.dumps(data, ensure_ascii=False, indent=2))
    with open(str(base) + ".json", "x", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return str(base) + ".txt"


def repair(result, output_dir, mode="flow", progress=lambda p, s: None, cancel=lambda: False,
           export="compatible", control_clips=False, artifacts=None):
    if signature(result.source) != result.signature:
        raise ValueError("Die Quelldatei hat sich geändert. Bitte neu analysieren.")
    approved = {g.start: g for g in result.gaps if g.approved}
    if not approved:
        raise ValueError("Keine sicher reparierbaren Lücken gefunden.")
    for gap in approved.values():
        validate_gap(result, gap)
    ordered = sorted((g for g in result.gaps if g.approved), key=lambda g: g.start)
    if any(a.end >= b.start for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Reparaturbereiche überschneiden sich. Bitte Framebereiche korrigieren.")
    field_order = (result.settings or {}).get("interlaced", "off")
    if export not in ("compatible", "prores", "lossless", "mxf") or mode not in ("rife", "rife_hq", "flow", "hold", "blend", "nearest", "motion"):
        raise ValueError("Unbekannter Export- oder Reparaturmodus.")
    renderer = Renderer(result.settings, progress, cancel)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    from review import event_key, patch_matches, render_event
    import master
    if not control_clips:
        progress(0, "Prüfe gespeicherten Gesamtexport …")
        ready = master.completed_output(result, output_dir, mode, export, cancel)
        if ready:
            progress(100, "Gesamtdatei bereits vorhanden")
            return ready
    artifacts = dict(artifacts or {})
    from review import artifacts as saved_artifacts
    artifacts.update(saved_artifacts(result, output_dir))
    missing = [g for g in ordered if not patch_matches(result, g, mode, artifacts.get(event_key(g)), full=True, cancel=cancel)]
    for number, gap in enumerate(missing):
        def clip_progress(p, message):
            progress(round(40 * (number + p / 100) / len(missing)), f"Prüfclip {number + 1}/{len(missing)} · {message}")
        renderer.progress = clip_progress
        artifacts[event_key(gap)] = render_event(result, gap, output_dir, mode, export,
            progress=clip_progress, cancel=cancel, renderer=renderer)
    outer_progress = progress
    if missing:
        progress = lambda p, message: outer_progress(40 + round(p * .6), message)
    extension = {"lossless": ".mkv", "compatible": ".mov", "prores": ".mov", "mxf": ".mxf"}[export]
    temp = None
    target = None
    published, completed = False, False
    try:
        temp = master.prepare(result, output_dir, mode, export, artifacts, progress, cancel)
        check(cancel)
        if signature(result.source) != result.signature:
            raise ValueError("Die Quelldatei wurde während des Exports verändert.")
        # Verify every output timestamp, not just the advertised frame count.
        progress(99, "Prüfe exportierte Bildfolge …")
        source_check = av.open(result.source) if export == "lossless" else None
        reference = source_check.decode(video=0) if source_check else None
        changed = {i for gap in approved.values() for i in range(gap.start, gap.end + 1)}
        with av.open(temp) as verified:
            count = 0
            first_time = None
            try:
                for frame in verified.decode(video=0):
                    check(cancel)
                    timestamp = frame.pts * frame.time_base
                    if first_time is None:
                        first_time = timestamp
                    expected = result.timestamps[count] if count < result.count else 0
                    if export == "mxf":
                        timestamp -= first_time
                        expected -= result.timestamps[0]
                    if count >= result.count or abs(timestamp - expected) > Fraction(1, 1000):
                        raise ValueError("Zeitstempelprüfung fehlgeschlagen.")
                    if reference is not None:
                        original = next(reference)
                        if count not in changed and not same_pixels(original, frame):
                            raise ValueError(f"Verlustfreie Bildprüfung fehlgeschlagen bei Bild {count}.")
                    count += 1
                if count != result.count:
                    raise ValueError("Bildanzahlprüfung fehlgeschlagen.")
            finally:
                if source_check:
                    source_check.close()
        # Hard link publishes the finished file atomically without overwriting originals.
        serial = 0
        while True:
            suffix = "" if not serial else f"_{serial}"
            target = output_dir / (Path(result.source).stem + "_REPAIRED" + suffix + extension)
            try:
                os.link(temp, target)
                published = True
                break
            except FileExistsError:
                serial += 1
            except OSError as exc:
                if exc.errno not in (errno.EPERM, errno.EXDEV, errno.ENOTSUP, errno.EINVAL):
                    raise
                # FAT/exFAT do not support hard links. Exclusively create a new file.
                try:
                    target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    serial += 1
                    continue
                try:
                    with os.fdopen(target_fd, "wb") as out, open(temp, "rb") as inp:
                        while True:
                            check(cancel)
                            chunk = inp.read(8 * 1024 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
                published = True
                break
        clips = export_control_clips(result, target, progress, cancel) if control_clips else None
        report = write_report(result, output_dir, mode, True, target,
            {"export": export, "field_order": field_order, "pixel_verified": export == "lossless",
             "inference": renderer.rife.detail if renderer.rife else next((a.get("inference") for a in artifacts.values()
                 if a.get("inference")), None), "control_clips": clips,
             "review_clips": {event_key(g): artifacts[event_key(g)]["clip"] for g in ordered}})
        master.mark_complete(result, output_dir, mode, export, target, report, cancel)
        progress(100, "Reparatur abgeschlossen")
        completed = True
        return str(target), report
    finally:
        if temp:
            Path(temp).unlink(missing_ok=True)
        if published and not completed:
            target.unlink(missing_ok=True)
