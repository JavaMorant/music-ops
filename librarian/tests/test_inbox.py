"""The inbox planner: dedupe a drop against the library and within itself, file
new tracks, and queue rekordbox additions — all as a reviewable plan."""

from __future__ import annotations

from pathlib import Path

import pytest

from librarian import inbox as inbox_mod
from librarian.inbox import InboxError, build_inbox_plan
from librarian.metadata import TrackMeta
from librarian.model import MOVE, QUARANTINE


def _fake_meta(specs: dict[str, dict]):
    def reader(path: Path) -> TrackMeta:
        return TrackMeta(path=path, **specs.get(path.name, {}))
    return reader


def _lib(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "lib"
    inbox = root / "Inbox"
    inbox.mkdir(parents=True)
    return root, inbox


def test_new_track_emits_move_and_addition(tmp_path: Path, monkeypatch):
    root, inbox = _lib(tmp_path)
    (inbox / "raw.mp3").write_bytes(b"fresh-track")
    monkeypatch.setattr(
        inbox_mod, "read_meta",
        _fake_meta({"raw.mp3": dict(artist="Burna Boy", title="Ta Ta Ta", genre="Afrobeats", key="8A", bitrate_kbps=320)}),
    )
    plan, report = build_inbox_plan(root, inbox, organize_by_genre=True)
    moves = [a for a in plan.actions if a.kind == MOVE]
    assert len(moves) == 1
    assert moves[0].dest == root / "Afrobeats" / "Burna Boy - Ta Ta Ta.mp3"
    assert report.imported == 1
    assert plan.rekordbox_additions and len(plan.rekordbox_additions) == 1
    add = plan.rekordbox_additions[0]
    assert add.location == moves[0].dest
    assert add.tonality == "8A" and add.artist == "Burna Boy" and add.kind == "MP3 File"


def test_dedup_against_library_quarantines_candidate(tmp_path: Path, monkeypatch):
    root, inbox = _lib(tmp_path)
    (root / "Existing.mp3").write_bytes(b"identical-bytes")          # already in library
    (inbox / "incoming.mp3").write_bytes(b"identical-bytes")         # same bytes
    monkeypatch.setattr(inbox_mod, "read_meta", _fake_meta({}))
    plan, report = build_inbox_plan(root, inbox)
    q = [a for a in plan.actions if a.kind == QUARANTINE]
    assert len(q) == 1 and q[0].src == inbox / "incoming.mp3"
    assert "already in library" in q[0].reason
    # cues redirect to the kept library copy; no collection addition for a dup.
    assert plan.location_redirects[inbox / "incoming.mp3"] == root / "Existing.mp3"
    assert not plan.rekordbox_additions
    assert len(report.dup_library) == 1


def test_within_batch_dedup_keeps_higher_quality(tmp_path: Path, monkeypatch):
    root, inbox = _lib(tmp_path)
    (inbox / "low.mp3").write_bytes(b"same-audio")
    (inbox / "high.mp3").write_bytes(b"same-audio")  # identical bytes -> exact dup
    monkeypatch.setattr(
        inbox_mod, "read_meta",
        _fake_meta({"low.mp3": dict(bitrate_kbps=128), "high.mp3": dict(bitrate_kbps=320)}),
    )
    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=False)
    moved = [a.src.name for a in plan.actions if a.kind == MOVE]
    quarantined = [a.src.name for a in plan.actions if a.kind == QUARANTINE]
    assert moved == ["high.mp3"]
    assert quarantined == ["low.mp3"]


def test_inbox_outside_library_root_rejected(tmp_path: Path):
    root = tmp_path / "lib"
    root.mkdir()
    outside = tmp_path / "Downloads"
    outside.mkdir()
    with pytest.raises(InboxError, match="inside the library root"):
        build_inbox_plan(root, outside)


