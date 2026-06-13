"""Cut chosen segments out of the source video with ffmpeg.

9:16 centre crop (optional manual x-offset) scaled to 1080x1920 delivery,
loudness normalised, encoded once — never more re-encoding than needed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .analyze import Segment
from .media import MediaError

# loudnorm resamples to 192 kHz internally; without an explicit rate that
# propagates to the encoder (192 kHz wav / 96 kHz aac outputs).
OUTPUT_SAMPLE_RATE = "48000"
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"


def _guard_dest(source: Path, dest: Path) -> None:
    if dest.resolve() == source.resolve():
        raise MediaError(f"refusing to overwrite source file: {source}")


def _run_ffmpeg(args: list[str], dest: Path) -> None:
    try:
        subprocess.run(args, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise MediaError(
            f"ffmpeg failed cutting {dest.name}:\n{exc.stderr.strip()[-2000:]}"
        ) from exc


def cut_segment(
    source: Path,
    segment: Segment,
    dest: Path,
    x_offset: int | None = None,
) -> None:
    """Cut one segment to dest: 9:16 crop centred (or x_offset px from the
    left edge), scaled to 1080x1920, loudness normalised to -14 LUFS.
    """
    _guard_dest(source, dest)
    x = "(iw-ow)/2" if x_offset is None else str(x_offset)
    args = [
        "ffmpeg",
        "-v", "error",
        "-ss", f"{segment.start:.3f}",
        "-t", f"{segment.duration:.3f}",
        "-i", str(source),
        "-vf", f"crop=ih*9/16:ih:{x}:0,scale=1080:1920",
        "-af", LOUDNORM,
        "-ar", OUTPUT_SAMPLE_RATE,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-y",
        str(dest),
    ]
    _run_ffmpeg(args, dest)


def cut_audio_segment(source: Path, segment: Segment, dest: Path) -> None:
    """Audio-only source: cut a loudness-normalised audio clip instead."""
    _guard_dest(source, dest)
    args = [
        "ffmpeg",
        "-v", "error",
        "-ss", f"{segment.start:.3f}",
        "-t", f"{segment.duration:.3f}",
        "-i", str(source),
        "-af", LOUDNORM,
        "-ar", OUTPUT_SAMPLE_RATE,
        "-y",
        str(dest),
    ]
    _run_ffmpeg(args, dest)
