import numpy as np
import pytest

from clipper.analyze import LEAD_IN_SECONDS, select_segments


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


# ---------------------------------------------------------------------------
# Anchoring: clip starts lead_in seconds before the detected energy rise
# ---------------------------------------------------------------------------


def test_anchoring_clip_starts_before_detected_drop():
    """A sharp build->drop produces a clip that starts ~LEAD_IN_SECONDS before the
    drop moment, so the drop itself lands inside the clip rather than at the cut edge.

    Fixture: 120-second array with a build ramp at seconds 50:60 (0.4) and a
    sustained high-energy region at seconds 60:90 (1.0).  The rise signal peaks
    at second 60 — the moment energy steps up sharply.  With the default
    lead_in=LEAD_IN_SECONDS, the chosen clip should start at 60 - LEAD_IN_SECONDS = 55.
    """
    n = 120
    scores = np.zeros(n)
    scores[50:60] = 0.4  # build ramp
    scores[60:90] = 1.0  # drop / sustained high energy

    clip_len = 30
    segs = select_segments(scores, clip_len=clip_len, max_clips=1, spacing=0)
    assert len(segs) == 1

    seg = segs[0]
    drop_second = 60  # known: rise signal peaks here
    expected_start = drop_second - LEAD_IN_SECONDS  # = 55

    # The start is within a small tolerance of lead_in seconds before the drop.
    TOLERANCE = 2  # seconds; allows for rounding in starts array
    assert abs(seg.start - expected_start) <= TOLERANCE, (
        f"clip should start ~{expected_start}s (drop_second={drop_second} minus "
        f"lead_in={LEAD_IN_SECONDS}); got start={seg.start}"
    )

    # The drop moment must land inside the clip, not before it.
    assert seg.start <= drop_second < seg.end, (
        f"drop second {drop_second} must be inside clip [{seg.start}, {seg.end})"
    )


def test_anchoring_lead_in_zero_disables_shift():
    """With lead_in=0 the algorithm does not shift starts earlier; the clip that
    scores highest should begin at or very near the highest-level window start,
    not offset by LEAD_IN_SECONDS.
    """
    n = 120
    scores = np.zeros(n)
    scores[50:60] = 0.4
    scores[60:90] = 1.0

    clip_len = 30
    # lead_in=0: no anchoring shift; drop_weight=0: pure level signal
    segs_no_anchor = select_segments(
        scores, clip_len=clip_len, max_clips=1, spacing=0, lead_in=0, drop_weight=0.0
    )
    segs_anchor = select_segments(
        scores, clip_len=clip_len, max_clips=1, spacing=0, lead_in=LEAD_IN_SECONDS
    )

    assert len(segs_no_anchor) == 1
    assert len(segs_anchor) == 1

    # lead_in=0 start should be at or later than the default anchored start
    # (anchoring pulls starts earlier, so no-anchor >= anchor start)
    assert segs_no_anchor[0].start >= segs_anchor[0].start, (
        "disabling lead_in should not move the start earlier than the anchored version"
    )


# ---------------------------------------------------------------------------
# Rise-reward: build->drop window ranks higher than equally-loud flat window
# ---------------------------------------------------------------------------


def test_rise_reward_build_drop_ranks_above_flat_loud():
    """When two regions have identical sustained loudness but only one is preceded
    by a clear energy rise (silence -> loud), the rise-reward blending boosts the
    build->drop region to a higher combined score.

    Fixture (300 seconds):
      - Region A: seconds 0:50 constant 0.5 (flat-loud throughout; the BUILD_WINDOW
        lookback sees the same 0.5 throughout, so rise = after - before ≈ 0).
      - Region B: seconds 170:190 constant 0.5, preceded by silence (seconds
        162:170 = 0.0); the BUILD_WINDOW lookback at second 170 sees ~0.0 before
        and 0.5 after, producing a large positive rise signal.

    Assertions:
      1. With pure level (drop_weight=0.0, lead_in=0): both clips score identically.
      2. With rise blending (default drop_weight, lead_in=5): the clip anchored to
         the B rise scores strictly higher than the clip from the flat-loud A region.
    """
    n = 300
    scores = np.zeros(n)
    scores[0:50] = 0.5    # A: flat-loud, no meaningful rise into it
    scores[170:190] = 0.5  # B: same level, but preceded by silence -> strong rise

    clip_len = 20
    spacing = 80  # large enough that both clips fit without collision

    # 1. Pure level: both regions have the same mean level (0.5), so after
    #    normalisation both score 1.0 — the level signal gives no preference.
    segs_level = select_segments(
        scores, clip_len=clip_len, max_clips=2, spacing=spacing, lead_in=0, drop_weight=0.0
    )
    assert len(segs_level) == 2
    score_A_level = next(s.score for s in segs_level if s.start < 100)
    score_B_level = next(s.score for s in segs_level if s.start >= 100)
    assert score_A_level == pytest.approx(score_B_level), (
        "with drop_weight=0 (pure level) both equally-loud regions must score the same"
    )

    # 2. With rise blending: the B region is anchored to its sharp rise and ranks higher.
    segs_rise = select_segments(
        scores, clip_len=clip_len, max_clips=2, spacing=spacing
    )
    assert len(segs_rise) == 2
    score_A_rise = next(s.score for s in segs_rise if s.start < 100)
    score_B_rise = next(s.score for s in segs_rise if s.start >= 100)
    assert score_B_rise > score_A_rise, (
        f"build->drop region (score={score_B_rise:.4f}) should rank above flat-loud "
        f"region (score={score_A_rise:.4f}) when rise blending is active"
    )