def test_inbox_dir_excluded_from_library_index(tmp_path: Path, monkeypatch):
    """A file sitting in the inbox must not be treated as an existing library
    copy of itself — otherwise the only candidate dedupes against itself."""
    root, inbox = _lib(tmp_path)
    (inbox / "only.mp3").write_bytes(b"unique")
    monkeypatch.setattr(inbox_mod, "read_meta", _fake_meta({"only.mp3": dict(artist="A", title="B")}))
    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=False)
    assert [a.kind for a in plan.actions] == [MOVE]


def test_genreless_new_track_files_to_library_root(tmp_path: Path, monkeypatch):
    root, inbox = _lib(tmp_path)
    (inbox / "x.mp3").write_bytes(b"z")
    monkeypatch.setattr(inbox_mod, "read_meta", _fake_meta({"x.mp3": dict(artist="A", title="B")}))
    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=True)
    move = next(a for a in plan.actions if a.kind == MOVE)
    assert move.dest == root / "A - B.mp3"  # at root, never an "Unknown" pile


def test_within_drop_dup_redirects_to_imported_keeper(tmp_path: Path, monkeypatch):
    """A within-drop duplicate's rekordbox cues must land on the IMPORTED keeper,
    not on its own quarantine dest."""
    root, inbox = _lib(tmp_path)
    (inbox / "a.mp3").write_bytes(b"same")
    (inbox / "b.mp3").write_bytes(b"same")  # identical -> b is a within-drop dup
    monkeypatch.setattr(inbox_mod, "read_meta", _fake_meta({
        "a.mp3": dict(artist="A", title="T", genre="House", bitrate_kbps=320),
        "b.mp3": dict(artist="A", title="T", genre="House", bitrate_kbps=320),
    }))
    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=True)
    move = next(a for a in plan.actions if a.kind == MOVE)
    dup = next(a for a in plan.actions if a.kind == QUARANTINE)
    # The dup redirects to the keeper's final library path, not its quarantine dest.
    assert plan.location_redirects[dup.src] == move.dest


def test_copy_marker_does_not_steal_keeper(tmp_path: Path, monkeypatch):
    """With a clean name and a copy-marker name for identical bytes, the CLEAN
    one is imported and the marker is quarantined — the track is never lost."""
    root, inbox = _lib(tmp_path)
    (inbox / "Track.mp3").write_bytes(b"identical")
    (inbox / "Track (1).mp3").write_bytes(b"identical")
    monkeypatch.setattr(inbox_mod, "read_meta", _fake_meta({}))
    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=False)
    moved = [a.src.name for a in plan.actions if a.kind == MOVE]
    quarantined = [a.src.name for a in plan.actions if a.kind == QUARANTINE]
    assert moved == ["Track.mp3"], "the clean-named copy must survive as the import"
    assert quarantined == ["Track (1).mp3"]
    assert plan.rekordbox_additions and len(plan.rekordbox_additions) == 1


def test_suspected_duplicate_copy_marker_quarantined(tmp_path: Path, monkeypatch):
    """A new-bytes file whose name has a copy marker is quarantined as suspected,
    not imported."""
    root, inbox = _lib(tmp_path)
    (inbox / "Some Track (1).mp3").write_bytes(b"genuinely-new-bytes")
    monkeypatch.setattr(inbox_mod, "read_meta", _fake_meta({}))
    plan, report = build_inbox_plan(root, inbox, organize_by_genre=False)
    assert [a.kind for a in plan.actions] == [QUARANTINE]
    assert "suspected duplicate" in plan.actions[0].reason
    assert len(report.suspected) == 1
    assert not plan.rekordbox_additions


