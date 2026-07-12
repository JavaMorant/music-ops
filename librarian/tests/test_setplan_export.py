# tests/test_setplan_export.py
from __future__ import annotations
from librarian.setplan.pool import Candidate, norm
from librarian.setplan.spec import GigSpec
from librarian.setplan.search import Slot, SetPlan
from librarian.setplan.export import to_m3u8, to_markdown


def _plan():
    c = Candidate(path="/lib/Mnike.mp3", artist="Tyler ICU", title="Mnike", genre="amapiano",
                  bpm=112.0, camelot=(8, "A"), length_s=200.0, norm_key=norm("Tyler ICU Mnike"),
                  low_bitrate=False, plays=3)
    slot = Slot(index=0, candidate=c, reason="⭐ anchor (must-play)", clock_min=0.0)
    return SetPlan(spec=GigSpec(minutes=30), slots=[slot])


def test_m3u8_lists_track_paths(tmp_path):
    out = tmp_path / "set.m3u8"
    to_m3u8(_plan(), out)
    text = out.read_text()
    assert text.startswith("#EXTM3U")
    assert "/lib/Mnike.mp3" in text
    assert "Tyler ICU - Mnike" in text


def test_markdown_has_reasons_and_badges(tmp_path):
    out = tmp_path / "set.md"
    to_markdown(_plan(), out)
    text = out.read_text()
    assert "Mnike" in text and "8A" in text and "112" in text
    assert "anchor" in text.lower()


def _xml(tmp_path, locations):
    import xml.etree.ElementTree as ET
    from librarian.rekordbox import path_to_location
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    coll = ET.SubElement(root, "COLLECTION", {"Entries": str(len(locations))})
    for i, loc in enumerate(locations, start=1):
        ET.SubElement(coll, "TRACK", {"TrackID": str(i), "Name": loc.stem,
                                      "Location": path_to_location(loc)})
    ET.SubElement(root, "PLAYLISTS")
    p = tmp_path / "collection.xml"
    ET.ElementTree(root).write(p, encoding="utf-8", xml_declaration=True)
    return p


def test_rekordbox_setlist_orders_existing_and_adds_missing(tmp_path):
    import xml.etree.ElementTree as ET
    from librarian.setplan.export import to_rekordbox_xml
    from librarian.setplan.search import Slot, SetPlan
    from librarian.setplan.spec import GigSpec
    from librarian.setplan.pool import Candidate, norm

    existing = tmp_path / "lib" / "Known.mp3"
    missing = tmp_path / "lib" / "New.mp3"
    existing.parent.mkdir()
    existing.write_bytes(b"0"); missing.write_bytes(b"0")
    xml_in = _xml(tmp_path, [existing])

    def cand(p, key):
        return Candidate(path=p, artist="A", title=p.stem, genre="amapiano", bpm=120.0,
                         camelot=key, length_s=180.0, norm_key=norm(f"A {p.stem}"),
                         low_bitrate=False)
    plan = SetPlan(spec=GigSpec(minutes=6), slots=[
        Slot(index=0, candidate=cand(missing, (8, "A")), reason="r"),
        Slot(index=1, candidate=cand(existing, (9, "A")), reason="r"),
    ])
    out = tmp_path / "setplan.rekordbox.xml"
    added, entries = to_rekordbox_xml(plan, xml_in, out, "setplan test")
    assert (added, entries) == (1, 2)

    tree = ET.parse(out)
    coll = tree.getroot().find("COLLECTION")
    assert len(coll.findall("TRACK")) == 2               # Known kept, New added once
    node = next(n for n in tree.getroot().find("PLAYLISTS").iter("NODE")
                if n.get("Name") == "setplan test")
    keys = [t.get("Key") for t in node.findall("TRACK")]
    by_id = {t.get("TrackID"): t.get("Name") for t in coll.findall("TRACK")}
    assert [by_id[k] for k in keys] == ["New", "Known"]  # SET ORDER preserved

    added2, entries2 = to_rekordbox_xml(plan, out, out, "setplan test")
    assert added2 == 0 and entries2 == 2                 # idempotent re-export
