"""Resumable video encoding in independently verified, closed video parts.

The final container only remuxes these packets and the original audio. A cancel
during final mux restarts that cheap pass; completed video encoding is retained.
"""
import bisect
import json
import os
import tempfile
from fractions import Fraction
from pathlib import Path

import av

import engine
import media
import review
import storage

PART_SECONDS = 10
ENCODER_REVISION = 1


def job_fingerprint(result, mode, export):
    return storage.digest({"source": result.source, "signature": result.signature, "count": result.count,
        "rate": result.rate, "timecode": result.source_timecode, "format": result.pixel_format,
        "depth": result.bit_depth, "export": export, "fields": result.settings.get("interlaced", "off"),
        "repairs": [review.fingerprint(result, gap, mode) for gap in result.gaps if gap.approved],
        "encoder": ENCODER_REVISION})


def completed_output(result, output_dir, mode, export, cancel=lambda: False):
    path = review.session_directory(result, output_dir) / ("MASTER_" + export) / "DONE.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        output = (Path(output_dir) / data["output"]).resolve()
        report = (Path(output_dir) / data["report"]).resolve()
        if not output.is_relative_to(Path(output_dir).resolve()) or not report.is_relative_to(Path(output_dir).resolve()):
            return None
        if data.get("fingerprint") == job_fingerprint(result, mode, export) and report.is_file() and storage.valid_file(output, data["integrity"], True, cancel):
            return str(output), str(report)
    except (OSError, ValueError, KeyError):
        pass


def mark_complete(result, output_dir, mode, export, output, report, cancel):
    storage.atomic_json(review.session_directory(result, output_dir) / ("MASTER_" + export) / "DONE.json",
        {"fingerprint": job_fingerprint(result, mode, export), "output": Path(output).name,
         "report": Path(report).name, "integrity": storage.file_record(output, cancel)})


def has_checkpoint(result, output_dir, export):
    path = review.session_directory(result, output_dir) / ("MASTER_" + export)
    return any(path.glob("part_*.json"))


def ranges(result):
    start = 0
    while start < result.count:
        stop = bisect.bisect_left(result.timestamps, result.timestamps[start] + Fraction(str(PART_SECONDS)))
        stop = min(result.count, max(start + 1, stop))
        yield start, stop - 1
        start = stop


def _frames(result, start, end, cancel):
    with av.open(result.source) as source:
        stream = source.streams.video[0]
        stream.codec_context.thread_count = 2
        tolerance = Fraction(1, 1000000)
        source.seek(max(0, int(result.timestamps[start] / stream.time_base)), stream=stream, backward=True)
        index = start
        for frame in source.decode(stream):
            engine.check(cancel)
            timestamp = frame.pts * frame.time_base
            if timestamp < result.timestamps[start] - tolerance:
                continue
            if index > end:
                break
            if abs(timestamp - result.timestamps[index]) > tolerance:
                raise ValueError("Bildfolge hat sich seit der Analyse verändert.")
            frame.duration = round(result.durations[index] / frame.time_base)
            yield index, frame
            index += 1
        if index != end + 1:
            raise ValueError("Unvollständige Bildfolge beim Export.")


def _encode_part(result, start, end, path, export, meta, artifacts, cancel, progress):
    order = result.settings.get("interlaced", "off")
    origin = result.timestamps[start]
    ledger, loaded = [], {}
    gaps = [g for g in result.gaps if g.approved and start <= g.end and end >= g.start]
    durations = {result.timestamps[i] - origin: result.durations[i] for i in range(start, end + 1)}
    with av.open(str(path), "w", format="matroska", options={"avoid_negative_ts": "disabled"}) as out:
        video = media.create_stream(out, export, result.rate, result.pixel_format, meta["width"], meta["height"], order, meta)
        tag = engine.FieldTagger(order)

        def write(packets):
            for packet in packets:
                tb = packet.time_base
                if packet.pts is None or packet.dts is None:
                    raise ValueError("Encoder liefert fehlende Paket-Zeitstempel.")
                duration = durations.get(packet.pts * tb, Fraction(1) / Fraction(result.rate))
                packet.duration = max(1, round(duration / tb))
                ledger.append({"pts": str(packet.pts * tb), "dts": str(packet.dts * tb), "duration": str(duration)})
                out.mux(packet)

        for index, frame in _frames(result, start, end, cancel):
            for gap in gaps:
                if gap.start <= index <= gap.end:
                    key = review.event_key(gap)
                    if key not in loaded:
                        loaded[key] = review.patch_frames(artifacts[key], result, gap, cancel)
                    frame = loaded[key][index - gap.start]
                    break
            prepared = media.prepare_frame(frame, video.pix_fmt, order, origin, video.codec_context.time_base,
                                           index - start if export == "mxf" else None)
            write(video.encode(tag(prepared)))
            progress((index - start + 1) / (end - start + 1))
        write(video.encode(None))
    # Decode each new part before it becomes a resume checkpoint.
    with av.open(str(path)) as check:
        count = sum(1 for frame in check.decode(video=0))
    if count != end - start + 1 or len(ledger) != count:
        raise ValueError("Exportabschnitt enthält eine unvollständige Bildfolge.")
    return ledger


