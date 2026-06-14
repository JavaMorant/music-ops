"""The pre-apply backup safeguard: at-risk files are copied, byte-for-byte,
before anything moves — and the run still round-trips."""

from __future__ import annotations

import hashlib
from pathlib import Path

from librarian.engine import apply_plan, undo_run
from librarian.planner import build_plan

from conftest import tree_digest


def _backup_files(run_dir: Path) -> list[Path]:
    backup = run_dir / "backup"
    return [p for p in backup.rglob("*") if p.is_file()] if backup.exists() else []


def test_backup_copies_touched_sources(library: Path, runs_dir: Path):
    # Hash each source before the run so we can prove the backup matches.
    plan = build_plan(library)
    src_hashes = {a.src.name: hashlib.sha256(a.src.read_bytes()).hexdigest() for a in plan.actions}

    journal = apply_plan(plan, runs_dir, backup=True)
    assert journal.backup is not None
    assert journal.backup["mode"] == "sources"
    assert journal.backup["files"] == len(plan.actions)

    backed = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in _backup_files(journal.run_dir)}
    assert backed == src_hashes, "backup must be a byte-for-byte copy of every touched source"


def test_full_backup_snapshots_whole_library(library: Path, runs_dir: Path):
    n_files = sum(1 for p in library.rglob("*") if p.is_file())
    journal = apply_plan(build_plan(library), runs_dir, backup=True, full_backup=True)
    assert journal.backup["mode"] == "full"
    assert journal.backup["files"] == n_files


def test_no_backup_flag_skips_backup(library: Path, runs_dir: Path):
    journal = apply_plan(build_plan(library), runs_dir, backup=False)
    assert journal.backup is None
    assert _backup_files(journal.run_dir) == []


def test_backup_does_not_break_roundtrip(library: Path, runs_dir: Path):
    before = tree_digest(library)
    journal = apply_plan(build_plan(library), runs_dir, backup=True)
    undo_run(journal.run_id, runs_dir)
    # The backup lives under runs_dir (outside the library), so the library tree
    # round-trips byte-for-byte and isn't polluted by backup copies.
    assert tree_digest(library) == before
