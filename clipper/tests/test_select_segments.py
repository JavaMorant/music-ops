import numpy as np
import pytest

from clipper.analyze import select_segments


def scores_with_peaks(n=600, peaks=((100, 1.0), (300, 0.8), (500, 0.6))):
    s = np.full(n, 0.1)
    for center, height in peaks:
        s[center - 15 : center + 15] = height
    return s


def test_picks_highest_energy_windows_in_score_order():
    segs = select_segments(scores_with_peaks(), clip_len=30, max_clips=3, spacing=60)
    assert len(segs) == 3
    assert segs[0].score >= segs[1].score >= segs[2].score
    starts = sorted(s.start for s in segs)
    assert abs(starts[0] - 85) <= 16
    assert abs(starts[1] - 285) <= 16
    assert abs(starts[2] - 485) <= 16


def test_respects_spacing():
    segs = select_segments(scores_with_peaks(), clip_len=30, max_clips=5, spacing=60)
    ordered = sorted(segs, key=lambda s: s.start)
    for a, b in zip(ordered, ordered[1:]):
        assert b.start - a.end >= 60


def test_no_overlap_even_with_zero_spacing():
    segs = select_segments(np.random.default_rng(0).random(300), clip_len=30, max_clips=8, spacing=0)
    ordered = sorted(segs, key=lambda s: s.start)
    for a, b in zip(ordered, ordered[1:]):
        assert b.start >= a.end


def test_caps_at_max_clips():
    segs = select_segments(scores_with_peaks(), clip_len=15, max_clips=2, spacing=0)
    assert len(segs) == 2


def test_source_shorter_than_clip_returns_whole_thing():
    segs = select_segments(np.array([0.5] * 10), clip_len=30, max_clips=3, spacing=0)
    assert len(segs) == 1
    assert segs[0].start == 0.0
    assert segs[0].duration == 10.0


def test_empty_scores():
    assert select_segments(np.array([]), clip_len=30, max_clips=3, spacing=0) == []


def test_segment_bounds_inside_source():
    segs = select_segments(scores_with_peaks(), clip_len=45, max_clips=10, spacing=30)
    for s in segs:
        assert s.start >= 0
        assert s.end <= 600
