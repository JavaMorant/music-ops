# tests/test_setplan_search.py
from __future__ import annotations
from librarian.setplan.pool import Candidate, norm
from librarian.setplan.spec import GigSpec
from librarian.setplan.search import build_set


def _c(artist, title, bpm=112.0, key=(8, "A"), genre="amapiano", plays=1, length=180.0):
    return Candidate(path=f"/lib/{title}.mp3", artist=artist, title=title, genre=genre, bpm=bpm,
                     camelot=key, length_s=length, norm_key=norm(f"{artist} {title}"),
                     low_bitrate=False, plays=plays)


def _pool(n):
    keys = [(8, "A"), (9, "A"), (10, "A"), (8, "B")]
    return [_c(f"Artist{i}", f"Track{i}", bpm=108 + i, key=keys[i % 4]) for i in range(n)]


def test_build_set_has_requested_length_and_no_immediate_repeats():
    spec = GigSpec(minutes=30, tracks_per_hour=20, journey=[("amapiano", 1.0)])
    plan = build_set(_pool(20), spec)
    assert len(plan.slots) == spec.n_slots() == 10
    picked = [s.candidate.path for s in plan.slots]
    assert len(set(picked)) == len(picked)                     # no track used twice
    # no artist twice within 6 slots
    for i in range(len(plan.slots)):
        window = [plan.slots[j].candidate.artist for j in range(max(0, i - 5), i)]
        assert plan.slots[i].candidate.artist not in window


def test_must_play_appears_as_anchor():
    pool = _pool(20) + [_c("SpecialArtist", "Mnike", key=(8, "A"))]
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)], must_play=["Mnike"])
    plan = build_set(pool, spec)
    titles = [s.candidate.title for s in plan.slots]
    assert "Mnike" in titles
    anchor = next(s for s in plan.slots if s.candidate.title == "Mnike")
    assert "anchor" in anchor.reason.lower()


def test_opener_is_forced_first():
    pool = _pool(20) + [_c("Opener", "FirstOne", key=(8, "A"))]
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)], opener="FirstOne")
    plan = build_set(pool, spec)
    assert plan.slots[0].candidate.title == "FirstOne"


def test_multiple_must_plays_all_placed_no_overwrite():
    # More must-plays than the peak region has free slots must NOT silently drop any.
    pool = _pool(20)
    musts = [f"Track{i}" for i in range(6)]      # 6 must-plays in a 10-slot set
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)], must_play=musts)
    plan = build_set(pool, spec)
    titles = {s.candidate.title for s in plan.slots}
    for mt in musts:
        assert mt in titles


def test_no_artist_repeat_within_6_with_duplicate_artists():
    # One artist owns half the pool: the hard filter must keep that artist out of
    # the trailing-6 window (fallback only if nothing else is available).
    keys = [(8, "A"), (9, "A"), (10, "A"), (8, "B")]
    pool = [_c("Dominant" if i % 2 == 0 else f"Other{i}", f"T{i}",
               bpm=110 + (i % 5), key=keys[i % 4]) for i in range(20)]
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)])
    plan = build_set(pool, spec)
    for i in range(len(plan.slots)):
        window = [plan.slots[j].candidate.artist for j in range(max(0, i - 5), i)]
        assert plan.slots[i].candidate.artist not in window
