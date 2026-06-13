"""Segment selection engine.

Scores every second of audio by RMS energy + onset strength, then picks
non-overlapping candidate clip windows ranked by mean score. Pure functions
on numpy arrays so the engine is testable without media files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

HOP_LENGTH = 512
SAMPLE_RATE = 22050


@dataclass(frozen=True)
class Segment:
    start: float  # seconds into the source
    duration: float
    score: float  # 0..1, higher = more postable energy

    @property
    def end(self) -> float:
        return self.start + self.duration


BLOCK_SECONDS = 600  # score in 10-min blocks: bounds STFT memory on multi-hour sets


def score_audio(path: Path) -> tuple[np.ndarray, float]:
    """Return (per-second score array, total duration in seconds).

    Reads the (already mono, 22050 Hz) wav in blocks so peak memory stays
    flat regardless of set length; normalisation is global across blocks.
    """
    import librosa  # deferred: heavy import, keeps --help fast
    import soundfile as sf

    info = sf.info(str(path))
    sr = info.samplerate
    rms_parts: list[np.ndarray] = []
    onset_parts: list[np.ndarray] = []
    for block in sf.blocks(
        str(path), blocksize=BLOCK_SECONDS * sr, dtype="float32", always_2d=False
    ):
        if len(block) < 2048:  # sub-frame remainder: < 0.1s, not scoreable
            continue
        rms_parts.append(librosa.feature.rms(y=block, hop_length=HOP_LENGTH)[0])
        onset_parts.append(
            librosa.onset.onset_strength(y=block, sr=sr, hop_length=HOP_LENGTH)
        )

    rms = np.concatenate(rms_parts) if rms_parts else np.array([])
    onset = np.concatenate(onset_parts) if onset_parts else np.array([])
    n = min(len(rms), len(onset))
    if n == 0:
        return np.array([]), 0.0
    combined = 0.6 * _normalize(rms[:n]) + 0.4 * _normalize(onset[:n])

    frames_per_second = sr / HOP_LENGTH
    duration = info.frames / sr
    per_second = _resample_to_seconds(combined, frames_per_second, duration)
    return per_second, duration


def _normalize(x: np.ndarray) -> np.ndarray:
    lo, hi = float(np.min(x)), float(np.max(x))
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return (x - lo) / (hi - lo)


def _resample_to_seconds(
    frames: np.ndarray, frames_per_second: float, duration: float
) -> np.ndarray:
    seconds = max(1, int(duration))
    out = np.zeros(seconds)
    for s in range(seconds):
        a = int(s * frames_per_second)
        b = max(a + 1, int((s + 1) * frames_per_second))
        out[s] = float(np.mean(frames[a:b])) if a < len(frames) else 0.0
    return out


def select_segments(
    scores: np.ndarray,
    clip_len: int,
    max_clips: int,
    spacing: int,
) -> list[Segment]:
    """Pick up to max_clips non-overlapping windows of clip_len seconds,
    greedily by mean score, keeping at least `spacing` seconds between the
    end of one chosen window and the start of the next.
    """
    n = len(scores)
    if n < clip_len:
        if n == 0:
            return []
        return [Segment(0.0, float(n), float(np.mean(scores)))]

    window = np.convolve(scores, np.ones(clip_len) / clip_len, mode="valid")
    order = np.argsort(window)[::-1]

    chosen: list[Segment] = []
    for start in order:
        if len(chosen) >= max_clips:
            break
        ok = all(
            start + clip_len + spacing <= c.start or start >= c.end + spacing
            for c in chosen
        )
        if ok:
            chosen.append(Segment(float(start), float(clip_len), float(window[start])))
    return sorted(chosen, key=lambda s: -s.score)