def _packet_stream(parts, result, output_stream, export, cancel):
    target_base = output_stream.time_base or (Fraction(1) / Fraction(result.rate) if export == "mxf" else Fraction(1, 1000000))
    offset = result.timestamps[0] if export == "mxf" else Fraction(0)
    last_dts = None
    for entry in parts:
        origin = result.timestamps[entry["start"]] - offset
        with av.open(entry["path"]) as part:
            index = 0
            for packet in part.demux(video=0):
                engine.check(cancel)
                if not packet.size:
                    continue
                if index >= len(entry["packets"]):
                    raise ValueError("Exportabschnitt hat unerwartete Pakete.")
                timing = entry["packets"][index]
                packet.pts = round((origin + Fraction(timing["pts"])) / target_base)
                packet.dts = round((origin + Fraction(timing["dts"])) / target_base)
                packet.duration = max(1, round(Fraction(timing["duration"]) / target_base))
                packet.time_base, packet.stream = target_base, output_stream
                # Closed encoders can have a different reorder delay for a
                # final short GOP. Decode times must remain strictly increasing.
                if last_dts is not None and packet.dts <= last_dts:
                    raise ValueError("Nicht aufsteigende Paket-Zeitstempel zwischen Exportabschnitten.")
                last_dts = packet.dts
                index += 1
                yield packet
            if index != len(entry["packets"]):
                raise ValueError("Exportabschnitt enthält fehlende Pakete.")


def _mux(parts, result, path, export, meta, cancel, progress):
    fmt = "matroska" if export == "lossless" else "mxf" if export == "mxf" else "mov"
    options = {"avoid_negative_ts": "disabled"}
    if fmt == "mov":
        options["movflags"] = "use_metadata_tags"
    with av.open(result.source) as source, av.open(parts[0]["path"]) as template, av.open(str(path), "w", format=fmt, options=options) as out:
        # PyAV 16's template API omits AVStream.avg_frame_rate. MXF and
        # MOV timecode need that value. add_stream(rate=...) fills it;
        # restore the already encoded headers after opening the context.
        # No frames are encoded in this final pass.
        template_video = template.streams.video[0]
        if fmt == "mxf" or (fmt == "mov" and result.source_timecode):
            ctx = template_video.codec_context
            video = media.create_stream(out, export, result.rate, ctx.format.name, ctx.width, ctx.height,
                                        result.settings.get("interlaced", "off"), meta)
            video.codec_context.open()
            video.codec_context.extradata = ctx.extradata
            video.codec_context.codec_tag = ctx.codec_tag
        else:
            video = out.add_stream_from_template(template_video, opaque=True)
        video.time_base = Fraction(1) / Fraction(result.rate) if export == "mxf" else source.streams.video[0].time_base
        video.metadata.update(engine.copy_tags(meta.get("video_tags", {})))
        out.metadata.update(engine.copy_tags(meta.get("container_tags", {})))
        if result.source_timecode:
            video.metadata["timecode"] = out.metadata["timecode"] = result.source_timecode
        audio_map = {s.index: out.add_stream_from_template(s, opaque=True) for s in source.streams.audio}
        for stream in source.streams.audio:
            audio_map[stream.index].metadata.update(engine.copy_tags(stream.metadata))

        def audio():
            if not source.streams.audio:
                return
            for packet in source.demux(list(source.streams.audio)):
                engine.check(cancel)
                if packet.dts is None:
                    continue
                if export == "mxf":
                    shift = round(result.timestamps[0] / packet.time_base)
                    packet.pts -= shift; packet.dts -= shift
                packet.stream = audio_map[packet.stream.index]
                yield packet

        videos = iter(_packet_stream(parts, result, video, export, cancel))
        audios = iter(audio())
        vp, ap = next(videos, None), next(audios, None)
        count = 0
        while vp is not None or ap is not None:
            engine.check(cancel)
            if ap is None or (vp is not None and vp.dts * vp.time_base <= ap.dts * ap.time_base):
                out.mux(vp); count += 1
                vp = next(videos, None)
                if count % 25 == 0:
                    progress(round(90 + 8 * count / result.count), f"Füge zusammen · Bild {count:,} / {result.count:,}")
            else:
                out.mux(ap)
                ap = next(audios, None)


