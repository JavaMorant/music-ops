"""ffmpeg/ffprobe helpers: probing and audio extraction.

All subprocess calls build argument lists — never shell strings — so paths
with spaces, unicode, and emoji survive.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aiff", ".ogg"}


class MediaError(RuntimeError):
    """A media file could not be probed or decoded."""


def _run(args: list[str], subject: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise MediaError(f"{args[0]} not found — is it installed and on PATH?") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaError(
            f"{args[0]} failed on {subject.name!r}:\n{exc.stderr.strip()[-2000:]}"
        ) from exc


def probe(path: Path) -> dict:
    """Return ffprobe format/stream info for a media file."""
    result = _run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,width,height",
            "-of", "json",
            str(path),
        ],
        subject=path,
    )
    info = json.loads(result.stdout)
    if "format" not in info or "duration" not in info.get("format", {}):
        raise MediaError(f"could not read duration of {path}")
    return info


def duration_seconds(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def video_frame_size(path: Path) -> tuple[int, int] | None:
    """(width, height) of the first video stream, or None for audio-only.

    Files with audio extensions skip the probe — embedded cover art in
    mp3/flac would otherwise register as a video stream.
    """
    if path.suffix.lower() in AUDIO_EXTENSIONS:
        return None
    for s in probe(path).get("streams", []):
        if s.get("codec_type") == "video" and s.get("width"):
            return int(s["width"]), int(s["height"])
    return None


def has_video_stream(path: Path) -> bool:
    return video_frame_size(path) is not None


def extract_audio(source: Path, dest_dir: Path, sample_rate: int = 22050) -> Path:
    """Extract the audio track to a mono wav inside dest_dir.

    This is the long-file efficiency rule: analysis decodes audio only;
    the video stream is never touched until cutting. The caller owns
    dest_dir and its cleanup (cli wraps it in a TemporaryDirectory).
    """
    out = Path(dest_dir) / "audio.wav"
    _run(
        [
            "ffmpeg",
            "-v", "error",
            "-i", str(source),
            "-vn",
            "-ac", "1",
            "-ar", str(sample_rate),
            "-y",
            str(out),
        ],
        subject=source,
    )
    return out
