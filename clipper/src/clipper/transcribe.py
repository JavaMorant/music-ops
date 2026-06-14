"""Ambition-tier: Whisper transcription of the set (feature-flagged).

Uses faster-whisper to transcribe the already-extracted mono wav into timed
segments. Those segments feed two things: per-clip transcript text passed to the
Claude captioner (ai.py), and a transcript file written alongside the clips.

Degrades gracefully: if faster-whisper isn't installed, `is_available()` returns
False and the caller skips transcription. Nothing here runs unless --transcribe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL_SIZE = "base"  # base is a good speed/quality tradeoff for set audio


class TranscribeError(RuntimeError):
    """Transcription could not be completed."""


@dataclass(frozen=True)
class TranscriptSegment:
    start: float  # seconds into the source
    end: float
    text: str


def is_available() -> bool:
    """True only if faster-whisper is importable."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe(wav_path: Path, model_size: str = DEFAULT_MODEL_SIZE) -> list[TranscriptSegment]:
    """Transcribe a wav to timed segments. Raises TranscribeError on failure."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - guarded by is_available()
        raise TranscribeError("faster-whisper not installed") from exc

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(wav_path))
        return [
            TranscriptSegment(float(s.start), float(s.end), s.text.strip())
            for s in segments
            if s.text and s.text.strip()
        ]
    except Exception as exc:  # model load / decode failures degrade gracefully
        raise TranscribeError(f"transcription failed: {exc}") from exc


def transcript_for_window(
    segments: list[TranscriptSegment], start: float, end: float
) -> str:
    """Join the text of every transcript segment overlapping [start, end)."""
    return " ".join(
        s.text for s in segments if s.end > start and s.start < end
    ).strip()