def test_two_new_tracks_same_tags_get_distinct_dests(tmp_path: Path, monkeypatch):
    """Two genuinely different files tagged identically (clean vs dirty) both
    import, disambiguated, with two distinct rekordbox additions."""
    root, inbox = _lib(tmp_path)
    (inbox / "x.mp3").write_bytes(b"version-one")
    (inbox / "y.mp3").write_bytes(b"version-two")  # different bytes, same tags
    monkeypatch.setattr(inbox_mod, "read_meta", _fake_meta({
        "x.mp3": dict(artist="A", title="T", genre="House"),
        "y.mp3": dict(artist="A", title="T", genre="House"),
    }))
    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=True)
    dests = sorted(a.dest.name for a in plan.actions if a.kind == MOVE)
    assert dests == ["A - T.mp3", "A - T (2).mp3"] or dests == ["A - T (2).mp3", "A - T.mp3"]
    assert len(plan.rekordbox_additions) == 2
    assert len({a.location for a in plan.rekordbox_additions}) == 2


def test_two_new_tracks_nfc_nfd_twin_names_apply_cleanly(tmp_path: Path, monkeypatch):
    """Two distinct new files whose titles differ only by Unicode form (NFC vs
    NFD) must produce a plan the engine ACCEPTS — the planner's reserved key and
    the engine's collision key must agree, so the second dest is disambiguated."""
    import unicodedata

    from librarian.engine import apply_plan, undo_run

    root, inbox = _lib(tmp_path)
    (inbox / "a.mp3").write_bytes(b"version-one")
    (inbox / "b.mp3").write_bytes(b"version-two")  # different bytes
    title_nfc = unicodedata.normalize("NFC", "Aseréjé")
    title_nfd = unicodedata.normalize("NFD", "Aseréjé")
    assert title_nfc != title_nfd
    monkeypatch.setattr(inbox_mod, "read_meta", _fake_meta({
        "a.mp3": dict(artist="Las Ketchup", title=title_nfc, genre="Pop"),
        "b.mp3": dict(artist="Las Ketchup", title=title_nfd, genre="Pop"),
    }))
    runs_dir = tmp_path / "runs"

    plan, _ = build_inbox_plan(root, inbox, organize_by_genre=True)
    dests = [a.dest for a in plan.actions if a.kind == MOVE]
    assert len(dests) == 2
    # The two dests must be distinct under the engine's NFC+casefold key.
    from librarian.engine import _norm
    assert _norm(dests[0]) != _norm(dests[1]), "twins must be disambiguated, not collide"

    # And the engine must actually accept + round-trip the plan (no preflight reject).
    journal = apply_plan(plan, runs_dir)
    assert sum(1 for a in journal.actions if a.status == "done") == 2
    undo_run(journal.run_id, runs_dir)


def test_report_flags_low_bitrate_and_missing_key(tmp_path: Path, monkeypatch):
    """Low-bitrate / missing-key / missing-tags are surfaced for review and the
    report renders them (never guess key, flag for re-acquire)."""
    root, inbox = _lib(tmp_path)
    (inbox / "lo.mp3").write_bytes(b"low-quality")
    monkeypatch.setattr(inbox_mod, "read_meta", _fake_meta({
        "lo.mp3": dict(artist="A", title="T", genre="House", bitrate_kbps=192),  # lossy, <320, no key
    }))
    plan, report = build_inbox_plan(root, inbox)
    assert any(p.name == "lo.mp3" for p, _ in report.low_bitrate)
    assert any(p.name == "lo.mp3" for p in report.missing_key)
    text = report.render()
    assert "Low bitrate" in text and "192 kbps" in text
    assert "Inbox files scanned: 1" in text


def test_symlinked_inbox_outside_root_rejected(tmp_path: Path):
    """An inbox that is a symlink resolving outside the library is refused."""
    root = tmp_path / "lib"
    root.mkdir()
    external = tmp_path / "external_drop"
    external.mkdir()
    link = root / "Inbox"
    link.symlink_to(external, target_is_directory=True)
    with pytest.raises(InboxError, match="real folder inside"):
        build_inbox_plan(root, link)


def test_empty_inbox_is_noop(tmp_path: Path):
    root, inbox = _lib(tmp_path)
    plan, report = build_inbox_plan(root, inbox)
    assert plan.actions == [] and report.total_files == 0
    assert not plan.rekordbox_additions
