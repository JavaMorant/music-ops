"""End-to-end CLI tests: scan → list → status → plan → dashboard against the
synthetic library, plus the db idempotency / override-survival invariant."""

from __future__ import annotations

from typer.testing import CliRunner

from releases import cli as climod
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


# --- release sub-app ------------------------------------------------------

def _scanned_db(synth_library, tmp_path):
    db = tmp_path / "r.db"
    _run(["scan", str(synth_library), "--db", str(db)])
    return db


def test_release_create_add_show_flow(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    r = _run(["release", "new", "Summer EP", "--kind", "ep", "--db", str(db)])
    assert r.exit_code == 0 and "Summer EP" in r.output

    r = _run(["release", "add", "Summer EP", "--top", "3", "--db", str(db)])
    assert r.exit_code == 0 and "Added 3" in r.output

    r = _run(["release", "show", "Summer EP", "--db", str(db)])
    assert r.exit_code == 0
    assert "Encara" in r.output and "track(s)" in r.output


def test_release_new_auto_untitled(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    r = _run(["release", "new", "--db", str(db)])
    assert "Untitled 1" in r.output


def test_release_add_bad_query_is_skipped_not_fatal(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    _run(["release", "new", "EP", "--db", str(db)])
    r = _run(["release", "add", "EP", "Encara", "zzznope", "--db", str(db)])
    assert r.exit_code == 0
    assert "skipped" in r.output and "Added 1" in r.output


def test_release_move_between_releases(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    _run(["release", "new", "A", "--db", str(db)])
    _run(["release", "new", "B", "--db", str(db)])
    _run(["release", "add", "A", "Encara", "--db", str(db)])
    r = _run(["release", "move", "Encara", "--to", "B", "--db", str(db)])
    assert r.exit_code == 0 and "A → B" in r.output


def test_plan_release_curated_and_dashboard(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    _run(["release", "new", "Summer EP", "--db", str(db)])
    _run(["release", "add", "Summer EP", "--top", "2", "--db", str(db)])
    r = _run(["plan", "--release", "Summer EP", "--target", "EP by Aug 31", "--db", str(db)])
    assert r.exit_code == 0
    assert "Summer EP (ep)" in r.output and "★ Summer EP" in r.output

    r = _run(["dashboard", "--db", str(db)])
    assert "Releases" in r.output and "scheduled" in r.output


def test_release_ship_marks_released(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    _run(["release", "new", "EP", "--db", str(db)])
    _run(["release", "add", "EP", "Encara", "--db", str(db)])
    r = _run(["release", "ship", "EP", "--db", str(db)])
    assert r.exit_code == 0 and "marked released" in r.output
    # the member project is now released in the index
    conn = dbmod.connect(db)
    row = conn.execute("SELECT stage_manual FROM projects WHERE name='Encara'").fetchone()
    assert row["stage_manual"] == "released"


def test_release_delete_force(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    _run(["release", "new", "Scratch", "--db", str(db)])
    r = _run(["release", "delete", "Scratch", "--force", "--db", str(db)])
    assert r.exit_code == 0 and "Deleted" in r.output
    r = _run(["release", "ls", "--db", str(db)])
    assert "No releases" in r.output


def test_any_command_refuses_db_inside_library(tmp_path, monkeypatch):
    # regression (critical): the --db guard must cover EVERY command, not just scan
    fake_lib = tmp_path / "ProducerLibrary"
    (fake_lib / "projects").mkdir(parents=True)
    monkeypatch.setattr(climod, "PROTECTED_LIBRARY", fake_lib)
    inside = fake_lib / "projects" / "sub" / "index.db"
    r = _run(["release", "new", "EP", "--db", str(inside)])
    assert r.exit_code == 1
    assert "Refusing" in r.output
    assert not inside.exists() and not inside.parent.exists()  # nothing written in the library
    # a non-mutating command is refused too, before opening anything
    r = _run(["dashboard", "--db", str(fake_lib / "x.db")])
    assert r.exit_code == 1 and "Refusing" in r.output


def test_add_top_zero_and_negative_error(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    _run(["release", "new", "EP", "--db", str(db)])
    for bad in ["0", "-3"]:
        r = _run(["release", "add", "EP", "--top", bad, "--db", str(db)])
        assert r.exit_code == 1
        assert "positive integer" in r.output


def test_rm_no_match_exits_nonzero(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    _run(["release", "new", "EP", "--db", str(db)])
    _run(["release", "add", "EP", "Encara", "--db", str(db)])
    r = _run(["release", "rm", "EP", "zzznope", "--db", str(db)])
    assert r.exit_code == 1
    assert "No matching tracks" in r.output


def test_plan_release_bad_name_clean_error(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    r = _run(["plan", "--release", "does-not-exist", "--db", str(db)])
    assert r.exit_code == 1
    assert "No release matches" in r.output
    assert "Traceback" not in r.output


def test_plan_release_does_not_wipe_other_release(synth_library, tmp_path):
    db = _scanned_db(synth_library, tmp_path)
    _run(["release", "new", "A", "--db", str(db)])
    _run(["release", "new", "B", "--db", str(db)])
    _run(["release", "add", "A", "Encara", "--db", str(db)])
    _run(["release", "add", "B", "joonya", "--db", str(db)])
    _run(["plan", "--release", "A", "--target", "EP by Aug 31", "--db", str(db)])
    _run(["plan", "--release", "B", "--target", "EP by Sep 30", "--db", str(db)])
    conn = dbmod.connect(db)
    names = {r["name"] for r in conn.execute("SELECT DISTINCT name FROM schedule")}
    assert {"A", "B"} <= names  # both releases survive on the calendar


def test_full_release_flow_never_writes_library(synth_library, tmp_path):
    import hashlib, os
    from pathlib import Path

    def meta(root):
        out = {}
        for dp, _d, fs in os.walk(root):
            for f in fs:
                p = Path(dp) / f
                st = p.stat()
                out[str(p.relative_to(root))] = (st.st_size, st.st_mtime)
        return out

    before = meta(synth_library)
    db = _scanned_db(synth_library, tmp_path)
    _run(["release", "new", "EP", "--db", str(db)])
    _run(["release", "add", "EP", "--top", "3", "--db", str(db)])
    _run(["plan", "--release", "EP", "--target", "EP by Aug 31", "--db", str(db)])
    _run(["release", "ship", "EP", "--db", str(db)])
    assert meta(synth_library) == before  # library untouched throughout
