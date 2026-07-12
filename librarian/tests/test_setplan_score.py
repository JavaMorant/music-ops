# tests/test_setplan_score.py
from __future__ import annotations
from librarian.setplan.pool import Candidate, norm
from librarian.setplan.score import score_slot


def _c(artist, title, bpm=120.0, key=(8, "A"), genre="amapiano", plays=0):
    return Candidate(path=None, artist=artist, title=title, genre=genre, bpm=bpm, camelot=key,
                     length_s=180.0, norm_key=norm(f"{artist} {title}"), low_bitrate=False, plays=plays)


def _ctx(**kw):
    base = dict(rising=True, target_bpm=120.0, block="amapiano", adjacency={}, max_plays=10,
                freshness=0.3, follows={}, recent_artists=[], harmonic_mode="loose")
    base.update(kw)
    return base


def test_unknown_key_and_bpm_are_neutral_not_zero():
    prev = _c("A", "x")
    cand = _c("B", "y", bpm=None, key=None)
    s, _ = score_slot(cand, prev, _ctx())
    # neutral harmonic(0.5) + neutral bpm(0.5) still yields a usable score
    assert s > 0.2


def test_follows_boost_is_additive_not_a_penalty_when_absent():
    prev = _c("A", "x")
    perfect = _c("B", "y", key=(8, "A"))            # same key, no history
    s_perfect, _ = score_slot(perfect, prev, _ctx(follows={}))
    with_follows = _ctx(follows={prev.norm_key: {perfect.norm_key: 4}})
    s_hist, _ = score_slot(perfect, prev, with_follows)
    assert s_hist > s_perfect                       # history adds, never subtracts
    assert s_perfect > 0.4                          # a no-history perfect pick is still strong


def test_artist_repeat_penalised():
    prev = _c("A", "x")
    cand = _c("Repeat", "y")
    clean, _ = score_slot(cand, prev, _ctx(recent_artists=[]))
    repeat, _ = score_slot(cand, prev, _ctx(recent_artists=["repeat"]))
    assert clean - repeat >= 0.29


def test_unknown_key_scores_neutral_not_punished():
    # An unknown key must be treated as NEUTRAL (0.5), strictly better than a
    # clashing key (~0.10) — never zero. If the invariant regressed to 0, the
    # unknown-key track would score BELOW the clashing one.
    prev = _c("A", "x", key=(8, "A"))
    unknown = _c("B", "y", key=None, bpm=None)
    clash = _c("C", "z", key=(2, "A"), bpm=None)   # 6 steps from 8A -> harmonic 0.10
    s_unknown, _ = score_slot(unknown, prev, _ctx())
    s_clash, _ = score_slot(clash, prev, _ctx())
    assert s_unknown > s_clash
    # the gap is exactly the harmonic weight * (0.5 - 0.10)
    assert abs((s_unknown - s_clash) - 0.20 * (0.5 - 0.10)) < 1e-9


def test_reason_cites_follows_history():
    prev = _c("A", "x", key=(8, "A"))
    cand = _c("B", "y", key=(8, "A"), plays=2)
    ctx = _ctx(follows={prev.norm_key: {cand.norm_key: 4}})
    _, reason = score_slot(cand, prev, ctx)
    assert "4 set" in reason


def test_reason_flags_missing_key():
    prev = _c("A", "x", key=(8, "A"))
    cand = _c("B", "y", key=None, plays=2)
    _, reason = score_slot(cand, prev, _ctx())
    assert "no key tag" in reason


def test_reason_names_camelot_move():
    prev = _c("A", "x", key=(8, "A"))
    cand = _c("B", "y", key=(9, "A"), plays=2)
    _, reason = score_slot(cand, prev, _ctx())
    assert "8A" in reason and "9A" in reason


def test_anchor_affinity_prefers_harmonic_handover():
    from librarian.setplan.score import anchor_affinity
    anchor = _c("X", "anchor", bpm=120.0, key=(5, "A"))
    near = _c("A", "near", bpm=120.0, key=(6, "A"))      # 1 step from 5A
    far = _c("B", "far", bpm=120.0, key=(11, "A"))       # 6 steps
    assert anchor_affinity(near, anchor, mode="loose") > anchor_affinity(far, anchor, mode="loose")
    unknown = _c("C", "unk", bpm=None, key=None)
    a = anchor_affinity(unknown, anchor, mode="loose")   # neutral, never zero
    assert a == 0.5 * 0.5 + 0.25 * 0.5
