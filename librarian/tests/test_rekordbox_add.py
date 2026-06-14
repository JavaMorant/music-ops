"""Unit tests for rekordbox.add_tracks_and_playlist — adding new tracks to the
collection and a TrackID-keyed 'New This Week' playlist."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from librarian.model import RekordboxAddition
from librarian.rekordbox import add_tracks_and_playlist, location_to_path


def _collection_xml(tmp_path: Path, tracks: list[tuple], with_playlists: bool = True) -> Path:
    """tracks: list of (track_id_str_or_None, location_or_None)."""
    rows = []
    for tid, loc in tracks:
        attrs = []
        if tid is not None:
            attrs.append(f'TrackID="{tid}"')
        if loc is not None:
            attrs.append(f'Location="{loc}"')
        rows.append(f'    <TRACK {" ".join(attrs)}/>')
    body = "\n".join(rows)
    playlists = (
        "  <PLAYLISTS>\n    <NODE Name=\"ROOT\" Type=\"0\" Count=\"0\"/>\n  </PLAYLISTS>\n"
        if with_playlists
        else ""
    )
    xml = tmp_path / "c.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<DJ_PLAYLISTS Version="1.0.0">\n'
        f'  <COLLECTION Entries="{len(tracks)}">\n{body}\n  </COLLECTION>\n{playlists}'
        "</DJ_PLAYLISTS>\n",
        encoding="utf-8",
    )
    return xml


def _add(loc: str, **kw) -> RekordboxAddition:
    return RekordboxAddition(location=Path(loc), name=kw.pop("name", "X"), **kw)


def _collection(xml: Path) -> ET.Element:
    return ET.parse(xml).getroot().find("COLLECTION")


def _track_ids(xml: Path) -> list[int]:
    return [int(t.get("TrackID")) for t in _collection(xml).findall("TRACK")
            if t.get("TrackID") and t.get("TrackID").isdigit()]


def test_add_assigns_max_id_plus_one(tmp_path: Path):
    xml = _collection_xml(tmp_path, [("1", "file://localhost/a.mp3"), ("5", "file://localhost/b.mp3"), ("12", "file://localhost/c.mp3")])
    n = add_tracks_and_playlist(xml, [_add("/x/new1.mp3"), _add("/x/new2.mp3")], None)
    assert n == 2
    ids = _track_ids(xml)
    assert 13 in ids and 14 in ids
    assert _collection(xml).get("Entries") == "5"


def test_add_empty_collection_starts_at_one(tmp_path: Path):
    xml = _collection_xml(tmp_path, [])
    add_tracks_and_playlist(xml, [_add("/x/first.mp3")], None)
    assert _track_ids(xml) == [1]


def test_add_guards_non_integer_track_ids(tmp_path: Path):
    xml = _collection_xml(tmp_path, [("ABC", "file://localhost/a.mp3"), ("3", "file://localhost/b.mp3")])
    add_tracks_and_playlist(xml, [_add("/x/new.mp3")], None)
    ids = _track_ids(xml)
    assert 4 in ids  # max of the parseable ids (3) + 1


def test_add_creates_trackid_keyed_playlist(tmp_path: Path):
    xml = _collection_xml(tmp_path, [("1", "file://localhost/a.mp3")])
    add_tracks_and_playlist(xml, [_add("/x/n1.mp3"), _add("/x/n2.mp3")], "New This Week")
    root = ET.parse(xml).getroot()
    node = next(n for n in root.find("PLAYLISTS").iter("NODE") if n.get("Name") == "New This Week")
    assert node.get("Type") == "1" and node.get("KeyType") == "0"
    keys = sorted(int(t.get("Key")) for t in node.findall("TRACK"))
    assert keys == [2, 3]
    assert node.get("Entries") == "2"


def test_add_appends_to_existing_playlist(tmp_path: Path):
    xml = _collection_xml(tmp_path, [("1", "file://localhost/a.mp3")], with_playlists=False)
    # Hand-build a PLAYLISTS with an existing New This Week node holding one entry.
    text = xml.read_text(encoding="utf-8").replace(
        "</DJ_PLAYLISTS>",
        '  <PLAYLISTS>\n    <NODE Name="ROOT" Type="0" Count="1">\n'
        '      <NODE Name="New This Week" Type="1" KeyType="0" Entries="1">\n'
        '        <TRACK Key="1"/>\n      </NODE>\n    </NODE>\n  </PLAYLISTS>\n</DJ_PLAYLISTS>',
    )
    xml.write_text(text, encoding="utf-8")
    add_tracks_and_playlist(xml, [_add("/x/n1.mp3")], "New This Week")
    node = next(n for n in ET.parse(xml).getroot().find("PLAYLISTS").iter("NODE") if n.get("Name") == "New This Week")
    keys = sorted(int(t.get("Key")) for t in node.findall("TRACK"))
    assert keys == [1, 2] and node.get("Entries") == "2"


def test_add_is_idempotent_on_location(tmp_path: Path):
    xml = _collection_xml(tmp_path, [("1", "file://localhost/a.mp3")])
    adds = [_add("/x/new.mp3")]
    assert add_tracks_and_playlist(xml, adds, "New This Week") == 1
    # Second run with the same addition adds nothing — no duplicate TRACK/Key.
    assert add_tracks_and_playlist(xml, adds, "New This Week") == 0
    locs = [t.get("Location") for t in _collection(xml).findall("TRACK")]
    assert sum(location_to_path(l) == Path("/x/new.mp3") for l in locs if l) == 1
    node = next(n for n in ET.parse(xml).getroot().find("PLAYLISTS").iter("NODE") if n.get("Name") == "New This Week")
    assert node.get("Entries") == "1"


def test_add_preserves_location_keyed_playlist(tmp_path: Path):
    """A pre-existing location-keyed 'New This Week' must NOT get TrackID keys
    injected into it — that would corrupt it. A fresh TrackID-keyed node is made."""
    xml = _collection_xml(tmp_path, [("1", "file://localhost/a.mp3")], with_playlists=False)
    text = xml.read_text(encoding="utf-8").replace(
        "</DJ_PLAYLISTS>",
        '  <PLAYLISTS>\n    <NODE Name="ROOT" Type="0" Count="1">\n'
        '      <NODE Name="New This Week" Type="1" KeyType="1" Entries="1">\n'
        '        <TRACK Key="file://localhost/a.mp3"/>\n      </NODE>\n    </NODE>\n  </PLAYLISTS>\n</DJ_PLAYLISTS>',
    )
    xml.write_text(text, encoding="utf-8")
    add_tracks_and_playlist(xml, [_add("/x/new.mp3")], "New This Week")

    nodes = [n for n in ET.parse(xml).getroot().find("PLAYLISTS").iter("NODE") if n.get("Name") == "New This Week"]
    loc_node = next(n for n in nodes if n.get("KeyType") == "1")
    # The location-keyed node is untouched: still one entry, still a file:// Key.
    assert [t.get("Key") for t in loc_node.findall("TRACK")] == ["file://localhost/a.mp3"]
    # A separate TrackID-keyed node holds the new track.
    tid_node = next(n for n in nodes if n.get("KeyType") == "0")
    assert [t.get("Key") for t in tid_node.findall("TRACK")] == ["2"]


def test_add_ignores_nested_same_named_playlist(tmp_path: Path):
    """A 'New This Week' nested in an unrelated folder must not be appended to;
    a top-level one is created instead (deterministic placement)."""
    xml = _collection_xml(tmp_path, [("1", "file://localhost/a.mp3")], with_playlists=False)
    text = xml.read_text(encoding="utf-8").replace(
        "</DJ_PLAYLISTS>",
        '  <PLAYLISTS>\n    <NODE Name="ROOT" Type="0" Count="1">\n'
        '      <NODE Name="Folder" Type="0" Count="1">\n'
        '        <NODE Name="New This Week" Type="1" KeyType="0" Entries="0"/>\n'
        '      </NODE>\n    </NODE>\n  </PLAYLISTS>\n</DJ_PLAYLISTS>',
    )
    xml.write_text(text, encoding="utf-8")
    add_tracks_and_playlist(xml, [_add("/x/new.mp3")], "New This Week")
    root_node = next(n for n in ET.parse(xml).getroot().find("PLAYLISTS") if n.get("Type") == "0")
    # A direct child of ROOT now holds the import; the nested one stays empty.
    top = [n for n in root_node.findall("NODE") if n.get("Name") == "New This Week"]
    assert len(top) == 1 and [t.get("Key") for t in top[0].findall("TRACK")] == ["2"]


def test_add_trackid_clears_dangling_playlist_keys(tmp_path: Path):
    """A playlist Key above the collection's max TrackID must not be reused for a
    new track (that would silently enrol it in that playlist)."""
    xml = _collection_xml(tmp_path, [("1", "file://localhost/a.mp3")], with_playlists=False)
    text = xml.read_text(encoding="utf-8").replace(
        "</DJ_PLAYLISTS>",
        '  <PLAYLISTS>\n    <NODE Name="ROOT" Type="0" Count="1">\n'
        '      <NODE Name="Old" Type="1" KeyType="0" Entries="1">\n'
        '        <TRACK Key="99"/>\n      </NODE>\n    </NODE>\n  </PLAYLISTS>\n</DJ_PLAYLISTS>',
    )
    xml.write_text(text, encoding="utf-8")
    add_tracks_and_playlist(xml, [_add("/x/new.mp3")], "New This Week")
    ids = _track_ids(xml)
    assert 100 in ids and 99 not in [i for i in ids]  # next id is max(1,99)+1


def test_add_writes_tonality_only_when_present(tmp_path: Path):
    xml = _collection_xml(tmp_path, [("1", "file://localhost/a.mp3")])
    add_tracks_and_playlist(
        xml,
        [_add("/x/keyed.mp3", name="K", tonality="8A"), _add("/x/nokey.mp3", name="N")],
        None,
    )
    by_name = {t.get("Name"): t for t in _collection(xml).findall("TRACK")}
    assert by_name["K"].get("Tonality") == "8A"
    assert by_name["N"].get("Tonality") is None
