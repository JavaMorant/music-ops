# tests/test_setplan_spec.py
from __future__ import annotations
from librarian.setplan.spec import GigSpec, target_percentile, genre_block_at


def test_n_slots_and_defaults():
    s = GigSpec(minutes=90)
    assert s.n_slots() == 30              # 90/60 * 20
    assert s.arc == "peak" and s.freshness == 0.3


def test_arc_curve_monotonic_endpoints():
    assert target_percentile("warmup", 0.0) < target_percentile("warmup", 1.0)
    assert target_percentile("closing", 0.0) > target_percentile("closing", 1.0)
    mid = target_percentile("peak", 0.5)
    assert 0.8 <= mid <= 0.95             # plateau in the peak


def test_genre_block_at_splits_by_fraction():
    s = GigSpec(minutes=60, journey=[("amapiano", 0.6), ("afrobeats", 0.4)])
    assert genre_block_at(s, 0.1) == "amapiano"
    assert genre_block_at(s, 0.8) == "afrobeats"
    assert genre_block_at(GigSpec(minutes=60), 0.5) is None


def test_parse_journey_moved_public():
    from librarian.setplan.spec import parse_journey
    assert parse_journey("amapiano:60,afrobeats:40") == [("amapiano", 0.6), ("afrobeats", 0.4)]


def test_catalogue_only_defaults_true():
    # The new scope toggle defaults to today's behavior: tracks you own.
    assert GigSpec(minutes=30).catalogue_only is True
    assert GigSpec(minutes=30, catalogue_only=False).catalogue_only is False
