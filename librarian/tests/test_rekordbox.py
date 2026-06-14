"""rekordbox cue safety: Location encoding round-trips, and a rewrite touches
only the moved tracks.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

from librarian.rekordbox import location_to_path, path_to_location, rewrite_locations


def test_location_path_roundtrip():
    # A path with spaces, punctuation and unicode — typical DJ filenames.
    p = Path("/Users/dj/Music/Burna Boy ft Travis Scott - TaTaTa (Intro Dirty).mp3")
    loc = path_to_location(p)
    assert loc.startswith("file://localhost/")
    assert " " not in loc, "spaces must be percent-encoded"
    assert location_to_path(loc) == p

    p2 = Path("/Users/dj/Música/Aserejé.mp3")
    assert location_to_path(path_to_location(p2)) == p2


def _xml_with(tmp_path: Path, paths: list[Path]) -> Path:
    rows = "\n".join(
        f'  <TRACK TrackID="{i}" Location="{path_to_location(p)}"/>'
        for i, p in enumerate(paths, 1)
    )
    xml = tmp_path / "c.xml"
    xml.write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<DJ_PLAYLISTS>\n<COLLECTION>\n{rows}\n'
        "</COLLECTION>\n</DJ_PLAYLISTS>\n",
        encoding="utf-8",
    )
    return xml


def test_rewrite_only_touches_mapped_tracks(tmp_path: Path):
    a = tmp_path / "old" / "a.mp3"
    b = tmp_path / "old" / "b.mp3"
    a_new = tmp_path / "new" / "a-clean.mp3"
    xml = _xml_with(tmp_path, [a, b])

    n = rewrite_locations(xml, {a: a_new}, xml)
    assert n == 1, "exactly one track was remapped"

    text = xml.read_text(encoding="utf-8")
    assert path_to_location(a_new) in text, "moved track points at its new path"
    assert path_to_location(b) in text, "untouched track's Location is preserved"
    assert path_to_location(a) not in text, "the dead path is gone"


def test_rewrite_matches_across_unicode_normalization(tmp_path: Path):
    """An NFD Location (macOS filesystem style) must match an NFC path_map key
    (tag/plan style) — otherwise accented/emoji tracks keep dead Locations."""
    name_nfc = unicodedata.normalize("NFC", "Aseréjé.mp3")
    name_nfd = unicodedata.normalize("NFD", "Aseréjé.mp3")
    assert name_nfc != name_nfd  # they really differ as strings

    old_nfd = tmp_path / "old" / name_nfd
    new = tmp_path / "new" / "Aseréjé (clean).mp3"
    xml = _xml_with(tmp_path, [old_nfd])

    # path_map keyed with the COMPOSED form; Location on disk is DECOMPOSED.
    n = rewrite_locations(xml, {tmp_path / "old" / name_nfc: new}, xml)
    assert n == 1, "NFC key must match the NFD Location"
    assert path_to_location(new) in xml.read_text(encoding="utf-8")
