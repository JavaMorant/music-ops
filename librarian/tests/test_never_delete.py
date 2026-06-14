"""The never-delete / never-clobber invariant."""

from __future__ import annotations

from pathlib import Path

import pytest

from librarian import engine
from librarian.engine import EngineError, apply_plan
from librarian.model import MOVE, Action, Plan
from librarian.planner import build_plan

from conftest import make_library, tree_digest


def test_file_count_never_drops(library: Path, runs_dir: Path):
    n_before = sum(1 for p in library.rglob("*") if p.is_file())
    plan = build_plan(library)
    apply_plan(plan, runs_dir)
    n_after = sum(1 for p in library.rglob("*") if p.is_file())
    assert n_after == n_before, "no file may disappear — quarantine, never delete"


def test_clobber_is_refused_and_nothing_moves(tmp_path: Path, runs_dir: Path):
    root = tmp_path / "lib"
    make_library(root, ["a.mp3", "b.mp3"])
    before = tree_digest(root)

    # A plan that would overwrite b.mp3 with a.mp3 — must be refused outright.
    bad = Plan(
        library_root=root,
        actions=[Action(MOVE, root / "a.mp3", root / "b.mp3", reason="would clobber")],
    )
    with pytest.raises(EngineError, match="overwrite"):
        apply_plan(bad, runs_dir)

    assert tree_digest(root) == before, "a refused apply must leave the tree untouched"


def test_engine_source_has_no_delete_or_copydelete_calls():
    """Belt-and-suspenders: the engine never calls a delete primitive, and never
    uses shutil.move (which silently becomes copy+delete across volumes)."""
    src = Path(engine.__file__).read_text(encoding="utf-8")
    for forbidden in ("os.remove", "os.unlink", "shutil.rmtree", ".unlink(", "os.rmdir", "shutil.move"):
        assert forbidden not in src, f"engine must not use {forbidden}"


def test_cross_volume_move_is_refused(tmp_path: Path, runs_dir: Path, monkeypatch):
    """A move that would cross volumes (non-atomic copy+delete) is refused, and
    nothing changes."""
    root = tmp_path / "lib"
    make_library(root, ["track_spotdown.org.mp3"])
    before = tree_digest(root)

    monkeypatch.setattr(engine, "_same_device", lambda a, b: False)
    plan = build_plan(root)
    assert plan.actions
    with pytest.raises(EngineError, match="cross-volume"):
        apply_plan(plan, runs_dir)
    assert tree_digest(root) == before


def test_dest_is_directory_is_refused(tmp_path: Path, runs_dir: Path):
    root = tmp_path / "lib"
    make_library(root, ["a.mp3"])
    (root / "occupied").mkdir()
    before = tree_digest(root)

    bad = Plan(
        library_root=root,
        actions=[Action(MOVE, root / "a.mp3", root / "occupied", reason="onto a dir")],
    )
    with pytest.raises(EngineError, match="directory"):
        apply_plan(bad, runs_dir)
    assert tree_digest(root) == before


def test_dest_outside_library_root_is_refused(tmp_path: Path, runs_dir: Path):
    root = tmp_path / "lib"
    make_library(root, ["a.mp3"])
    outside = tmp_path / "elsewhere" / "a.mp3"
    bad = Plan(
        library_root=root,
        actions=[Action(MOVE, root / "a.mp3", outside, reason="escapes root")],
    )
    with pytest.raises(EngineError, match="escapes the library root"):
        apply_plan(bad, runs_dir)
    assert (root / "a.mp3").exists() and not outside.exists()


def test_case_only_destination_collision_is_refused(tmp_path: Path, runs_dir: Path):
    """Two actions whose dests differ only by case must be caught up front, not
    mid-apply, on a case-insensitive volume."""
    root = tmp_path / "lib"
    make_library(root, ["one.mp3", "two.mp3"])
    before = tree_digest(root)
    bad = Plan(
        library_root=root,
        actions=[
            Action(MOVE, root / "one.mp3", root / "Song.mp3", reason="x"),
            Action(MOVE, root / "two.mp3", root / "song.mp3", reason="case-collides"),
        ],
    )
    with pytest.raises(EngineError, match="same destination"):
        apply_plan(bad, runs_dir)
    assert tree_digest(root) == before
