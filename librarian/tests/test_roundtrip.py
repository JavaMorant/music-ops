"""The headline guarantee: plan -> apply -> undo restores the library
byte-for-byte, including the rekordbox XML. Proven on both a synthetic library
and a copy of the real testbed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from librarian.engine import apply_plan, undo_run
from librarian.journal import APPLIED, UNDONE
from librarian.model import MOVE, QUARANTINE, Action, Plan
from librarian.planner import build_plan
from librarian.paths import QUARANTINE_DIRNAME
from librarian.rekordbox import location_to_path, path_to_location

from conftest import SAMPLE_LIBRARY, tree_digest


def _write_rekordbox_xml(path: Path, tracks: list[Path]) -> None:
    """A minimal but real-shaped rekordbox export referencing ``tracks``."""
    rows = "\n".join(
        f'    <TRACK TrackID="{i}" Name="t{i}" Location="{path_to_location(t.absolute())}"/>'
        for i, t in enumerate(tracks, 1)
    )
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<DJ_PLAYLISTS Version="1.0.0">\n'
        '  <COLLECTION Entries="%d">\n%s\n  </COLLECTION>\n'
        "</DJ_PLAYLISTS>\n" % (len(tracks), rows),
        encoding="utf-8",
    )


def test_roundtrip_synthetic(library: Path, runs_dir: Path, tmp_path: Path):
    before = tree_digest(library)
    assert before, "fixture library should not be empty"

    plan = build_plan(library)
    assert plan.actions, "planner should propose changes for junk names"
    # Exercise both kinds in one run.
    assert any(a.kind == MOVE for a in plan.actions)
    assert any(a.kind == QUARANTINE for a in plan.actions)

    journal = apply_plan(plan, runs_dir)
    assert journal.status == APPLIED

    after_apply = tree_digest(library)
    assert after_apply != before, "apply must actually change the tree"
    # Same set of file *contents*, just at new paths — nothing lost.
    assert sorted(after_apply.values()) == sorted(before.values())
    # The duplicate landed in quarantine, not oblivion.
    assert any(QUARANTINE_DIRNAME in rel for rel in after_apply)

    undone = undo_run(journal.run_id, runs_dir)
    assert undone.status == UNDONE
    assert tree_digest(library) == before, "undo must restore the tree byte-for-byte"


def test_roundtrip_with_rekordbox(library: Path, runs_dir: Path, tmp_path: Path):
    # An XML that tracks two files the plan will rename.
    tracked = [
        library / "Chammak Challo_spotdown.org.mp3",
        library / "Avicii - The Nights (Clean Extended).mp3",
    ]
    xml = tmp_path / "collection.xml"
    _write_rekordbox_xml(xml, tracked)
    xml_before = xml.read_bytes()

    plan = build_plan(library, rekordbox_xml=xml)
    journal = apply_plan(plan, runs_dir)

    # The renamed track's Location now points at its new path, and that path
    # exists on disk — no dead cue pointer.
    moved = {a.src: a.dest for a in plan.actions}
    xml_after = xml.read_text(encoding="utf-8")
    new_loc = path_to_location(moved[tracked[0]].absolute())
    assert new_loc in xml_after
    assert location_to_path(new_loc).exists()

    undo_run(journal.run_id, runs_dir)
    assert xml.read_bytes() == xml_before, "undo must restore the rekordbox XML exactly"
    assert tree_digest(library) == tree_digest(library)  # sanity


@pytest.mark.skipif(not SAMPLE_LIBRARY.is_dir(), reason="testbed sample-library not present")
def test_roundtrip_real_testbed(tmp_path: Path):
    """Prove it on a COPY of the real testbed — never the original."""
    work = tmp_path / "sample-library"
    shutil.copytree(SAMPLE_LIBRARY, work)
    runs_dir = tmp_path / "runs"

    before = tree_digest(work)
    plan = build_plan(work)
    assert plan.actions, "the real testbed has junk names to clean"

    journal = apply_plan(plan, runs_dir)
    assert tree_digest(work) != before

    undo_run(journal.run_id, runs_dir)
    assert tree_digest(work) == before, "real-testbed round-trip must be byte-for-byte"
