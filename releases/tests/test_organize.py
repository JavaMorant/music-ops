"""Tests for the on-disk folder engine: planning, the safety invariants
(never-overwrite, never-delete, containment, run-id guard), and the
apply→undo round-trip + index re-linking. Everything runs on throwaway temp
trees — never the real library."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from releases import db as dbmod
from releases import organize as org
from releases.scan import scan


def _mk(root: Path, rel: str, files: dict[str, str]) -> Path:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (d / name).write_text(content)
    return d


def _digest(root: Path) -> dict[str, str]:
    out = {}
    for dp, _d, fs in os.walk(root):
        for f in fs:
            p = Path(dp) / f
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def lib(tmp_path):
    """A tiny projects tree with two filed-wrong sketches + a db indexing them."""
    root = tmp_path / "projects"
    _mk(root, "Beats/Tracks/Mels/sketchy", {"sketchy.flp": "a", "sketchy.mp3": "b"})
    _mk(root, "Beats/Tracks/Need Arranged : Deep Progress/halfdone", {"halfdone.flp": "c"})
    (root / "Beats" / "Tracks" / "Complete Tracks").mkdir(parents=True)
    db = dbmod.connect(tmp_path / "index.db")
    dbmod.upsert_projects(db, scan(root))
    return root, db


def _proj_path(db, name):
    return dbmod.find_projects(db, name)[0].path


class TestPlanner:
    def test_by_stage_proposes_mismatches(self, lib):
        root, db = lib
        dbmod.set_stage(db, dbmod.find_projects(db, "sketchy")[0], "complete", None)
        actions, _ = org.plan_by_stage(root, dbmod.all_projects(db))
        moved = {Path(a.src).name: a.dest for a in actions}
        assert "sketchy" in moved
        assert moved["sketchy"] == root / "Beats/Tracks/Complete Tracks/sketchy"

    def test_already_filed_is_skipped(self, lib):
        root, db = lib
        # 'halfdone' is already in Need Arranged, its inferred stage → no action
        actions, _ = org.plan_by_stage(root, dbmod.all_projects(db))
        assert all(Path(a.src).name != "halfdone" for a in actions)

    def test_file_collision_is_skipped_not_overwritten(self, lib):
        root, db = lib
        # pre-create the destination so filing would collide
        (root / "Beats/Tracks/Complete Tracks/sketchy").mkdir(parents=True)
        action, note = org.plan_file(root, Path(_proj_path(db, "sketchy")), "complete", "x")
        assert action is None and "already exists" in note

    def test_rename_keeps_extension_for_loose_file(self, tmp_path):
        root = tmp_path / "projects"
        _mk(root, "Beats/Tracks", {})
        f = root / "Beats/Tracks/old.flp"
        f.write_text("x")
        action, _ = org.plan_rename(root, f, "new")
        assert action.dest.name == "new.flp"

    def test_rename_rejects_path_separators(self, lib):
        root, db = lib
        action, note = org.plan_rename(root, Path(_proj_path(db, "sketchy")), "a/b")
        assert action is None and "invalid" in note


class TestSafety:
    def test_preflight_refuses_existing_dest(self, lib):
        root, db = lib
        src = Path(_proj_path(db, "sketchy"))
        dest = root / "Beats/Tracks/Complete Tracks/halfdone"  # exists after we make it
        dest.mkdir(parents=True)
        plan = org.Plan(root, [org.Action(org.MOVE, src, dest, "x")])
        with pytest.raises(org.OrganizeError, match="already exists"):
            org.preflight(plan)

    def test_preflight_refuses_escape(self, lib, tmp_path):
        root, db = lib
        src = Path(_proj_path(db, "sketchy"))
        outside = tmp_path / "elsewhere" / "sketchy"
        plan = org.Plan(root, [org.Action(org.MOVE, src, outside, "x")])
        with pytest.raises(org.OrganizeError, match="escapes"):
            org.preflight(plan)

    def test_preflight_refuses_duplicate_dest(self, lib):
        root, db = lib
        a = Path(_proj_path(db, "sketchy"))
        b = Path(_proj_path(db, "halfdone"))
        dest = root / "Beats/Tracks/Complete Tracks/clash"
        plan = org.Plan(root, [org.Action(org.MOVE, a, dest, "x"),
                               org.Action(org.MOVE, b, dest, "y")])
        with pytest.raises(org.OrganizeError, match="same destination"):
            org.preflight(plan)

    def test_undo_rejects_traversal_run_id(self, tmp_path):
        with pytest.raises(org.OrganizeError, match="invalid run id"):
            org.undo_run("../evil", tmp_path / "runs")

    def test_undo_rejects_unknown_run(self, tmp_path):
        (tmp_path / "runs").mkdir()
        with pytest.raises(org.OrganizeError, match="no run found"):
            org.undo_run("20200101-000000-abcdef", tmp_path / "runs")


class TestApplyUndo:
    def _plan(self, root, db):
        dbmod.set_stage(db, dbmod.find_projects(db, "sketchy")[0], "complete", None)
        dbmod.set_stage(db, dbmod.find_projects(db, "halfdone")[0], "remix", None)
        actions, _ = org.plan_by_stage(root, dbmod.all_projects(db))
        return org.Plan(root, actions)

    def test_roundtrip_is_byte_identical(self, lib, tmp_path):
        root, db = lib
        before = _digest(root)
        plan = self._plan(root, db)
        runs = tmp_path / "runs"
        j = org.apply_plan(plan, runs)
        assert _digest(root) != before  # moved
        org.undo_run(j.run_id, runs)
        assert _digest(root) == before  # fully reversed

    def test_apply_is_journaled_and_listed(self, lib, tmp_path):
        root, db = lib
        plan = self._plan(root, db)
        runs = tmp_path / "runs"
        j = org.apply_plan(plan, runs)
        assert j.status == org.APPLIED
        assert (runs / j.run_id / "journal.json").exists()
        assert org.list_runs(runs)[0].run_id == j.run_id

    def test_double_undo_refused(self, lib, tmp_path):
        root, db = lib
        runs = tmp_path / "runs"
        j = org.apply_plan(self._plan(root, db), runs)
        org.undo_run(j.run_id, runs)
        with pytest.raises(org.OrganizeError, match="already been undone"):
            org.undo_run(j.run_id, runs)


class TestSymlinkSafety:
    def test_dangling_symlink_dest_is_refused(self, lib):
        root, db = lib
        src = Path(_proj_path(db, "sketchy"))
        dest = root / "Beats/Tracks/Complete Tracks/sketchy"
        os.symlink(root / "nonexistent-target", dest)  # dangling symlink occupies dest
        plan = org.Plan(root, [org.Action(org.MOVE, src, dest, "x")])
        with pytest.raises(org.OrganizeError, match="already exists"):
            org.preflight(plan)
        assert os.path.islink(dest)  # the symlink was NOT destroyed

    def test_hardlink_dest_is_refused_not_temp_danced(self, tmp_path):
        root = tmp_path / "projects"
        _mk(root, "Beats/Tracks", {})
        src = root / "Beats/Tracks/a.flp"
        src.write_text("x")
        dest = root / "Beats/Tracks/b.flp"
        os.link(src, dest)  # hardlink: samefile(src, dest) is True, but it's NOT a case-rename
        plan = org.Plan(root, [org.Action(org.MOVE, src, dest, "x")])
        with pytest.raises(org.OrganizeError, match="already exists"):
            org.preflight(plan)
        # and the engine itself refuses, leaving no stray temp file
        with pytest.raises(org.OrganizeError):
            org._safe_move(src, dest)
        assert not (dest.parent / "b.flp.releases-rename-tmp").exists()

    def test_symlinked_dir_cannot_redirect_a_move_outside_root(self, tmp_path):
        root = tmp_path / "projects"
        outside = tmp_path / "outside"
        _mk(root, "Beats/Tracks/Mels/proj", {"proj.flp": "x"})
        outside.mkdir()
        os.symlink(outside, root / "escape")  # a symlink inside root → outside
        src = root / "Beats/Tracks/Mels/proj"
        dest = root / "escape" / "proj"  # lexically within root, really outside
        plan = org.Plan(root, [org.Action(org.MOVE, src, dest, "x")])
        with pytest.raises(org.OrganizeError, match="escapes"):
            org.preflight(plan)


class TestOverlap:
    def test_dest_nested_in_another_source_refused(self, lib):
        root, db = lib
        a = Path(_proj_path(db, "sketchy"))
        b = Path(_proj_path(db, "halfdone"))
        # action1 moves INTO a subpath of action2's source → order-dependent, refuse
        plan = org.Plan(root, [
            org.Action(org.MOVE, a, b / "inner", "x"),
            org.Action(org.MOVE, b, root / "Beats/Tracks/Complete Tracks/halfdone", "y"),
        ])
        with pytest.raises(org.OrganizeError, match="overlap"):
            org.preflight(plan)


class TestPartialApply:
    def test_interrupted_apply_surfaces_partial_journal(self, lib, tmp_path, monkeypatch):
        root, db = lib
        dbmod.set_stage(db, dbmod.find_projects(db, "sketchy")[0], "complete", None)
        dbmod.set_stage(db, dbmod.find_projects(db, "halfdone")[0], "remix", None)
        actions, _ = org.plan_by_stage(root, dbmod.all_projects(db))
        assert len(actions) == 2
        plan = org.Plan(root, actions)

        real_move = org._safe_move
        calls = {"n": 0}

        def flaky(src, dest):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated disk failure")
            return real_move(src, dest)

        monkeypatch.setattr(org, "_safe_move", flaky)
        with pytest.raises(org.ApplyInterrupted) as ei:
            org.apply_plan(plan, tmp_path / "runs")
        j = ei.value.journal
        assert sum(1 for a in j.actions if a.status == org.DONE) == 1   # one move completed
        # the journal on disk reflects the partial run and is undoable
        on_disk = org.load_journal((tmp_path / "runs") / j.run_id)
        assert sum(1 for a in on_disk.actions if a.status == org.DONE) == 1
        org.undo_run(j.run_id, tmp_path / "runs")  # the completed move reverses cleanly


class TestRepath:
    def test_repath_updates_index_and_release_links(self, lib):
        root, db = lib
        src = _proj_path(db, "sketchy")
        rid = dbmod.create_release(db, "EP", "ep")
        dbmod.add_tracks(db, rid, [(src, "sketchy")])
        new = str(root / "Beats/Tracks/Complete Tracks/sketchy")
        moved = dbmod.repath(db, src, new)
        assert moved == 1
        assert dbmod.find_projects(db, "sketchy")[0].path == new
        assert new in dbmod.ordered_paths(db, rid)  # release membership followed

    def test_repath_is_collision_proof(self, lib):
        root, db = lib
        old = _proj_path(db, "sketchy")
        new = _proj_path(db, "halfdone")  # pretend a STALE row already sits at the dest
        # repath old -> new must clear the stale row, not raise a PK collision
        moved = dbmod.repath(db, old, new)
        assert moved == 1
        rows = db.execute("SELECT path FROM projects WHERE path = ?", (new,)).fetchall()
        assert len(rows) == 1  # exactly one row at the destination path

    def test_repath_reports_unknown_old_path(self, lib):
        root, db = lib
        assert dbmod.repath(db, "/not/in/index", "/somewhere/else") == 0
