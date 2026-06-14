"""The cleanup planner: exact dedupe, rename to Artist - Title, refile by genre,
and a faithful report — all driving the same reversible engine.

Track metadata is injected (mutagen needs real encoded audio for tags/bitrate),
so these tests exercise the planner's decisions on controlled inputs while the
engine still moves real files on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from librarian import cleanup
from librarian.cleanup import build_cleanup_plan
from librarian.engine import apply_plan, undo_run
from librarian.metadata import TrackMeta
from librarian.model import MOVE, QUARANTINE
from librarian.paths import QUARANTINE_DIRNAME

from conftest import tree_digest


def _fake_meta(specs: dict[str, dict]):
    """Return a read_meta stand-in driven by a {filename: fields} table."""

    def reader(path: Path) -> TrackMeta:
        fields = specs.get(path.name, {})
        return TrackMeta(path=path, **fields)

    return reader


def test_rename_and_refile_from_tags(tmp_path: Path, runs_dir: Path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    (root / "raw1.mp3").write_bytes(b"track-one")
    (root / "raw2.mp3").write_bytes(b"track-two")
    specs = {
        "raw1.mp3": dict(artist="Burna Boy", title="Ta Ta Ta", genre="Afrobeats", key="8A", bitrate_kbps=320),
        "raw2.mp3": dict(artist="Avicii", title="The Nights", genre="Dance", bitrate_kbps=192),
    }
    monkeypatch.setattr(cleanup, "read_meta", _fake_meta(specs))

    plan, report = build_cleanup_plan(root, organize_by_genre=True)
    dests = {a.src.name: a.dest for a in plan.actions}
    assert dests["raw1.mp3"] == root / "Afrobeats" / "Burna Boy - Ta Ta Ta.mp3"
    assert dests["raw2.mp3"] == root / "Dance" / "Avicii - The Nights.mp3"
    assert report.renamed == 2 and report.refiled == 2
    # raw2 is 192kbps lossy -> flagged; raw2 has no key -> flagged.
    assert any(p.name == "raw2.mp3" for p, _ in report.low_bitrate)
    assert any(p.name == "raw2.mp3" for p in report.missing_key)

    before = tree_digest(root)
    journal = apply_plan(plan, runs_dir)
    assert (root / "Afrobeats" / "Burna Boy - Ta Ta Ta.mp3").exists()
    undo_run(journal.run_id, runs_dir)
    # Files are restored byte-for-byte. (An emptied genre dir may linger — the
    # engine never removes directories, by the never-delete design.)
    assert (root / "raw1.mp3").exists()
    assert not (root / "Afrobeats" / "Burna Boy - Ta Ta Ta.mp3").exists()
    assert tree_digest(root) == before


def test_exact_duplicate_quarantined_one_survivor(tmp_path: Path, runs_dir: Path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    # Byte-identical files -> exact duplicates.
    for name in ("keep.mp3", "copy.mp3"):
        (root / name).write_bytes(b"identical-audio-bytes")
    monkeypatch.setattr(cleanup, "read_meta", _fake_meta({}))

    plan, report = build_cleanup_plan(root, organize_by_genre=False)
    quarantines = [a for a in plan.actions if a.kind == QUARANTINE]
    assert len(quarantines) == 1, "exactly one of the two identical files is quarantined"
    assert len(report.duplicates) == 1
    # The quarantine dest is inside the quarantine dir, never deleted.
    assert QUARANTINE_DIRNAME in quarantines[0].dest.parts

    before = tree_digest(root)
    journal = apply_plan(plan, runs_dir)
    after = tree_digest(root)
    # No bytes lost: same multiset of contents, just one copy relocated.
    assert sorted(after.values()) == sorted(before.values())
    assert any(QUARANTINE_DIRNAME in rel for rel in after)
    undo_run(journal.run_id, runs_dir)
    assert tree_digest(root) == before


def test_quality_rank_prefers_lossless_then_bitrate(tmp_path: Path):
    from librarian.metadata import quality_rank

    lossy = TrackMeta(path=tmp_path / "a.mp3", bitrate_kbps=320)
    lossless = TrackMeta(path=tmp_path / "b.wav", lossless=True, bitrate_kbps=0)
    lo = TrackMeta(path=tmp_path / "c.mp3", bitrate_kbps=128)
    assert quality_rank(lossless) > quality_rank(lossy) > quality_rank(lo)


def test_cleanup_copy_marker_does_not_steal_keeper(tmp_path: Path, runs_dir: Path, monkeypatch):
    """For identical bytes named `Track.mp3` and `Track (1).mp3`, cleanup must
    keep the CLEAN-named copy in the library and quarantine the marker — never
    send the whole group to quarantine (which would lose the track)."""
    root = tmp_path / "lib"
    root.mkdir()
    (root / "Track.mp3").write_bytes(b"identical-bytes")
    (root / "Track (1).mp3").write_bytes(b"identical-bytes")
    monkeypatch.setattr(cleanup, "read_meta", _fake_meta({}))

    plan, _ = build_cleanup_plan(root, organize_by_genre=False)
    quarantined = [a.src.name for a in plan.actions if a.kind == QUARANTINE]
    assert quarantined == ["Track (1).mp3"], "only the marker copy is set aside"
    apply_plan(plan, runs_dir)
    assert (root / "Track.mp3").exists(), "the clean copy stays in the library"


def test_no_genre_left_in_place_not_dumped_in_unknown(tmp_path: Path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    (root / "Artist - Title.mp3").write_bytes(b"x")
    # Has artist/title (already correctly named) but no genre.
    monkeypatch.setattr(
        cleanup, "read_meta", _fake_meta({"Artist - Title.mp3": dict(artist="Artist", title="Title")})
    )
    plan, report = build_cleanup_plan(root, organize_by_genre=True)
    assert plan.actions == [], "an already-clean, genre-less file is left untouched"
    assert report.left_in_place == 1
