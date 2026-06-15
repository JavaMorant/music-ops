"""CLI smoke tests over the full add → log → due → draft → export flow."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from outreach.cli import app

runner = CliRunner()


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "outreach.db"


def run(*args):
    return runner.invoke(app, [str(a) for a in args])


def test_add_list_due_log_flow(db_path):
    r = run("add", "Sam Promoter", "--org", "Corsica", "--city", "London", "--db", db_path)
    assert r.exit_code == 0, r.stdout
    assert "Added" in r.stdout

    r = run("list", "--db", db_path)
    assert "Sam Promoter" in r.stdout
    assert "lead" in r.stdout

    # new lead is due for first contact today
    r = run("due", "--db", db_path)
    assert "Sam Promoter" in r.stdout

    # log a touch, advancing the stage
    r = run("log", "1", "sent intro", "--stage", "contacted", "--db", db_path)
    assert r.exit_code == 0
    assert "contacted" in r.stdout

    # now nothing is due until the +7 follow-up
    r = run("due", "--db", db_path)
    assert "Nothing due" in r.stdout
    # …but it is due if we look a fortnight ahead
    r = run("due", "--on", "2026-12-31", "--db", db_path)
    assert "Sam Promoter" in r.stdout


def test_draft_writes_file_with_fill_markers(tmp_path, db_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run("add", "Sam", "--org", "Corsica", "--db", db_path).exit_code == 0
    r = run("draft", "1", "--db", db_path)
    assert r.exit_code == 0, r.stdout
    assert "Draft written" in r.stdout
    assert "Fill before sending" in r.stdout  # no profile → me.* + literals flagged
    drafts = list((tmp_path / "drafts").glob("*.md"))
    assert len(drafts) == 1


def test_draft_uses_profile(tmp_path, db_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / "p.toml"
    profile.write_text('[me]\nname = "Dibsss"\nemail = "x@y.z"\ntagline = "t"\nlinks = "l"\n', encoding="utf-8")
    run("add", "Sam", "--org", "Corsica", "--city", "London", "--genre-fit", "amapiano", "--db", db_path)
    r = run("draft", "1", "--profile", profile, "--db", db_path)
    assert r.exit_code == 0
    body = next((tmp_path / "drafts").glob("*.md")).read_text(encoding="utf-8")
    assert "Dibsss" in body
    assert "amapiano" in body


def test_import_and_export(tmp_path, db_path):
    src = tmp_path / "contacts.csv"
    src.write_text("name,org,city\nSam,Corsica,London\nLee,Fold,London\n", encoding="utf-8")
    r = run("import", src, "--db", db_path)
    assert "Imported 2" in r.stdout

    out = tmp_path / "backup.csv"
    r = run("export", out, "--db", db_path)
    assert r.exit_code == 0
    text = out.read_text(encoding="utf-8")
    assert "Sam" in text and "Lee" in text
    assert text.splitlines()[0].startswith("id,name,org")


def test_log_unknown_contact_errors(db_path):
    r = run("log", "999", "note", "--db", db_path)
    assert r.exit_code == 1


def test_help_mentions_the_never_sends_rule():
    r = run("--help")
    assert r.exit_code == 0
    assert "never sends" in r.stdout.lower()
