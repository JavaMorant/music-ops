"""Guest-USB output: genre .m3u8 + Low/Unrated split (rating wins over genre)."""

from __future__ import annotations

from pathlib import Path

from librarian import classify_v2
from librarian.dedup_v2 import FileInfo
from librarian.organise import OrganiseResult, build_usb_playlists


def _keeper(artist, title):
    return FileInfo(path=Path(f"/Volumes/D_MI/Contents/{artist}/{title}.mp3"),
                    artist=artist, title=title)


def test_low_unrated_wins_over_genre(tmp_path):
    k_rap = _keeper("Drake", "Headlines")
    k_lowstar = _keeper("X", "B-side")
    k_unrated = _keeper("Y", "Demo")
    genres = {
        classify_v2.track_key("Drake", "Headlines"): "US Rap",
        classify_v2.track_key("X", "B-side"): "US Rap",
        classify_v2.track_key("Y", "Demo"): "Pop",
    }
    res = OrganiseResult(root=Path("/Volumes/D_MI"), total=3,
                         dedup={}, keepers=[k_rap, k_lowstar, k_unrated],
                         genres=genres, used_ai=True)
    ratings = {str(k_rap.path): 4, str(k_lowstar.path): 1, str(k_unrated.path): 0}

    out = build_usb_playlists(res, tmp_path, ratings=ratings)
    assert out["by_bucket"]["US Rap"] == 1          # only the 4-star rap track
    assert out["by_bucket"]["Low _ Unrated"] == 2   # the 1-star and the unrated

    m3u = (tmp_path / "US Rap.m3u8").read_text()
    assert m3u.startswith("#EXTM3U")
    assert "Drake - Headlines" in m3u
    assert "/Volumes/D_MI/Contents/Drake/Headlines.mp3" in m3u
    # the low/unrated tracks are NOT in the genre playlist
    assert "B-side" not in m3u and "Demo" not in m3u


def test_unrated_default_when_no_ratings(tmp_path):
    k = _keeper("A", "Song")
    res = OrganiseResult(root=Path("/x"), total=1, dedup={},
                         keepers=[k], genres={classify_v2.track_key("A", "Song"): "Pop"},
                         used_ai=True)
    out = build_usb_playlists(res, tmp_path)  # no ratings -> all unrated
    assert out["by_bucket"].get("Low _ Unrated") == 1
    assert "Pop" not in out["by_bucket"]
