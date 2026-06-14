"""Segment selection engine.

Scores every second of audio by RMS energy + onset strength, then picks
non-overlapping candidate clip windows by a blend of drop strength (a sharp
energy rise) and sustained level, anchored so the drop lands just inside each
clip. Pure functions on numpy arrays so the engine is testable without media
files.
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


NORMALIZE_PERCENTILE = 99.0  # robust range: ignore the top/bottom 1% of values


def _normalize(x: np.ndarray, percentile: float = NORMALIZE_PERCENTILE) -> np.ndarray:
    """Scale to 0..1 using a robust percentile range, then clamp.

    A single clipping spike (feedback, a dropped mic, a bumped fader) is a huge
    outlier in raw RMS; plain min-max would map it to 1.0 and crush every real
    moment toward 0. Taking lo/hi from the 1st/99th percentiles and clamping
    keeps the scale set by the body of the audio, not its worst sample.
    """
    if x.size == 0:
        return x
    lo = float(np.percentile(x, 100.0 - percentile))
    hi = float(np.percentile(x, percentile))
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


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


# Clip selection tuning. A good clip isn't just the loudest 30s — it's a build
# that pays off into a drop, with the drop landing a beat or two in (not at the
# very start, not buried). These constants encode that; they're the dials to
# turn if selection favours the wrong moments.
LEAD_IN_SECONDS = 5  # start a clip this many seconds before the detected drop
BUILD_WINDOW = 8  # seconds compared before vs after a moment to measure a step up
DROP_WEIGHT = 0.5  # blend: "is this a drop" vs "is this sustained-loud" (0..1)


def _rise_signal(energy: np.ndarray, k: int) -> np.ndarray:
    """Per-second energy *step up*: mean of the k seconds after each moment minus
    the k seconds before it, clipped at zero. Peaks mark builds paying off into a
    drop — a sharp rise scores high here even if the absolute level is moderate,
    which is what separates a real drop from a sustained-loud stretch.
    """
    n = len(energy)
    if n == 0:
        return energy
    cumsum = np.concatenate([[0.0], np.cumsum(energy)])
    idx = np.arange(n)
    after_end = np.minimum(n, idx + k)
    before_start = np.maximum(0, idx - k)
    after = (cumsum[after_end] - cumsum[idx]) / np.maximum(after_end - idx, 1)
    before = (cumsum[idx] - cumsum[before_start]) / np.maximum(idx - before_start, 1)
    rise = np.where(idx > 0, after - before, 0.0)  # second 0 has no "before"
    return np.clip(rise, 0.0, None)


def snap_to_beat(
    wav_path: Path,
    start: float,
    max_start: float,
    window: float = 6.0,
    search: float = 2.0,
) -> float:
    """Snap a clip start to the nearest beat within ±`search` seconds.

    Tracks beats on a short audio window loaded around `start` only — so the
    tempo is local (a DJ set's BPM drifts across transitions) and no large file
    is ever loaded. Returns the original start unchanged if beats can't be found,
    and never moves the start past `max_start` (so the clip stays in-bounds).
    """
    import librosa  # deferred: heavy import, keeps --help fast

    if not Path(wav_path).is_file():
        return start
    offset = max(0.0, start - window / 2)
    try:
        y, sr = librosa.load(str(wav_path), sr=SAMPLE_RATE, offset=offset, duration=window)
    except Exception:
        return start
    if y.size < SAMPLE_RATE // 2:  # under half a second of audio: nothing to track
        return start
    _, beats = librosa.beat.beat_track(y=y, sr=sr, hop_length=HOP_LENGTH, units="time")
    times = np.asarray(beats, dtype=float) + offset
    times = times[np.abs(times - start) <= search]
    if times.size == 0:
        return start
    snapped = float(times[int(np.argmin(np.abs(times - start)))])
    return min(max(0.0, snapped), max_start)


def align_to_beats(
    wav_path: Path, segments: list[Segment], duration: float
) -> list[Segment]:
    """Return segments with each start snapped to the nearest beat."""
    aligned = []
    for seg in segments:
        max_start = max(0.0, duration - seg.duration)
        start = snap_to_beat(wav_path, seg.start, max_start)
        aligned.append(Segment(start, seg.duration, seg.score))
    return aligned


def select_segments(
    scores: np.ndarray,
    clip_len: int,
    max_clips: int,
    spacing: int,
    lead_in: int = LEAD_IN_SECONDS,
    drop_weight: float = DROP_WEIGHT,
    build_window: int = BUILD_WINDOW,
) -> list[Segment]:
    """Pick up to max_clips non-overlapping clip_len-second windows, ranked by a
    blend of drop strength and sustained energy, keeping at least `spacing`
    seconds between the end of one chosen clip and the start of the next.

    Each candidate is anchored to a moment of rising energy (a drop) and starts
    `lead_in` seconds before it, so the drop lands just inside the clip rather
    than at the cut or halfway through. `drop_weight` trades off rewarding the
    rise against rewarding overall loudness across the window.
    """
    n = len(scores)
    if n == 0:
        return []
    if n < clip_len:
        return [Segment(0.0, float(n), float(np.mean(scores)))]

    last_start = n - clip_len
    level = np.convolve(scores, np.ones(clip_len) / clip_len, mode="valid")
    level_norm = _normalize(level)
    rise_norm = _normalize(_rise_signal(scores, build_window))

    # one candidate clip per second, started lead_in before the drop at that
    # second and clamped inside the source
    starts = np.clip(np.arange(n) - lead_in, 0, last_start)
    combined = drop_weight * rise_norm + (1.0 - drop_weight) * level_norm[starts]
    order = np.argsort(combined)[::-1]

    chosen: list[Segment] = []
    for d in order:
        if len(chosen) >= max_clips:
            break
        start = int(starts[d])
        ok = all(
            start + clip_len + spacing <= c.start or start >= c.end + spacing
            for c in chosen
        )
        if ok:
            chosen.append(Segment(float(start), float(clip_len), float(combined[d])))
    return sorted(chosen, key=lambda s: -s.score)
