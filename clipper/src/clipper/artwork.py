"""Batch-resize one artwork image into every platform spec with Pillow."""

from __future__ import annotations

from pathlib import Path

SPECS: dict[str, tuple[int, int]] = {
    "square": (3000, 3000),   # release artwork / most platforms
    "story": (1080, 1920),    # IG/TikTok story
    "banner": (1500, 500),    # X / page header
    "profile": (1000, 1000),  # avatars
}

JPEG_QUALITY = 92


class ArtworkError(RuntimeError):
    """An image could not be read or processed."""


def cover_box(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple[int, int, int, int]:
    """Centre crop box in source pixels matching the destination aspect."""
    src_aspect = src_w / src_h
    dst_aspect = dst_w / dst_h
    if src_aspect > dst_aspect:  # source wider: crop sides
        crop_w = round(src_h * dst_aspect)
        left = (src_w - crop_w) // 2
        return left, 0, left + crop_w, src_h
    crop_h = round(src_w / dst_aspect)  # source taller: crop top/bottom
    top = (src_h - crop_h) // 2
    return 0, top, src_w, top + crop_h


def render_specs(image: Path, out_dir: Path) -> list[tuple[str, Path, bool]]:
    """Write every spec; returns (spec name, path, was_upscaled) per output."""
    from PIL import Image  # deferred: keeps --help fast

    # UnidentifiedImageError (non-image) subclasses OSError; DecompressionBombError
    # (oversized image, > 2x Pillow's pixel cap) subclasses Exception, so both are
    # translated to ArtworkError for a clean CLI message instead of a traceback.
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        with Image.open(image) as src:
            rgb = src.convert("RGB")
            for name, (w, h) in SPECS.items():
                box = cover_box(rgb.width, rgb.height, w, h)
                upscaled = (box[2] - box[0]) < w
                dest = out_dir / f"{image.stem}_{name}_{w}x{h}.jpg"
                rgb.resize((w, h), Image.LANCZOS, box=box).save(
                    dest, quality=JPEG_QUALITY, optimize=True
                )
                results.append((name, dest, upscaled))
    except (OSError, Image.DecompressionBombError) as exc:
        raise ArtworkError(f"could not process {image.name}: {exc}") from exc
    return results
