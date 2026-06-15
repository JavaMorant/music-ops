"""The hard rule, pinned: outreach DRAFTS and TRACKS — it never sends.

This is outreach's safety invariant (the analog of librarian's never-delete).
If anyone ever wires a mail transport into this tool, these tests fail.
"""

from __future__ import annotations

from pathlib import Path

import outreach
from outreach.cli import app

SRC = Path(outreach.__file__).parent

# Tokens that would indicate the code can actually transmit a message.
FORBIDDEN = (
    "smtplib",
    "sendmail",
    "SMTP(",
    "SMTP_SSL",
    "yagmail",
    "sendgrid",
    "mailgun",
    "ses.send",
    "send_message(",
    "send_raw_email",
)


def test_no_mail_transport_anywhere_in_source():
    offenders = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                offenders.append(f"{py.name}: {token}")
    assert not offenders, f"outreach must never send — found: {offenders}"


def test_cli_exposes_no_send_command():
    # Typer leaves cmd.name None for @app.command() with no explicit name, so the
    # effective name comes from the callback (trailing underscore stripped: list_).
    names = {
        (cmd.name or cmd.callback.__name__.rstrip("_")) for cmd in app.registered_commands
    }
    assert "send" not in names
    # the commands we *do* ship are all draft/track operations
    assert {"add", "list", "due", "log", "draft", "export", "import"} <= names


def test_draft_only_writes_a_file(tmp_path, monkeypatch):
    """End-to-end: `draft` produces a file on disk and transmits nothing."""
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    db_path = tmp_path / "o.db"
    assert runner.invoke(app, ["add", "Sam", "--org", "Corsica", "--db", str(db_path)]).exit_code == 0
    result = runner.invoke(app, ["draft", "1", "--db", str(db_path)])
    assert result.exit_code == 0
    drafts = list((tmp_path / "drafts").glob("*.md"))
    assert len(drafts) == 1
    assert "never sends" in result.stdout.lower()
