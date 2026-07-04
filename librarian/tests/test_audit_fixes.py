"""Regression tests for the 2026-07 post-audit fixes:

  * C2  — cue-safety guard refuses moves without a rekordbox XML (opt-out only)
  * C8  — a mid-apply failure raises PartialApplyError (not "nothing changed")
           and leaves an undoable run
  * C15 — undo reconciles a move that completed but was journaled PENDING
           (the crash window between os.rename and the journal flush)
  * C1/C6 — default_runs_dir lives outside the library, one place, env-overridable
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from librarian.engine import PartialApplyError, apply_plan, undo_run
from librarian.journal import PENDING, load_journal, write_journal
from librarian.model import MOVE, Action, Plan
from librarian.paths import default_runs_dir

from conftest import make_library


# --- C8: partial-apply reporting -------------------------------------------------

def test_partial_apply_raises_partial_error_and_is_undoable(tmp_path: Path):
    root = tmp_path / "lib"
    make_library(root, ["a.mp3"])
    runs = tmp_path / "runs"
    # Two actions from the SAME source pass preflight (which checks dests, not
    # srcs) but the second fails after the first has moved — a PARTIAL apply.
    bad = Plan(
        library_root=root,
        actions=[
            Action(MOVE, root / "a.mp3", root / "x.mp3", reason="first"),
            Action(MOVE, root / "a.mp3", root / "y.mp3", reason="src now gone"),
        ],
    )
    with pytest.raises(PartialApplyError) as ei:
        apply_plan(bad, runs, backup=False)

    err = ei.value
    assert err.run_id
    # the first move really happened — this is NOT "nothing changed"
    assert (root / "x.mp3").exists() and not (root / "a.mp3").exists()
    # the partial run is journaled and fully undoable
    undo_run(err.run_id, runs)
    assert (root / "a.mp3").exists() and not (root / "x.mp3").exists()


# --- C15: undo reconciles the crash window --------------------------------------

def test_undo_reverses_moved_but_pending_action(tmp_path: Path):
    root = tmp_path / "lib"
    make_library(root, ["a.mp3"])
    runs = tmp_path / "runs"
    plan = Plan(
        library_root=root,
        actions=[Action(MOVE, root / "a.mp3", root / "sub" / "a.mp3", reason="x")],
    )
    journal = apply_plan(plan, runs, backup=False)
    assert (root / "sub" / "a.mp3").exists() and not (root / "a.mp3").exists()

    # Simulate a crash between os.rename and the journal flush: the file moved,
    # but the action is recorded PENDING. undo must still reverse it.
    j = load_journal(runs / journal.run_id)
    j.actions[0].status = PENDING
    write_journal(j)

    undo_run(journal.run_id, runs)
    assert (root / "a.mp3").exists() and not (root / "sub" / "a.mp3").exists()


# --- C2: cue-safety guard -------------------------------------------------------

def test_cue_safety_guard_refuses_moves_without_xml():
    from librarian.cli import _require_cue_safety

    root = Path("/lib")
    moving = Plan(
        library_root=root,
        actions=[Action(MOVE, root / "a.mp3", root / "b.mp3", reason="x")],
    )
    with pytest.raises(typer.Exit):
        _require_cue_safety(moving, no_rekordbox=False)

    # explicit opt-out is allowed
    _require_cue_safety(moving, no_rekordbox=True)
    # a plan that moves nothing needs no XML
    _require_cue_safety(Plan(library_root=root, actions=[]), no_rekordbox=False)


# --- C1/C6: unified runs dir ----------------------------------------------------

def test_default_runs_dir_is_a_library_sibling(monkeypatch):
    monkeypatch.delenv("LIBRARIAN_RUNS_DIR", raising=False)
    rd = default_runs_dir(Path("/Users/x/DJ/library"))
    assert rd == Path("/Users/x/DJ/.librarian-runs")
    # never inside the library root itself
    assert not str(rd).startswith("/Users/x/DJ/library")


def test_default_runs_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("LIBRARIAN_RUNS_DIR", str(tmp_path / "custom-runs"))
    assert default_runs_dir(Path("/anything/library")) == (tmp_path / "custom-runs").absolute()
