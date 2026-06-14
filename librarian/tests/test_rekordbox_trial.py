"""Rekordbox cue-safety trial against a REALISTIC exported collection.

The minimal test in test_rekordbox.py proves the Location rewrite mechanism;
this is the dry-run trial the brief demands before trusting it on real data: a
full `DJ_PLAYLISTS` export shaped like rekordbox's own, driven through the real
cleanup -> apply -> undo flow, with both *structural* and *byte-level* checks.

It covers the cases a real export will actually hit:
  * moved tracks (mixed literal-space and percent-encoded Locations);
  * a track with NO cues / no beatgrid;
  * a track rekordbox tracks OUTSIDE this library (must stay byte-identical);
  * an exact-duplicate that gets quarantined — cues must repoint to the KEEPER,
    not follow the reject into _quarantine;
  * a TrackID-keyed playlist AND a location-keyed (KeyType="1") playlist inside
    a nested folder node.

Assertions prove only moved tracks change, cues/beatgrids/playlist references
survive (including verbatim bytes for untouched content), and undo restores the
collection byte-for-byte.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from librarian.cleanup import build_cleanup_plan
from librarian.engine import apply_plan, undo_run
from librarian.model import MOVE, QUARANTINE
from librarian.paths import QUARANTINE_DIRNAME
from librarian.rekordbox import location_to_path, path_to_location

from conftest import SAMPLE_LIBRARY

EXTERNAL_LOCATION = "file://localhost/Volumes/External/Some Other Set.mp3"


def _attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _literal_location(path: Path) -> str:
    """rekordbox often exports spaces unencoded — model that style verbatim."""
    return "file://localhost" + str(path.absolute())


def _track_with_cues(track_id: int, name: str, location: str) -> str:
    return (
        f'    <TRACK TrackID="{track_id}" Name="{_attr(name)}" Artist="A &amp; B" '
        f'Genre="House" Kind="MP3 File" TotalTime="240" AverageBpm="124.00" '
        f'Tonality="8A" BitRate="320" SampleRate="44100" Location="{_attr(location)}">\n'
        f'      <TEMPO Inizio="0.025" Bpm="124.00" Metro="4/4" Battito="1"/>\n'
        f'      <POSITION_MARK Name="Intro" Type="0" Start="5.0" Num="0"/>\n'
        f'      <POSITION_MARK Name="Drop" Type="0" Start="32.0" Num="1"/>\n'
        f'      <POSITION_MARK Name="Memory" Type="0" Start="90.5" Num="-1"/>\n'
        f'      <POSITION_MARK Name="Loop" Type="4" Start="60.0" End="124.0" Num="-1"/>\n'
        f"    </TRACK>\n"
    )


def _track_no_cues(track_id: int, name: str, location: str) -> str:
    return (
        f'    <TRACK TrackID="{track_id}" Name="{_attr(name)}" Kind="MP3 File" '
        f'Location="{_attr(location)}"/>\n'
    )


def _index(xml_path: Path) -> dict[int, dict]:
    """{TrackID: {location, children:[(tag, attrib)]}} for COLLECTION tracks."""
    collection = ET.parse(xml_path).getroot().find("COLLECTION")
    out: dict[int, dict] = {}
    for track in collection.findall("TRACK"):
        out[int(track.get("TrackID"))] = {
            "location": track.get("Location"),
            "children": [(c.tag, dict(c.attrib)) for c in track],
        }
    return out


def _playlist_track_keys(xml_path: Path, node_name: str) -> list[str]:
    root = ET.parse(xml_path).getroot()
    for node in root.find("PLAYLISTS").iter("NODE"):
        if node.get("Name") == node_name:
            return [t.get("Key") for t in node.findall("TRACK")]
    raise AssertionError(f"playlist node {node_name!r} not found")


@pytest.mark.skipif(not SAMPLE_LIBRARY.is_dir(), reason="testbed sample-library not present")
def test_rekordbox_trial_on_realistic_export(tmp_path: Path):
    work = tmp_path / "sample-library"
    shutil.copytree(SAMPLE_LIBRARY, work)
    runs_dir = tmp_path / "runs"
    xml = tmp_path / "rekordbox_export.xml"

    # Inject a byte-identical duplicate so cleanup quarantines one and keeps the
    # other — the case where cues must repoint to the keeper.
    originals = sorted(p for p in work.glob("*.mp3"))
    dup_source = originals[0]
    dup_copy = work / "ZZ Exact Duplicate.mp3"
    shutil.copy2(dup_source, dup_copy)

    plan, _ = build_cleanup_plan(work, organize_by_genre=True, rekordbox_xml=xml)
    moves = {a.src: a.dest for a in plan.actions if a.kind == MOVE}
    moved = list(moves)[:4]
    assert len(moved) == 4, "need four moved tracks for the trial"

    # The exact-duplicate quarantine and where rekordbox should be repointed.
    dup_q = next(a for a in plan.actions if a.kind == QUARANTINE and "exact duplicate" in a.reason)
    keeper_final = plan.location_redirects[dup_q.src]
    assert QUARANTINE_DIRNAME not in keeper_final.parts, "keeper must not be in quarantine"

    # Build the export: cues, a no-cue track, the external track, and the
    # duplicate (pointed at its pre-move path).
    coll = (
        _track_with_cues(1, "Track One", _literal_location(moved[0]))           # literal spaces
        + _track_with_cues(2, "Track Two", path_to_location(moved[1].absolute()))  # %-encoded
        + _track_with_cues(3, "Track Three", _literal_location(moved[2]))
        + _track_no_cues(4, "No Cues", _literal_location(moved[3]))
        + _track_with_cues(5, "Duplicate", _literal_location(dup_q.src))
        + _track_with_cues(99, "External Set", EXTERNAL_LOCATION)
    )
    # A TrackID-keyed playlist and a location-keyed one, in a nested folder.
    tid_keys = "".join(f'        <TRACK Key="{t}"/>\n' for t in (1, 2, 3, 4, 5, 99))
    loc_key = _attr(_literal_location(moved[0]))
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<DJ_PLAYLISTS Version="1.0.0">\n'
        '  <PRODUCT Name="rekordbox" Version="6.6.1" Company="AlphaTheta"/>\n'
        '  <COLLECTION Entries="6">\n' + coll + "  </COLLECTION>\n"
        "  <PLAYLISTS>\n"
        '    <NODE Name="ROOT" Type="0" Count="1">\n'
        '      <NODE Name="Crates" Type="0" Count="2">\n'
        '        <NODE Name="By ID" Type="1" KeyType="0" Entries="6">\n' + tid_keys + "        </NODE>\n"
        '        <NODE Name="By Path" Type="1" KeyType="1" Entries="1">\n'
        f'          <TRACK Key="{loc_key}"/>\n'
        "        </NODE>\n"
        "      </NODE>\n"
        "    </NODE>\n"
        "  </PLAYLISTS>\n"
        "</DJ_PLAYLISTS>\n",
        encoding="utf-8",
    )

    before_index = _index(xml)
    before_tid_keys = _playlist_track_keys(xml, "By ID")
    original_bytes = xml.read_bytes()

    apply_plan(plan, runs_dir)

    after_index = _index(xml)
    rewritten_text = xml.read_text(encoding="utf-8")

    # 1) Moved tracks (with and without cues) now point at their real new paths.
    for tid, src in zip((1, 2, 3, 4), moved):
        dest = moves[src].absolute()
        assert location_to_path(after_index[tid]["location"]).absolute() == dest
        assert dest.exists()

    # 2) The duplicate's cues repoint to the KEEPER (a live, non-quarantine file).
    dup_dest = location_to_path(after_index[5]["location"]).absolute()
    assert dup_dest == keeper_final.absolute()
    assert dup_dest.exists()
    assert QUARANTINE_DIRNAME not in dup_dest.parts

    # 3) The external track is untouched — and verbatim in the rewritten bytes.
    assert after_index[99]["location"] == EXTERNAL_LOCATION
    assert f'Location="{EXTERNAL_LOCATION}"' in rewritten_text
    # &-escaping survives on a track whose Location WAS rewritten (load-bearing).
    coll = ET.parse(xml).getroot().find("COLLECTION")
    assert coll.find('TRACK[@TrackID="1"]').get("Artist") == "A & B"

    # 4) Beatgrids + cues survive unchanged for every track (incl. the no-cue one).
    for tid in before_index:
        assert after_index[tid]["children"] == before_index[tid]["children"], (
            f"cues/beatgrid changed for track {tid}"
        )
    assert after_index[4]["children"] == [], "the no-cue track must stay cue-less"

    # 5) Playlists: TrackID keys unchanged; the location-keyed entry is rewritten.
    assert _playlist_track_keys(xml, "By ID") == before_tid_keys
    by_path_key = _playlist_track_keys(xml, "By Path")[0]
    assert location_to_path(by_path_key).absolute() == moves[moved[0]].absolute(), (
        "location-keyed playlist entry must follow the move, not die"
    )

    # 6) Undo restores the collection byte-for-byte.
    run_id = next(p.name for p in runs_dir.iterdir())
    undo_run(run_id, runs_dir)
    assert xml.read_bytes() == original_bytes
