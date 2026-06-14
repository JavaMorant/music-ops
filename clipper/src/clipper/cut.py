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
AUDIO_CODEC = ["-c:a", "aac", "-b:a", "192k"]

# 9:16 portrait crop clamped so the box never exceeds the source in either
# dimension. A source already taller than 9:16 (phone footage, a pre-cropped
# vertical, a still/waveform) would otherwise make crop width = ih*9/16 exceed
# the source width and ffmpeg aborts ("Invalid too big size for width"). The
# comma inside min() is escaped (\,) so the filtergraph parser doesn't read it
# as a filter separator.
CROP_W = r"min(iw\,ih*9/16)"
CROP_H = r"min(ih\,iw*16/9)"


def _crop_scale_filter(x_offset: int | None) -> str:
    """The -vf chain: clamped 9:16 crop (centred, or x_offset px from the left)
    centred vertically, scaled to the 1080x1920 delivery size."""
    x = "(iw-ow)/2" if x_offset is None else str(x_offset)
    return f"crop={CROP_W}:{CROP_H}:{x}:(ih-oh)/2,scale=1080:1920"


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


def _video_args(source: Path, segment: Segment, dest: Path, x_offset: int | None) -> list[str]:
    return [
        "ffmpeg",
        "-v", "error",
        "-ss", f"{segment.start:.3f}",
        "-t", f"{segment.duration:.3f}",
        "-i", str(source),
        "-vf", _crop_scale_filter(x_offset),
        "-af", LOUDNORM,
        "-ar", OUTPUT_SAMPLE_RATE,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        *AUDIO_CODEC,
        "-movflags", "+faststart",
        "-y",
        str(dest),
    ]


def _audio_args(source: Path, segment: Segment, dest: Path) -> list[str]:
    # Pin AAC@192k so audio-only sources (e.g. .wav set recordings) yield a
    # consistent, compressed, postable file instead of an uncompressed clip in
    # whatever codec the source extension implied.
    return [
        "ffmpeg",
        "-v", "error",
        "-ss", f"{segment.start:.3f}",
        "-t", f"{segment.duration:.3f}",
        "-i", str(source),
        "-af", LOUDNORM,
        "-ar", OUTPUT_SAMPLE_RATE,
        *AUDIO_CODEC,
        "-movflags", "+faststart",
        "-y",
        str(dest),
    ]


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
    _run_ffmpeg(_video_args(source, segment, dest, x_offset), dest)


def cut_audio_segment(source: Path, segment: Segment, dest: Path) -> None:
    """Audio-only source: cut a loudness-normalised AAC clip instead."""
    _guard_dest(source, dest)
    _run_ffmpeg(_audio_args(source, segment, dest), dest)
