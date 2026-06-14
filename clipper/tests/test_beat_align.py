"""Tests for beat snapping (analyze.snap_to_beat / align_to_beats).

Uses a synthetic click track written to a real wav so librosa's beat tracker
has something to lock onto, without needing any media fixture.
"""

from __future__ import annotations

import numpy as np
import pytest

from clipper.analyze import SAMPLE_RATE, Segment, align_to_beats, snap_to_beat


def _click_track(path, bpm=120, seconds=8) -> None:
    """Write a wav of short clicks on the beat at a constant tempo."""
    import soundfile as sf

    period = 60.0 / bpm  # seconds between beats
    y = np.zeros(int(seconds * SAMPLE_RATE), dtype="float32")
    click = np.linspace(1.0, 0.0, int(0.02 * SAMPLE_RATE), dtype="float32")
    t = 0.0
    while t < seconds:
        i = int(t * SAMPLE_RATE)
        y[i : i + len(click)] = click
        t += period
    sf.write(str(path), y, SAMPLE_RATE)


def _nearest_grid_distance(value, period=0.5, span=8.0) -> float:
    grid = np.arange(0.0, span, period)
    return float(np.min(np.abs(grid - value)))


class TestSnapToBeat:
    def test_off_beat_start_snaps_onto_the_grid(self, tmp_path):
        wav = tmp_path / "clicks.wav"
        _click_track(wav, bpm=120, seconds=8)  # beats every 0.5s
        # 3.2s sits 0.2s past the beat at 3.0; snapping should pull it to a beat.
        snapped = snap_to_beat(wav, start=3.2, max_start=8.0)
        assert _nearest_grid_distance(snapped) < 0.12, (
            f"snapped start {snapped} should land near a 0.5s beat grid"
        )

    def test_never_moves_more_than_search(self, tmp_path):
        wav = tmp_path / "clicks.wav"
        _click_track(wav, bpm=120, seconds=8)
        snapped = snap_to_beat(wav, start=4.0, max_start=8.0, search=2.0)
        assert abs(snapped - 4.0) <= 2.0

    def test_respects_max_start_clamp(self, tmp_path):
        wav = tmp_path / "clicks.wav"
        _click_track(wav, bpm=120, seconds=8)
        snapped = snap_to_beat(wav, start=5.0, max_start=4.5)
        assert snapped <= 4.5

    def test_silence_returns_original_start(self, tmp_path):
        import soundfile as sf

        wav = tmp_path / "silence.wav"
        sf.write(str(wav), np.zeros(SAMPLE_RATE * 4, dtype="float32"), SAMPLE_RATE)
        # No beats detectable in silence → start is returned unchanged.
        assert snap_to_beat(wav, start=2.0, max_start=4.0) == 2.0

    def test_missing_file_returns_original_start(self, tmp_path):
        assert snap_to_beat(tmp_path / "nope.wav", start=1.0, max_start=4.0) == 1.0


class TestAlignToBeats:
    def test_preserves_count_duration_and_score(self, tmp_path):
        wav = tmp_path / "clicks.wav"
        _click_track(wav, bpm=120, seconds=12)
        segs = [Segment(3.2, 5.0, 0.9), Segment(7.3, 5.0, 0.6)]
        aligned = align_to_beats(wav, segs, duration=12.0)
        assert len(aligned) == 2
        for orig, new in zip(segs, aligned):
            assert new.duration == orig.duration
            assert new.score == orig.score
            assert 0.0 <= new.start <= 12.0 - new.duration
