"""End-to-end inbox trials: a byte-for-byte round-trip through the real engine,
and a rekordbox add + undo against a realistic export."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from librarian import inbox as inbox_mod
from librarian.engine import apply_plan, undo_run
from librarian.inbox import build_inbox_plan
from librarian.metadata import TrackMeta
from librarian.model import MOVE
from librarian.rekordbox import location_to_path, path_to_location

from conftest import tree_digest


def _fake_meta(specs: dict[str, dict]):
    def reader(path: Path) -> TrackMeta:
        return TrackMeta(path=path, **specs.get(path.name, {}))
    return reader


def test_inbox_roundtrip_byte_for_byte(tmp_path: Path, monkeypatch):
    root = tmp_path / "lib"
    inbox = root / "Inbox"
    inbox.mkdir(parents=True)
    (root / "Existing - Track.mp3").write_bytes(b"already-here")
    (inbox / "drop1.mp3").write_bytes(b"new-one")
    (inbox / "drop2.mp3").write_bytes(b"new-two")
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(
        inbox_mod, "read_meta",
        _fake_meta({
            "drop1.mp3": dict(artist="Burna Boy", title="Tata", genre="Afrobeats"),
            "drop2.mp3": dict(artist="Avicii", title="Levels", genre="Dance"),
        }),
    )

    before = tree_digest(root)
    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=True)
    assert len([a for a in plan.actions if a.kind == MOVE]) == 2

    journal = apply_plan(plan, runs_dir)
    after = tree_digest(root)
    assert after != before, "apply must file the dropped tracks"
    # Same bytes, just relocated into the library — nothing lost.
    assert sorted(after.values()) == sorted(before.values())
    assert (root / "Afrobeats" / "Burna Boy - Tata.mp3").exists()
    assert not (inbox / "drop1.mp3").exists()

    undo_run(journal.run_id, runs_dir)
    assert tree_digest(root) == before, "undo must put the drop back, byte-for-byte"
    assert (inbox / "drop1.mp3").exists()


def _existing_xml(path: Path, tracks: list[tuple[int, Path]]) -> None:
    rows = ""
    for tid, p in tracks:
        rows += (
            f'    <TRACK TrackID="{tid}" Name="t{tid}" Artist="X" Genre="House" '
            f'Kind="MP3 File" AverageBpm="120.00" Tonality="1A" '
            f'Location="{path_to_location(p.absolute())}">\n'
            f'      <TEMPO Inizio="0.0" Bpm="120.00" Metro="4/4" Battito="1"/>\n'
            f'      <POSITION_MARK Name="Cue" Type="0" Start="1.0" Num="0"/>\n'
            f"    </TRACK>\n"
        )
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<DJ_PLAYLISTS Version="1.0.0">\n'
        '  <PRODUCT Name="rekordbox" Version="6.6.1"/>\n'
        f'  <COLLECTION Entries="{len(tracks)}">\n{rows}  </COLLECTION>\n'
        '  <PLAYLISTS>\n    <NODE Name="ROOT" Type="0" Count="0"/>\n  </PLAYLISTS>\n'
        "</DJ_PLAYLISTS>\n",
        encoding="utf-8",
    )


def _index(xml: Path) -> dict[int, dict]:
    coll = ET.parse(xml).getroot().find("COLLECTION")
    return {
        int(t.get("TrackID")): {
            "location": t.get("Location"),
            "children": [(c.tag, dict(c.attrib)) for c in t],
            "tonality": t.get("Tonality"),
        }
        for t in coll.findall("TRACK")
    }


def test_inbox_rekordbox_add_and_undo(tmp_path: Path, monkeypatch):
    root = tmp_path / "lib"
    inbox = root / "Inbox"
    inbox.mkdir(parents=True)
    existing = root / "Existing - Track.mp3"
    existing.write_bytes(b"already-here")
    (inbox / "n1.mp3").write_bytes(b"new-keyed")
    (inbox / "n2.mp3").write_bytes(b"new-nokey")
    runs_dir = tmp_path / "runs"
    xml = tmp_path / "rb.xml"
    _existing_xml(xml, [(1, existing)])
    original_bytes = xml.read_bytes()
    before = _index(xml)

    monkeypatch.setattr(
        inbox_mod, "read_meta",
        _fake_meta({
            "n1.mp3": dict(artist="DJ A", title="Keyed", genre="House", key="5A", bitrate_kbps=320),
            "n2.mp3": dict(artist="DJ B", title="NoKey", genre="Techno", bitrate_kbps=320),
        }),
    )

    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=True, rekordbox_xml=xml, playlist_name="New This Week")
    journal = apply_plan(plan, runs_dir)

    after = _index(xml)
    # Two new tracks added with fresh TrackIDs (max(1)+1..).
    new_ids = sorted(set(after) - set(before))
    assert new_ids == [2, 3]
    assert after[1]["children"] == before[1]["children"], "existing track's cues untouched"

    # New Locations resolve to the filed library paths, which exist.
    for tid in new_ids:
        assert location_to_path(after[tid]["location"]).exists()
    # Tonality written only for the tagged track.
    tonalities = {after[t]["location"]: after[t]["tonality"] for t in new_ids}
    keyed = path_to_location((root / "House" / "DJ A - Keyed.mp3").absolute())
    nokey = path_to_location((root / "Techno" / "DJ B - NoKey.mp3").absolute())
    assert tonalities[keyed] == "5A"
    assert tonalities[nokey] is None

    # COLLECTION Entries bumped; New This Week playlist created, TrackID-keyed.
    coll = ET.parse(xml).getroot().find("COLLECTION")
    assert coll.get("Entries") == "3"
    node = next(n for n in ET.parse(xml).getroot().find("PLAYLISTS").iter("NODE") if n.get("Name") == "New This Week")
    assert node.get("Type") == "1" and node.get("KeyType") == "0"
    assert sorted(int(t.get("Key")) for t in node.findall("TRACK")) == [2, 3]

    # Undo restores the collection byte-for-byte (additions + playlist gone).
    undo_run(journal.run_id, runs_dir)
    assert xml.read_bytes() == original_bytes


def _xml_with_existing_playlist(path: Path, existing: Path) -> None:
    """An export that already has a TrackID-keyed 'New This Week' (the normal
    state on every run after the first)."""
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<DJ_PLAYLISTS Version="1.0.0">\n'
        '  <PRODUCT Name="rekordbox" Version="6.6.1"/>\n'
        '  <COLLECTION Entries="1">\n'
        f'    <TRACK TrackID="1" Name="t1" Location="{path_to_location(existing.absolute())}"/>\n'
        "  </COLLECTION>\n"
        '  <PLAYLISTS>\n    <NODE Name="ROOT" Type="0" Count="1">\n'
        '      <NODE Name="New This Week" Type="1" KeyType="0" Entries="1">\n'
        '        <TRACK Key="1"/>\n      </NODE>\n    </NODE>\n  </PLAYLISTS>\n'
        "</DJ_PLAYLISTS>\n",
        encoding="utf-8",
    )


def test_inbox_appends_to_existing_playlist_with_unicode_name(tmp_path: Path, monkeypatch):
    """The recurring case: 'New This Week' already exists; a new track with an
    accented (NFD on disk) name is appended, its Location round-trips, undo is
    byte-for-byte."""
    import unicodedata

    root = tmp_path / "lib"
    inbox = root / "Inbox"
    inbox.mkdir(parents=True)
    existing = root / "Existing.mp3"
    existing.write_bytes(b"already-here")
    # Filename composed on disk however the FS stores it; the tag is accented.
    (inbox / "drop.mp3").write_bytes(b"new-accented")
    runs_dir = tmp_path / "runs"
    xml = tmp_path / "rb.xml"
    _xml_with_existing_playlist(xml, existing)
    original_bytes = xml.read_bytes()

    title = unicodedata.normalize("NFC", "Aseréjé")
    monkeypatch.setattr(
        inbox_mod, "read_meta",
        _fake_meta({"drop.mp3": dict(artist="Las Ketchup", title=title, genre="Pop", key="1A")}),
    )

    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=True, rekordbox_xml=xml, playlist_name="New This Week")
    journal = apply_plan(plan, runs_dir)

    root_xml = ET.parse(xml).getroot()
    node = next(n for n in root_xml.find("PLAYLISTS").iter("NODE") if n.get("Name") == "New This Week")
    keys = sorted(int(t.get("Key")) for t in node.findall("TRACK"))
    assert keys == [1, 2], "appended to the existing playlist, not replaced"
    # The new accented Location resolves to a real file on disk.
    new_track = next(t for t in root_xml.find("COLLECTION").findall("TRACK") if t.get("TrackID") == "2")
    assert location_to_path(new_track.get("Location")).exists()

    undo_run(journal.run_id, runs_dir)
    assert xml.read_bytes() == original_bytes
