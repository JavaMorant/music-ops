"""Cut chosen segments out of the source video with ffmpeg.

9:16 centre crop (optional manual x-offset), loudness normalised, encoded
once — no second pass, never more re-encoding than needed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .analyze import Segment
from .media import MediaError


def cut_segment(
    source: Path,
    segment: Segment,
    dest: Path,
    vertical: bool = True,
    x_offset: int | None = None,
) -> None:
    """Cut one segment to dest. Crop to 9:16 centred (or x_offset px from
    the left edge of the crop window) and normalise loudness to -14 LUFS.
    """
    filters = []
    if vertical:
        x = "(iw-ow)/2" if x_offset is None else str(x_offset)
        filters.append(f"crop=ih*9/16:ih:{x}:0")

    args = [
        "ffmpeg",
        "-v", "error",
        "-ss", f"{segment.start:.3f}",
        "-t", f"{segment.duration:.3f}",
        "-i", str(source),
    ]
    if filters:
        args += ["-vf", ",".join(filters)]
    args += [
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-y",
        str(dest),
    ]
    try:
        subprocess.run(args, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise MediaError(
            f"ffmpeg failed cutting {dest.name}:\n{exc.stderr.strip()[-2000:]}"
        ) from exc


def cut_audio_segment(source: Path, segment: Segment, dest: Path) -> None:
    """Audio-only source: cut a loudness-normalised audio clip instead."""
    args = [
        "ffmpeg",
        "-v", "error",
        "-ss", f"{segment.start:.3f}",
        "-t", f"{segment.duration:.3f}",
        "-i", str(source),
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-y",
        str(dest),
    ]
    try:
        subprocess.run(args, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise MediaError(
            f"ffmpeg failed cutting {dest.name}:\n{exc.stderr.strip()[-2000:]}"
        ) from exc