def prepare(result, output_dir, mode, export, artifacts, progress, cancel):
    session = review.session_directory(result, output_dir)
    job = session / ("MASTER_" + export)
    job.mkdir(parents=True, exist_ok=True)
    with storage.file_lock(job / "active.lock"):
        with av.open(result.source) as source:
            video = source.streams.video[0]
            meta = review.video_metadata(video.codec_context)
            meta.update(width=video.width, height=video.height,
                        container_tags=dict(source.metadata), video_tags=dict(video.metadata))
            if not result.pixel_format:
                result.pixel_format = video.codec_context.format.name
            spec = media.profile(export, result.pixel_format)
            if export == "mxf":
                engine.validate_mxf(result, video.width, video.height)
                if any(s.codec_context.name not in ("pcm_s16le", "pcm_s24le") or s.codec_context.sample_rate != 48000 for s in source.streams.audio):
                    raise ValueError("MXF unterstützt hier PCM-Ton mit 48 kHz (16/24 Bit), wie bei XDCAM. Für anderen Ton MOV/MKV wählen.")
        windows = list(ranges(result))
        completed = []
        state = {"source": result.source, "signature": list(result.signature), "export": export,
                 "total": len(windows), "completed": [], "state": "encoding"}
        final_temp = None
        try:
            for number, (start, end) in enumerate(windows):
                engine.check(cancel)
                affected = [g for g in result.gaps if g.approved and start <= g.end and end >= g.start]
                plan = {"signature": result.signature, "source": result.source, "start": start, "end": end,
                        "timestamps": [str(t) for t in result.timestamps[start:end + 1]], "profile": spec,
                        "fields": result.settings.get("interlaced", "off"), "rate": result.rate,
                        "metadata": {k: str(v) for k, v in meta.items() if k not in ("container_tags", "video_tags")},
                        "repairs": [review.fingerprint(result, gap, mode) for gap in affected], "encoder": ENCODER_REVISION}
                stamp = storage.digest(plan)
                path = job / f"part_{start:09}_{stamp[:20]}.mkv"
                ready = path.with_suffix(".json")
                try:
                    entry = json.loads(ready.read_text(encoding="utf-8"))
                    valid = (entry.get("fingerprint") == stamp
                             and len(entry.get("packets", [])) == end - start + 1
                             and entry.get("ledger_hash") == storage.digest(entry["packets"])
                             and storage.valid_file(path, entry.get("integrity"), True, cancel))
                except (OSError, ValueError):
                    valid = False
                if not valid:
                    fd, temp = tempfile.mkstemp(prefix=".part-", suffix=".mkv", dir=job)
                    os.close(fd)
                    try:
                        ledger = _encode_part(result, start, end, temp, export, meta, artifacts, cancel,
                            lambda p: progress(round(5 + 85 * (number + p) / len(windows)),
                                f"Exportabschnitt {number + 1}/{len(windows)} · {round(p * 100)} %"))
                        entry = {"fingerprint": stamp, "start": start, "end": end, "packets": ledger,
                                 "integrity": storage.file_record(temp, cancel), "ledger_hash": storage.digest(ledger)}
                        os.replace(temp, path)
                        storage.atomic_json(ready, entry)
                    finally:
                        Path(temp).unlink(missing_ok=True)
                else:
                    progress(round(5 + 85 * (number + 1) / len(windows)),
                             f"Exportabschnitt {number + 1}/{len(windows)} bereits vorhanden")
                entry["path"] = str(path)
                completed.append(entry)
                state["completed"].append({"start": start, "end": end, "fingerprint": stamp})
                storage.atomic_json(job / "INDEX.json", state)
            engine.check(cancel)
            state["state"] = "muxing"
            storage.atomic_json(job / "INDEX.json", state)
            extension = ".mkv" if export == "lossless" else ".mxf" if export == "mxf" else ".mov"
            fd, final_temp = tempfile.mkstemp(prefix=".video-repair-", suffix=extension, dir=output_dir)
            os.close(fd)
            _mux(completed, result, final_temp, export, meta, cancel, progress)
            state["state"] = "encoded"
            storage.atomic_json(job / "INDEX.json", state)
            return final_temp
        except BaseException as exc:
            if final_temp:
                Path(final_temp).unlink(missing_ok=True)
            state["state"] = "cancelled" if isinstance(exc, engine.Cancelled) else "failed"
            state["error"] = str(exc)
            storage.atomic_json(job / "INDEX.json", state)
            raise
