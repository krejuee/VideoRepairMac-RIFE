"""Durable event clips and reusable, lossless repair patches."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import av

import engine
import media
import storage

_index_lock = threading.RLock()


def event_key(gap):
    return f"{gap.identity[0]}-{gap.identity[1]}"


def frozen(result):
    # Cache arrays are immutable during review; only decisions/settings are copied.
    return replace(result, gaps=[replace(g) for g in result.gaps], settings=dict(result.settings or {}),
                   review_history={})


def repair_plan(result, gap, mode):
    settings = result.settings or {}
    method = engine.resolved_mode(gap, mode)
    fields = settings.get("interlaced", "off")
    overlays = gap.overlays if gap.overlays is not None else settings.get("preserve_overlays", True)
    return {"source": result.source, "signature": list(result.signature), "start": gap.start, "end": gap.end,
        "approved": gap.approved, "type": gap.effective_kind, "mode": method,
        "fields": fields, "depth": result.bit_depth,
        "overlays": bool(overlays),
        "threshold": settings.get("pixel_threshold", 12) if overlays or fields != "off" else None,
        "black_fraction": settings.get("min_black_fraction", .995) if fields != "off" else None,
        "color_stabilize": bool(settings.get("color_stabilize", True)) if method not in ("hold", "motion", "nearest") else False,
        "force_cpu": bool(settings.get("force_cpu", False)) if method.startswith("rife") else False,
        "pipeline": 8 if result.bit_depth > 8 else 6}


def fingerprint(result, gap, mode, export=None):
    # The repair pixels are independent of container, UI language, selection
    # sensitivity (unless it changes a pixel mask), and maximum gap duration.
    return storage.digest(repair_plan(result, gap, mode))


def legacy_fingerprint(result, gap, mode, export):
    settings = result.settings or {}
    plan = {"source": result.source, "signature": result.signature, "start": gap.start, "end": gap.end,
        "approved": gap.approved, "type": gap.effective_kind,
        "mode": engine.resolved_mode(gap, mode), "overlays": gap.overlays,
        "export": export, "timecode": result.source_timecode,
        "settings": {k: settings.get(k) for k in ("interlaced", "pixel_threshold", "min_black_fraction",
            "preserve_overlays", "color_stabilize", "force_cpu")}, "pipeline": 6}
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def session_directory(result, output_dir):
    identity = hashlib.sha256(json.dumps([result.source, result.signature]).encode()).hexdigest()[:10]
    return Path(output_dir) / (Path(result.source).stem + "_PRUEFUNG_" + identity)


def read_session(result, output_dir):
    directory = session_directory(result, output_dir)
    empty = {"schema": 8, "source": result.source, "signature": list(result.signature),
             "events": {}, "decisions": {}, "versions": {}}
    try:
        data = json.loads((directory / "INDEX.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = empty
        # Individually committed repair journals survive a damaged main index.
        for path in sorted(directory.glob("event_*.json"), key=lambda p: p.stat().st_mtime_ns):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("source") != result.source or saved.get("signature") != list(result.signature):
                    continue
                entry = saved["entry"]
                data["events"][saved["key"]] = entry
                data["versions"][entry["fingerprint"]] = entry
                data["decisions"][saved["key"]] = saved.get("decision", {})
                if saved.get("preferences"):
                    data["preferences"] = saved["preferences"]
            except (OSError, ValueError, KeyError):
                continue
    if data.get("source") != result.source or data.get("signature") != list(result.signature):
        raise ValueError("Der Prüfclip-Index gehört zu einer anderen Quelldatei.")
    for key in ("events", "decisions", "versions"):
        data.setdefault(key, {})
    return data


def save_session(result, output_dir, data):
    storage.atomic_json(session_directory(result, output_dir) / "INDEX.json", data)


def save_decisions(result, output_dir):
    with _index_lock:
        data = read_session(result, output_dir)
        known = dict(result.review_history)
        known.update({g.identity: g for g in result.gaps})
        for gap in known.values():
            data["decisions"][event_key(gap)] = {"decision": gap.decision, "type_override": gap.type_override,
                "mode": gap.mode, "overlays": gap.overlays, "start": gap.start, "end": gap.end,
                "range_override": gap.range_override}
        save_session(result, output_dir, data)


def restore_decisions(result, output_dir):
    data = read_session(result, output_dir)
    for gap in result.gaps:
        saved = data["decisions"].get(event_key(gap))
        if saved:
            gap.decision = saved.get("decision", "auto")
            gap.type_override = saved.get("type_override", "auto")
            gap.mode, gap.overlays = saved.get("mode", "global"), saved.get("overlays")
            if saved.get("range_override") and saved["end"] < result.count:
                gap.start, gap.end, gap.range_override = saved["start"], saved["end"], True
            engine.apply_decision(result, gap)
    return artifacts(result, output_dir, data)


def _resolved(result, output_dir, entry):
    directory = session_directory(result, output_dir).resolve()
    item = dict(entry)
    def resolve(name):
        if not name:
            return None
        path = (directory / name).resolve()
        return str(path) if path.is_relative_to(directory) else None
    for name in ("clip", "original", "patch"):
        item[name] = resolve(item.get(name))
    item["exports"] = {key: dict(value, clip=resolve(value.get("clip")), original=resolve(value.get("original")))
                       for key, value in item.get("exports", {}).items()}
    return item


def artifacts(result, output_dir, data=None):
    data = data or read_session(result, output_dir)
    return {key: _resolved(result, output_dir, entry) for key, entry in data["events"].items()}


def patch_matches(result, gap, mode, artifact, full=False, cancel=lambda: False):
    if not artifact:
        return False
    same_plan = artifact.get("fingerprint") == fingerprint(result, gap, mode)
    if not same_plan and result.bit_depth == 8:
        same_plan = artifact.get("fingerprint") == legacy_fingerprint(result, gap, mode, artifact.get("export"))
    if not same_plan:
        return False
    if not gap.approved:
        return True
    record = artifact.get("integrity", {}).get("patch")
    valid = storage.valid_file(artifact.get("patch"), record, full, cancel)
    if valid and full and not record:
        try:
            with av.open(artifact["patch"]) as video:
                valid = sum(1 for frame in video.decode(video=0)) == gap.length
        except (av.error.FFmpegError, OSError, ValueError):
            valid = False
    return valid


def export_view(artifact, export, context=1.0):
    key = f"{export}:{float(context):g}"
    view = artifact.get("exports", {}).get(key)
    if view:
        return {**artifact, **view, "integrity": {**artifact.get("integrity", {}), **view.get("integrity", {})}}
    return artifact if artifact.get("export") == export and artifact.get("context", 1.0) == context else None


def matches(result, gap, mode, export, artifact):
    if not patch_matches(result, gap, mode, artifact):
        return False
    view = export_view(artifact, export)
    return bool(view and storage.valid_file(view.get("clip"), view.get("integrity", {}).get("clip"))
                and view.get("timecode_source_value", result.source_timecode) == result.source_timecode)


def preferences(result, output_dir):
    return read_session(result, output_dir).get("preferences", {})


def save_preferences(result, output_dir, mode, export):
    with _index_lock:
        data = read_session(result, output_dir)
        data["preferences"] = {"settings": result.settings, "mode": mode, "export": export}
        save_session(result, output_dir, data)


def _commit(result, gap, output_dir, entry):
    directory = session_directory(result, output_dir)
    relative = dict(entry)
    for name in ("patch", "clip", "original"):
        if relative.get(name):
            relative[name] = Path(relative[name]).name
    relative["exports"] = {}
    for key, variant in entry.get("exports", {}).items():
        variant = dict(variant)
        for name in ("clip", "original"):
            if variant.get(name):
                variant[name] = Path(variant[name]).name
        relative["exports"][key] = variant
    with _index_lock:
        data = read_session(result, output_dir)
        storage.atomic_json(directory / f"event_{event_key(gap)}_{entry['fingerprint'][:20]}.json",
            {"source": result.source, "signature": list(result.signature), "key": event_key(gap),
             "entry": relative, "decision": data["decisions"].get(event_key(gap), {}),
             "preferences": data.get("preferences", {})})
        data["events"][event_key(gap)] = relative
        data["versions"][entry["fingerprint"]] = relative
        save_session(result, output_dir, data)
    return _resolved(result, output_dir, relative)


def video_metadata(context):
    return {attr: getattr(context, attr) for attr in
            ("color_range", "colorspace", "color_primaries", "color_trc", "sample_aspect_ratio")}


def render_event(result, gap, output_dir, mode, export, context=1.0,
                 progress=lambda p, s: None, cancel=lambda: False, renderer=None):
    directory = session_directory(result, output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with storage.file_lock(directory / "render.lock"):
        return _render_event(result, gap, output_dir, mode, export, context, progress, cancel, renderer)


def _render_event(result, gap, output_dir, mode, export, context, progress, cancel, renderer):
    if engine.signature(result.source) != result.signature:
        raise ValueError("Die Quelldatei hat sich verändert. Bitte erneut scannen.")
    engine.check(cancel)
    directory = session_directory(result, output_dir)
    key, stamp = event_key(gap), fingerprint(result, gap, mode)
    data = read_session(result, output_dir)
    candidates = [data["events"].get(key), data["versions"].get(stamp), *data["versions"].values()]
    existing = None
    for candidate in candidates:
        if candidate:
            candidate = _resolved(result, output_dir, candidate)
            if patch_matches(result, gap, mode, candidate, full=True, cancel=cancel):
                existing = candidate
                break
    if existing and matches(result, gap, mode, export, existing):
        view = export_view(existing, export, context)
        if view and storage.valid_file(view.get("clip"), view.get("integrity", {}).get("clip"), True, cancel):
            progress(100, "Prüfclip bereits vorhanden")
            # Keep the active event in sync when returning to a previous setting.
            view.update(fingerprint=stamp, plan=repair_plan(result, gap, mode))
            return _commit(result, gap, output_dir, view)
    start, end = engine.clip_bounds(result, gap, context)
    originals = engine.read_window(result.source, result.timestamps, start, end, cancel)
    with av.open(result.source) as source:
        meta = video_metadata(source.streams.video[0].codec_context)
        meta.update({"container_tags": engine.copy_tags(source.metadata),
                     "video_tags": engine.copy_tags(source.streams.video[0].metadata)})
    media.profile(export, originals[0].format.name)  # Validate BEFORE expensive inference.
    if export == "mxf":
        engine.validate_mxf(result, originals[0].width, originals[0].height)
    order = (result.settings or {}).get("interlaced", "off")
    tc, clip_tc = engine.event_timecode(result, gap.start), engine.event_timecode(result, start)
    basis = "TC" if result.source_timecode else "LaufzeitTC"
    basename = f"{basis}_{tc.replace(':', '-').replace(';', '-')}_F{gap.start:08}_{stamp[:16]}"
    entry = dict(existing or {})
    entry.update({"fingerprint": stamp, "plan": repair_plan(result, gap, mode), "event_start": gap.start,
        "event_end": gap.end, "start": start, "end": end, "event_timecode": tc, "clip_timecode": clip_tc,
        "timecode_source": "source" if result.source_timecode else "runtime", "timecode_source_value": result.source_timecode,
        "type": gap.effective_kind, "approved": gap.approved, "mode": engine.resolved_mode(gap, mode),
        "field_order": order, "context": context, "export": export})
    entry["exports"] = dict(entry.get("exports", {}))
    entry["integrity"] = dict(entry.get("integrity", {}))
    repaired = list(originals)
    a, b = gap.start - start, gap.end - start

    def save_video(label, frames, kind, frame_tc, name, low, high):
        suffix = ".mkv" if kind == "lossless" else ".mxf" if kind == "mxf" else ".mov"
        fd, temp = tempfile.mkstemp(prefix=".clip-", suffix=suffix, dir=directory)
        os.close(fd)
        try:
            engine.write_clip(frames, temp, result.rate, order, kind == "lossless", cancel, meta, export=kind, tc=frame_tc,
                progress=lambda p: progress(round(low + p * (high - low)), f"Speichere {label} · {tc}"))
            with av.open(temp) as check:
                count = 0
                for frame in check.decode(video=0):
                    engine.check(cancel)
                    if count >= len(frames) or (kind == "lossless" and not engine.same_pixels(frames[count], frame)):
                        raise ValueError("Prüfclip-Bildprüfung fehlgeschlagen.")
                    count += 1
            if count != len(frames):
                raise ValueError("Prüfclip enthält eine unvollständige Bildfolge.")
            record = storage.file_record(temp, cancel)
            final = directory / (name + suffix)
            os.replace(temp, final)
            return str(final), record
        finally:
            Path(temp).unlink(missing_ok=True)

    if gap.approved:
        engine.validate_gap(result, gap)
        if existing:
            progress(5, "Gespeicherte Reparatur wird übernommen · keine neue Berechnung")
            native = patch_frames(existing, result, gap, cancel)
        else:
            renderer = renderer or engine.Renderer(result.settings, progress, cancel)
            renderer.progress = progress
            if engine.resolved_mode(gap, mode) == "motion":
                renderer.motion_frames = engine.motion_history(result, gap, cancel)
            pixels = renderer.render(originals[a - 1], originals[b + 1], originals[a:b + 1], gap, mode)
            fmt = media.native_format(originals[0].format.name)
            native = [engine.replacement_frame(frame, image, fmt, order, untouched_fields=engine.intact_fields(frame, result.settings))
                      for frame, image in zip(originals[a:b + 1], pixels)]
            entry["inference"] = dict(renderer.rife.detail) if renderer.rife else None
            if engine.resolved_mode(gap, mode) == "motion":
                entry["motion_reference_frames"] = len(renderer.motion_frames)
            entry["patch"], entry["integrity"]["patch"] = save_video("patch", native, "lossless", tc, basename + "_patch", 80, 84)
            # Inference is durable BEFORE original/result exports; interruption
            # here must never require computing the same repair a second time.
            _commit(result, gap, output_dir, entry)
        repaired[a:b + 1] = native
    suffix = ".mkv" if export == "lossless" else ".mxf" if export == "mxf" else ".mov"
    variant_name = storage.digest([export, context, clip_tc, start, end, 8])[:10]
    for label, frames, low, high in (("original", originals, 84, 90), ("clip", repaired, 90, 99)):
        entry[label], entry["integrity"][label] = save_video(label, frames, export, clip_tc,
            basename + "_" + variant_name + "_" + label, low, high)
    engine.check(cancel)
    if engine.signature(result.source) != result.signature:
        raise ValueError("Die Quelldatei wurde während der Clipberechnung verändert. Bitte erneut scannen.")
    variant = {k: entry[k] for k in ("clip", "original", "start", "end", "context", "export", "clip_timecode", "timecode_source_value")}
    variant["integrity"] = {k: entry["integrity"][k] for k in ("clip", "original")}
    entry["exports"][f"{export}:{float(context):g}"] = variant
    saved = _commit(result, gap, output_dir, entry)
    progress(100, "Prüfclip gespeichert")
    return saved


def patch_frames(artifact, result, gap, cancel=lambda: False):
    with av.open(artifact["patch"]) as container:
        frames = []
        for frame in container.decode(video=0):
            engine.check(cancel)
            frames.append(frame)
    if len(frames) != gap.length:
        raise ValueError("Gespeicherte Reparatur enthält nicht die erwartete Bildanzahl.")
    for frame, index in zip(frames, range(gap.start, gap.end + 1)):
        frame.pts, frame.time_base = result.timestamps[index].numerator, Fraction(1, result.timestamps[index].denominator)
        frame.duration = round(result.durations[index] / frame.time_base)
    return frames


def preview_artifact(result, gap, artifact, cancel=lambda: False):
    result = frozen(result)
    result.settings["interlaced"] = artifact.get("field_order", "off")
    # A clip rendered early during scanning may have less available post-roll.
    result.count = min(result.count, artifact["end"] + 1)
    before = engine.preview(result, gap, "hold", cancel=cancel, original_only=True,
                            context=artifact.get("context", 1.0))
    with av.open(artifact["clip"]) as container:
        frames = list(container.decode(video=0))
    expected = artifact["end"] - artifact["start"] + 1
    if len(frames) != expected:
        raise ValueError("Der gespeicherte Prüfclip ist unvollständig.")
    after = []
    order = artifact.get("field_order", "off")
    for frame in frames:
        engine.check(cancel)
        if order == "off":
            after.append(engine.preview_size(engine.frame_rgb(frame)))
        else:
            fields = engine.split_native_fields(frame)
            for parity in ((0, 1) if order == "tff" else (1, 0)):
                import cv2
                after.append(engine.preview_size(cv2.resize(fields[parity], (frame.width, frame.height))))
    if len(after) != len(before.before):
        raise ValueError("Prüfclip und Original haben unterschiedliche Bildfolgen. Interlaced-Einstellung prüfen.")
    before.after, before.comparison = after, "clip"
    before.note = "Gespeicherter Prüfclip · " + Path(artifact["clip"]).name + " · ohne Ton"
    return before
