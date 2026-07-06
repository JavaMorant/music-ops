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
