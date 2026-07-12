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


from librarian.cli import _parse_journey


def test_parse_journey_string():
    assert _parse_journey("amapiano:60,afrobeats:40") == [("amapiano", 0.6), ("afrobeats", 0.4)]
    assert _parse_journey("") == []
    assert _parse_journey("house") == [("house", 1.0)]


def test_unmatched_must_and_opener_are_reported():
    # A typo'd must-play must never vanish silently — it surfaces on the plan.
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)],
                   must_play=["No Such Track"], opener="Ghost Opener")
    plan = build_set(_pool(20), spec)
    assert "No Such Track" in plan.unmatched
    assert "Ghost Opener" in plan.unmatched
    assert len(plan.slots) == spec.n_slots()   # the set still builds


def _k(artist, title, key, bpm=120.0):
    # identical genre/bpm/plays so ONLY harmonic distance differentiates
    return _c(artist, title, bpm=bpm, key=key)


def test_beam_routes_into_anchor_better_than_greedy():
    # 4 slots (12 min @ 20 tph), must-play anchor lands at slot 2 (int(4*0.5)).
    # From the 8A opener, greedy's locally-best chain strands it far from the
    # 5A anchor; beam(8) finds the 7A -> 6A route (6A -> 5A is a 0.90 handover).
    from librarian import camelot as C
    pool = [
        _k("Op", "Opener", (8, "A")),
        _k("A1", "Nine", (9, "A")), _k("A2", "Ten", (10, "A")),
        _k("B1", "Seven", (7, "A")), _k("B2", "Six", (6, "A")),
        _k("Anch", "Anchor", (5, "A")),
    ]
    spec = GigSpec(minutes=12, journey=[("amapiano", 1.0)],
                   opener="Opener", must_play=["Anchor"])
    greedy = build_set(pool, spec, beam_width=1)
    beam = build_set(pool, spec, beam_width=8)
    anchor_slot = next(i for i, s in enumerate(beam.slots) if s.candidate.title == "Anchor")
    g_prev = greedy.slots[anchor_slot - 1].candidate
    b_prev = beam.slots[anchor_slot - 1].candidate
    g_h = C.harmonic(g_prev.camelot, (5, "A"), rising=True)
    b_h = C.harmonic(b_prev.camelot, (5, "A"), rising=True)
    assert b_h >= g_h
    assert b_h >= 0.85          # beam genuinely lands a clean handover


def test_pinned_slot_is_respected_and_labelled():
    pool = _pool(20)
    target = pool[7]
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)])
    plan = build_set(pool, spec, pinned={3: str(target.path)})
    assert plan.slots[3].candidate.path == target.path
    assert "locked" in plan.slots[3].reason


def test_unresolvable_pin_reported_not_silent():
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)])
    plan = build_set(_pool(20), spec, pinned={2: "/nope/ghost.mp3"})
    assert "/nope/ghost.mp3" in plan.unmatched


def test_same_seed_same_plan():
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)], seed=42)
    a = build_set(_pool(30), spec, beam_width=8)
    b = build_set(_pool(30), spec, beam_width=8)
    assert [s.candidate.path for s in a.slots] == [s.candidate.path for s in b.slots]


def test_opener_displaced_by_pin_is_reported():
    pool = _pool(20)
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)], opener="Track5")
    plan = build_set(pool, spec, pinned={0: str(pool[9].path)})
    assert plan.slots[0].candidate.path == pool[9].path   # the pin held slot 0
    assert "Track5" in plan.unmatched                     # the opener request surfaced


def test_beam_multistep_lookahead_beats_greedy():
    # Segment of length 2 before a pinned 5A anchor at slot 3. Greedy's locally
    # best slot-1 pick ("X - Nine", 9A, 0.90 from the 8A opener) burns artist X,
    # so the artist-repeat window then hard-filters the perfect closer
    # ("X - Six", 6A -> 5A = 0.90 handover) out of slot 2, stranding greedy on a
    # 0.10 handover. Beam (width >= 2) keeps the locally weaker "Y - EightB"
    # (8B, 0.85) slot-1 path, preserving artist X for the 6A closer.
    pool = [
        _k("Op", "Opener", (8, "A")),
        _k("X", "Nine", (9, "A")),
        _k("X", "Six", (6, "A")),
        _k("Y", "EightB", (8, "B")),
        _k("Z", "Ten", (10, "A")),
        _k("Anch", "Anchor", (5, "A")),
    ]
    from librarian import camelot as C
    anchor_path = str(pool[5].path)
    spec = GigSpec(minutes=12, journey=[("amapiano", 1.0)], opener="Opener")
    greedy = build_set(pool, spec, beam_width=1, pinned={3: anchor_path})
    beam = build_set(pool, spec, beam_width=8, pinned={3: anchor_path})
    g_h = C.harmonic(greedy.slots[2].candidate.camelot, (5, "A"), rising=True)
    b_h = C.harmonic(beam.slots[2].candidate.camelot, (5, "A"), rising=True)
    assert b_h > g_h                      # STRICT: multi-step lookahead must win
    assert b_h >= 0.85
    assert beam.slots[2].candidate.title == "Six"
