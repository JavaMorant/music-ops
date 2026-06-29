"""Floor-fillers / recs must never include a 0-star track."""

from __future__ import annotations

from dataclasses import dataclass

from librarian.rekordbox_sync import _rated_cids


@dataclass
class FakeContent:
    ID: int
    Rating: int


def _by_title(*tracks):
    return {f"k{c.ID}": [c] for c in tracks}


def test_zero_star_excluded():
    rated = FakeContent(1, 4)
    unrated = FakeContent(2, 0)
    by_title = _by_title(rated, unrated)
    cids = _rated_cids(["k1", "k2"], by_title, min_rating=1)
    assert cids == [1]  # the 0-star track is dropped


def test_missing_rating_attr_treated_as_zero():
    class NoRating:
        ID = 9  # no Rating attribute at all
    cids = _rated_cids(["k9"], {"k9": [NoRating()]}, min_rating=1)
    assert cids == []


def test_higher_threshold_filters_more():
    low = FakeContent(1, 2)
    high = FakeContent(2, 4)
    by_title = _by_title(low, high)
    assert _rated_cids(["k1", "k2"], by_title, min_rating=3) == [2]


def test_unknown_key_skipped_safely():
    assert _rated_cids(["missing"], {}, min_rating=1) == []
