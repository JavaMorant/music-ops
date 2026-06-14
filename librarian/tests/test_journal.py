"""The undo journal: written before execution, durable across a crash, and the
basis for reversing a partially-applied run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from librarian import engine
from librarian.engine import apply_plan, undo_run
from librarian.journal import DONE, JOURNAL_NAME, PENDING, UNDONE, list_runs
from librarian.planner import build_plan

from conftest import tree_digest


def test_journal_exists_before_first_move(library: Path, runs_dir: Path, monkeypatch):
    """At the instant the first file moves, the full plan is already on disk."""
    seen = {}
    real_move = engine._safe_move
    calls = {"n": 0}

    def once(src, dest):
        if calls["n"] == 0:
            runs = list_runs(runs_dir)
            seen["present"] = bool(runs) and (runs[0].run_dir / JOURNAL_NAME).exists()
        calls["n"] += 1
        return real_move(src, dest)

    monkeypatch.setattr(engine, "_safe_move", once)
    apply_plan(build_plan(library), runs_dir)
    assert seen.get("present") is True, "journal must be written before the first move"


def test_crash_midway_is_recoverable(library: Path, runs_dir: Path, monkeypatch):
    """If apply dies mid-run, the journal reflects exactly what completed and
    undo reverses precisely those moves."""
    before = tree_digest(library)
    plan = build_plan(library)
    assert len(plan.actions) >= 2, "need >=2 actions to crash between them"

    real_move = engine._safe_move
    calls = {"n": 0}

    def crash_on_second(src, dest):
        if calls["n"] == 1:
            raise RuntimeError("simulated crash mid-apply")
        calls["n"] += 1
        return real_move(src, dest)

    monkeypatch.setattr(engine, "_safe_move", crash_on_second)
    with pytest.raises(RuntimeError, match="simulated crash"):
        apply_plan(plan, runs_dir)

    # The journal survived and records a partial run: first done, rest pending.
    runs = list_runs(runs_dir)
    assert len(runs) == 1
    journal = runs[0]
    statuses = [a.status for a in journal.actions]
    assert statuses[0] == DONE
    assert PENDING in statuses, "the un-executed actions stay pending"

    # Undo the partial run -> back to the original tree.
    monkeypatch.setattr(engine, "_safe_move", real_move)
    undone = undo_run(journal.run_id, runs_dir)
    assert undone.status == UNDONE
    assert tree_digest(library) == before, "recovery from a partial run is byte-for-byte"


def test_double_undo_is_refused(library: Path, runs_dir: Path):
    journal = apply_plan(build_plan(library), runs_dir)
    undo_run(journal.run_id, runs_dir)
    with pytest.raises(engine.EngineError, match="already been undone"):
        undo_run(journal.run_id, runs_dir)
