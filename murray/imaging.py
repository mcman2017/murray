"""Image handling on the trusted side.

Bytes that come back from the sandbox, or from a visitor's upload, are untrusted. Everything here decodes them
strictly, under size caps, and re-encodes before anything is stored or shown to a model. The objective image
statistics computed here are also the ground truth the evaluation scores against, so this file is hashed into every
eval run (`detector_hash`).
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import config

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_SIDE = 2048
Image.MAX_IMAGE_PIXELS = 40_000_000


class ImageRejected(ValueError):
    pass


def decode_png(data: bytes) -> Image.Image:
    """Strictly decode sandbox output: PNG only, bounded size, fully loaded, flattened to RGB."""
    if not data.startswith(PNG_MAGIC):
        raise ImageRejected("not a PNG file")
    if len(data) > 16 * 1024 * 1024:
        raise ImageRejected("PNG larger than 16 MB")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format != "PNG":
            raise ImageRejected("not a PNG file")
        if img.width * img.height > config.SANDBOX_MAX_PIXELS or max(img.size) > MAX_SIDE:
            raise ImageRejected(f"image is {img.width}x{img.height}; the limit is {MAX_SIDE} pixels on a side")
        img.load()
    except ImageRejected:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImageRejected(f"undecodable PNG ({type(exc).__name__})") from exc
    return _flatten(img)


def _flatten(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.split()[3])
        return base
    return img.convert("RGB")


def normalize_upload(data: bytes) -> Image.Image:
    """A visitor's image: allow-listed formats, bounded pixels, first frame only, metadata dropped, downscaled."""
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ImageRejected("upload larger than the limit")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("PNG", "JPEG", "WEBP", "GIF"):
            raise ImageRejected("unsupported image format (use PNG, JPEG, WebP or GIF)")
        if img.width * img.height > config.MAX_UPLOAD_PIXELS:
            raise ImageRejected("image has too many pixels")
        img.seek(0)
        img.load()
    except ImageRejected:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImageRejected(f"undecodable image ({type(exc).__name__})") from exc
    out = _flatten(img)
    if max(out.size) > 1568:
        out.thumbnail((1568, 1568), Image.LANCZOS)
    return out


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


# ---------------------------------------------------------------- objective statistics

# Frozen before the first evaluation round; see README "How defects are detected".
THRESHOLDS = {
    "uniform_fraction": 0.98,      # one colour covers the frame
    "luma_std_blank": 1.5,         # or the frame has no tonal variation at all
    "clip_high": 0.35,             # share of pixels with a saturated channel
    "banding_levels": 24,          # at most this many distinct colours outside the dominant one
    "banding_min_area": 0.10,      # ... over at least this share of the frame
    "pixelation_dup": 0.45,        # share of adjacent identical rows (or columns) among those that carry detail
    "pixelation_dup_minor": 0.20,  # ... with the other direction repeating too: blocks, not stripes
    "rms_contrast": 0.035,         # tonal spread (0..1) below which the subject is lost
    "ink_fraction": 0.005,         # share of pixels that differ from the background
}