def test_rise_reward_top_segment_is_from_build_drop_region():
    """The highest-scored segment returned by select_segments with default parameters
    should come from the build->drop region (B), not the flat-loud region (A),
    even though both have the same sustained amplitude.
    """
    n = 300
    scores = np.zeros(n)
    scores[0:50] = 0.5    # A: flat-loud
    scores[170:190] = 0.5  # B: preceded by silence -> rise rewarded

    clip_len = 20
    segs = select_segments(scores, clip_len=clip_len, max_clips=2, spacing=80)
    assert len(segs) >= 1
    top = segs[0]  # sorted by descending score
    assert top.start >= 100, (
        f"top-ranked clip should come from the build->drop region (start >= 100); "
        f"got start={top.start}"
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_select_segments_is_deterministic():
    """Calling select_segments twice with the same numpy array returns identical
    results — the algorithm is purely functional with no hidden state.
    """
    rng = np.random.default_rng(42)
    scores = rng.random(200)
    segs1 = select_segments(scores, clip_len=30, max_clips=3, spacing=60)
    segs2 = select_segments(scores, clip_len=30, max_clips=3, spacing=60)
    assert len(segs1) == len(segs2)
    for s1, s2 in zip(segs1, segs2):
        assert s1.start == s2.start
        assert s1.duration == s2.duration
        assert s1.score == s2.score


# ---------------------------------------------------------------------------
# Crowd-roar blending (--crowd-weight)
# ---------------------------------------------------------------------------


def _two_equal_peaks(n=300, a=(50, 80), b=(200, 230)):
    """Two regions of identical energy preceded by silence (so their rise/level
    signals are symmetric and the base ranking is a tie broken only by index)."""
    s = np.zeros(n)
    s[a[0] : a[1]] = 1.0
    s[b[0] : b[1]] = 1.0
    return s


def test_crowd_weight_zero_is_identical_to_no_crowd():
    """Supplying a crowd signal with crowd_weight=0 must not change selection at
    all — the opt-in feature is provably inert when off (regression guard)."""
    scores = _two_equal_peaks()
    crowd = np.zeros_like(scores)
    crowd[200:230] = 1.0
    base = select_segments(scores, clip_len=20, max_clips=2, spacing=40)
    with_off = select_segments(
        scores, clip_len=20, max_clips=2, spacing=40, crowd=crowd, crowd_weight=0.0
    )
    assert [(s.start, s.score) for s in base] == [(s.start, s.score) for s in with_off]


def test_crowd_boosts_the_region_with_the_roar():
    """Two equally-energetic regions; a crowd roar sits over the *earlier* one.
    On a tie the later region naturally wins (argsort is reverse-stable), so a
    crowd boost on the earlier region flipping it to the top proves the signal
    actually moves the ranking, not just rides the default order."""
    scores = _two_equal_peaks()
    crowd = np.zeros_like(scores)
    crowd[50:80] = 1.0  # audience only erupts over the earlier region A

    no_crowd = select_segments(scores, clip_len=20, max_clips=2, spacing=40)
    assert no_crowd[0].start >= 150, "without crowd, the later equal region wins the tiebreak"

    with_crowd = select_segments(
        scores, clip_len=20, max_clips=2, spacing=40, crowd=crowd, crowd_weight=0.5
    )
    assert with_crowd[0].start < 100, (
        f"with crowd_weight=0.5 the roar region (start<100) should rank first; "
        f"got start={with_crowd[0].start}"
    )


def test_crowd_signal_shorter_than_scores_does_not_crash():
    """A crowd array shorter than the score array (stream length mismatch) is
    padded, not an error — mirrors blend_visual's length tolerance."""
    scores = _two_equal_peaks()
    crowd = np.zeros(120)  # deliberately shorter than scores (300)
    crowd[60:90] = 1.0
    segs = select_segments(
        scores, clip_len=20, max_clips=2, spacing=40, crowd=crowd, crowd_weight=0.5
    )
    assert len(segs) == 2  # no exception, still returns clips
