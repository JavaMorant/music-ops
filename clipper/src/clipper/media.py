"""ffmpeg/ffprobe helpers: probing and audio extraction.

All subprocess calls build argument lists — never shell strings — so paths
with spaces, unicode, and emoji survive.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aiff", ".ogg"}


class MediaError(RuntimeError):
    """A media file could not be probed or decoded."""


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise MediaError(f"{args[0]} not found — is it installed and on PATH?") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaError(
            f"{args[0]} failed on {args[-1]!r}:\n{exc.stderr.strip()[-2000:]}"
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
        ]
    )
    info = json.loads(result.stdout)
    if "format" not in info or "duration" not in info.get("format", {}):
        raise MediaError(f"could not read duration of {path}")
    return info


def duration_seconds(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def has_video_stream(path: Path) -> bool:
    if path.suffix.lower() in AUDIO_EXTENSIONS:
        return False
    info = probe(path)
    return any(s.get("codec_type") == "video" for s in info.get("streams", []))


def extract_audio(source: Path, sample_rate: int = 22050) -> Path:
    """Extract the audio track to a mono wav in a temp dir.

    This is the long-file efficiency rule: analysis decodes audio only;
    the video stream is never touched until cutting.
    """
    tmp = Path(tempfile.mkdtemp(prefix="clipper-")) / "audio.wav"
    _run(
        [
            "ffmpeg",
            "-v", "error",
            "-i", str(source),
            "-vn",
            "-ac", "1",
            "-ar", str(sample_rate),
            "-y",
            str(tmp),
        ]
    )
    return tmp
