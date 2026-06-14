"""End-to-end CLI tests: scan → list → status → plan → dashboard against the
synthetic library, plus the db idempotency / override-survival invariant."""

from __future__ import annotations

from typer.testing import CliRunner

from releases import db as dbmod
from releases.cli import app

runner = CliRunner()


def _run(args):
    return runner.invoke(app, args)


def test_scan_then_list_closest(synth_library, tmp_path):
    db = tmp_path / "r.db"
    r = _run(["scan", str(synth_library), "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "Indexed" in r.output

    r = _run(["list", "--closest", "--db", str(db)])
    assert r.exit_code == 0
    # a complete track should sit above a bones/mels sketch
    assert r.output.index("Encara") < r.output.index("meh")


def test_status_logs_and_survives_rescan(synth_library, tmp_path):
    db = tmp_path / "r.db"
    _run(["scan", str(synth_library), "--db", str(db)])

    r = _run(["status", "Encara", "released", "--note", "out now", "--db", str(db)])
    assert r.exit_code == 0
    assert "Released" in r.output

    # re-scan must not clobber the manual override
    _run(["scan", str(synth_library), "--db", str(db)])
    conn = dbmod.connect(db)
    row = conn.execute("SELECT stage_manual FROM projects WHERE name='Encara'").fetchone()
    assert row["stage_manual"] == "released"
    log = conn.execute("SELECT to_stage, note FROM status_log").fetchone()
    assert log["to_stage"] == "released" and log["note"] == "out now"


def test_status_rejects_unknown_stage(synth_library, tmp_path):
    db = tmp_path / "r.db"
    _run(["scan", str(synth_library), "--db", str(db)])
    r = _run(["status", "Encara", "banger", "--db", str(db)])
    assert r.exit_code == 1
    assert "Unknown stage" in r.output


def test_status_ambiguous_lists_candidates(synth_library, tmp_path):
    db = tmp_path / "r.db"
    _run(["scan", str(synth_library), "--db", str(db)])
    r = _run(["status", "e", "complete", "--db", str(db)])  # matches many
    assert r.exit_code == 1
    assert "ambiguous" in r.output


def test_plan_persists_schedule_and_dashboard_shows_it(synth_library, tmp_path):
    db = tmp_path / "r.db"
    _run(["scan", str(synth_library), "--db", str(db)])

    r = _run(["plan", "--cadence", "single/3w", "--target", "EP by Aug 31", "--db", str(db)])
    assert r.exit_code == 0
    assert "Release calendar" in r.output
    assert "EP" in r.output

    r = _run(["dashboard", "--db", str(db)])
    assert r.exit_code == 0
    assert "By stage" in r.output
    assert "Schedule" in r.output
    assert "Closest to done" in r.output


def test_plan_bad_cadence_errors(synth_library, tmp_path):
    db = tmp_path / "r.db"
    _run(["scan", str(synth_library), "--db", str(db)])
    r = _run(["plan", "--cadence", "nonsense", "--db", str(db)])
    assert r.exit_code == 1


def test_plan_impossible_date_errors_cleanly(synth_library, tmp_path):
    db = tmp_path / "r.db"
    _run(["scan", str(synth_library), "--db", str(db)])
    r = _run(["plan", "--target", "EP by Feb 30", "--db", str(db)])
    assert r.exit_code == 1                 # PlanError caught — no traceback
    assert "Traceback" not in r.output


def test_scan_refuses_db_inside_library(synth_library, tmp_path):
    inside = synth_library / ".releases.db"
    r = _run(["scan", str(synth_library), "--db", str(inside)])
    assert r.exit_code == 1
    assert "Refusing" in r.output
    assert not inside.exists()              # nothing written into the library


def test_empty_db_guides_user(tmp_path):
    db = tmp_path / "empty.db"
    r = _run(["list", "--db", str(db)])
    assert r.exit_code == 0
    assert "releases scan" in r.output