def stats(img: Image.Image) -> dict:
    a = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w, _ = a.shape
    n = h * w
    packed = (a[..., 0].astype(np.uint32) << 16) | (a[..., 1].astype(np.uint32) << 8) | a[..., 2].astype(np.uint32)
    colours, counts = np.unique(packed, return_counts=True)
    mode_idx = int(np.argmax(counts))
    mode_colour = int(colours[mode_idx])
    uniform = float(counts[mode_idx]) / n
    luma = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    luma_std = float(luma.std())
    # distance from the dominant colour: "ink" is whatever is not background
    mc = np.array([(mode_colour >> 16) & 255, (mode_colour >> 8) & 255, mode_colour & 255], dtype=np.int16)
    dist = np.abs(a.astype(np.int16) - mc).max(axis=2)
    ink = float((dist > 24).mean())
    sat = float(((a.max(axis=2) == 255) & (a.min(axis=2) < 250)).mean() + (a.min(axis=2) == 255).mean())
    black = float((a.max(axis=2) <= 2).mean())
    levels_outside = int((counts[np.arange(len(counts)) != mode_idx] > max(n // 20000, 2)).sum())
    area_outside = 1.0 - uniform
    # Pixelation shows as blocks: neighbouring rows AND neighbouring columns repeat exactly. Rows or columns that
    # are one flat colour (margins, backgrounds, stripes) say nothing about it and are left out.
    row_varies = (a != a[:, :1]).any(axis=(1, 2))
    col_varies = (a != a[:1, :]).any(axis=(0, 2))
    row_pairs = row_varies[1:] & row_varies[:-1]
    col_pairs = col_varies[1:] & col_varies[:-1]
    same_rows = (a[1:] == a[:-1]).all(axis=(1, 2))
    same_cols = (a[:, 1:] == a[:, :-1]).all(axis=(0, 2))
    dup_rows = float((same_rows & row_pairs).sum() / row_pairs.sum()) if row_pairs.sum() >= 16 else 0.0
    dup_cols = float((same_cols & col_pairs).sum() / col_pairs.sum()) if col_pairs.sum() >= 16 else 0.0
    return {
        "width": w, "height": h,
        "uniform_fraction": round(uniform, 4),
        "luma_mean": round(float(luma.mean()), 2),
        "luma_std": round(luma_std, 3),
        "rms_contrast": round(luma_std / 255.0, 4),
        "distinct_colours": int(len(colours)),
        "levels_outside_mode": levels_outside,
        "area_outside_mode": round(area_outside, 4),
        "clip_high": round(min(sat, 1.0), 4),
        "black_fraction": round(black, 4),
        "ink_fraction": round(ink, 4),
        "dup_rows": round(dup_rows, 4),
        "dup_cols": round(dup_cols, 4),
    }


def detect(st: dict) -> list[str]:
    """Defect classes that the statistics alone establish. Conservative: a class is reported only when the numbers
    leave little room for a clean image."""
    t = THRESHOLDS
    found: list[str] = []
    blank = st["uniform_fraction"] >= t["uniform_fraction"] or st["luma_std"] < t["luma_std_blank"] or st["ink_fraction"] < t["ink_fraction"]
    if blank:
        return ["blank_or_uniform"]
    if st["clip_high"] >= t["clip_high"]:
        found.append("clipping")
    if st["levels_outside_mode"] <= t["banding_levels"] and st["area_outside_mode"] >= t["banding_min_area"]:
        found.append("banding")
    hi, lo = max(st["dup_rows"], st["dup_cols"]), min(st["dup_rows"], st["dup_cols"])
    if hi >= t["pixelation_dup"] and lo >= t["pixelation_dup_minor"]:
        found.append("pixelation")
    if st["rms_contrast"] < t["rms_contrast"]:
        found.append("low_contrast")
    return found


def hard_fail(st: dict) -> str | None:
    """The only signal the render loop takes from the statistics: the frame is blank. Subtle defects are the
    visual critic's job, so that the detector which scores the evaluation is not also steering the loop."""
    if "blank_or_uniform" in detect(st):
        return "the frame is blank or one flat colour"
    return None


def detector_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------- frames, sheets, files

def key_frames(n: int, k: int = 6) -> list[int]:
    if n <= k:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def contact_sheet(frames: list[Image.Image], indices: list[int] | None = None) -> Image.Image:
    """Up to six labelled key frames in a 3x2 grid, for the critic."""
    idx = indices if indices is not None else key_frames(len(frames))
    tiles = [frames[i] for i in idx]
    tw, th = 384, 288
    cols = 3 if len(tiles) > 2 else len(tiles)
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    for pos, (i, tile) in enumerate(zip(idx, tiles)):
        t = tile.copy()
        t.thumbnail((tw, th), Image.LANCZOS)
        x = (pos % cols) * tw + (tw - t.width) // 2
        y = (pos // cols) * th + (th - t.height) // 2
        sheet.paste(t, (x, y))
        label = f"frame {i}"
        lx, ly = (pos % cols) * tw + 6, (pos // cols) * th + 4
        draw.rectangle([lx - 2, ly - 1, lx + 8 * len(label), ly + 13], fill=(0, 0, 0))
        draw.text((lx, ly), label, fill=(255, 255, 255))
    return sheet


def save_gif(frames: list[Image.Image], path: Path, duration_ms: int = 90) -> None:
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=255) for f in frames]
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=duration_ms, loop=0, optimize=False)


def save_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")


def for_model(img: Image.Image, max_side: int = 1024) -> bytes:
    """PNG bytes at a size worth sending to a vision model."""
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return to_png(img)
