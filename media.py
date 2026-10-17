"""Bit-depth aware pixel storage and shared export profiles."""
from fractions import Fraction

import av
import cv2
import numpy as np

YUV = {f"yuv{s}p{suffix}" for s in ("420", "422", "444")
       for suffix in ("", "10le", "10be")}
YUV |= {"yuvj420p", "yuvj422p", "yuvj444p"}


def bit_depth(frame_or_format):
    fmt = av.VideoFormat(frame_or_format) if isinstance(frame_or_format, str) else frame_or_format.format
    return max(c.bits for c in fmt.components)


def rgb_format(array):
    return "rgb48le" if array.dtype == np.uint16 else "rgb24"


def rgb_target(frame):
    return "rgb48le" if bit_depth(frame) > 8 else "rgb24"


def maximum(array):
    return float(np.iinfo(array.dtype).max)


def gray8(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return ((gray.astype(np.uint32) + 128) // 257).astype(np.uint8) if gray.dtype == np.uint16 else gray


def active_bytes(frame, plane):
    # VideoPlane.width is a SAMPLE count for >8-bit planar formats.
    if frame.format.is_planar or frame.format.name.startswith("gray"):
        return plane.width * (2 if bit_depth(frame) > 8 else 1)
    return frame.width * ((frame.format.bits_per_pixel + 7) // 8)


def native_format(fmt):
    supported = {x.name for x in av.Codec("ffv1", "w").video_formats}
    if fmt in supported:
        return fmt
    if bit_depth(fmt) > 8:
        return "yuv420p10le" if "420" in fmt or fmt.startswith("p010") else "yuv444p10le"
    return "bgr0" if av.VideoFormat(fmt).is_rgb else "yuv420p"


def profile(export, source_format):
    depth = bit_depth(source_format)
    if export == "lossless":
        return {"codec": "ffv1", "pix_fmt": native_format(source_format),
                "options": {"level": "3", "coder": "1", "context": "1", "slicecrc": "1"}}
    if export == "prores" or (export == "compatible" and depth > 8):
        full_chroma = "444" in source_format or av.VideoFormat(source_format).is_rgb
        return {"codec": "prores_ks", "pix_fmt": "yuv444p10le" if full_chroma else "yuv422p10le",
                "options": {"profile": "4" if full_chroma else "3", "vendor": "apl0"}}
    if export == "compatible":
        return {"codec": "libx264", "pix_fmt": "yuv420p", "options": {"crf": "12", "preset": "slow"}}
    if export == "mxf":
        if depth > 8:
            raise ValueError("Das XDCAM-MXF-Profil ist 8 Bit. Für 10 Bit bitte FFV1/MKV oder ProRes/MOV wählen.")
        return {"codec": "mpeg2video", "pix_fmt": "yuv422p", "options": {}}
    raise ValueError("Unbekanntes Exportformat.")


def create_stream(container, export, rate, source_format, width, height, fields, metadata):
    spec = profile(export, source_format)
    stream = container.add_stream(spec["codec"], rate=Fraction(rate))
    stream.width, stream.height, stream.pix_fmt = width, height, spec["pix_fmt"]
    stream.codec_context.time_base = Fraction(1, 1000000)
    stream.codec_context.thread_count = 2
    options = dict(spec["options"])
    if fields != "off":
        options["field_order"] = "tt" if fields == "tff" else "bb"
        if spec["codec"] in ("prores_ks", "libx264"):
            options["flags"] = "+ildct+ilme"
        if spec["codec"] == "libx264":
            options["x264-params"] = f"{fields}=1"
    stream.options = options
    if export == "mxf":
        import engine
        engine.configure_mxf(stream, fields)
    for attr in ("color_range", "colorspace", "color_primaries", "color_trc"):
        setattr(stream.codec_context, attr, metadata.get(attr, 2))
    stream.codec_context.sample_aspect_ratio = metadata.get("sample_aspect_ratio") or Fraction(1)
    return stream


def prepare_frame(original, fmt, fields, origin, time_base, local_index=None):
    import engine
    timestamp, duration = original.pts * original.time_base, original.duration * original.time_base
    if original.format.name != fmt:
        # Split chroma as well as luma before any interlaced conversion.
        frame = engine.replacement_frame(original, engine.frame_rgb(original, fields), fmt, fields)
    else:
        frame = engine.clone_frame(original)
    frame.pts = round((timestamp - origin) / time_base) if local_index is None else local_index
    frame.time_base = time_base
    frame.duration = max(1, round(duration / time_base))
    return frame
