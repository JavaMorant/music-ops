"""outreach CLI — a booking pipeline you drive by hand.

    outreach add "Name" --org "Venue" --city London   # add a contact (a 'lead')
    outreach list [--stage contacted]                  # see the pipeline
    outreach due                                       # follow-ups owed today
    outreach log <contact> "sent intro" --stage contacted
    outreach draft <contact> --profile me.toml         # write an email draft FILE
    outreach import contacts.csv                        # bulk-add from CSV
    outreach export backup.csv                          # back the pipeline up

Hard rule: this tool DRAFTS and TRACKS. It never sends. `draft` writes a file
for you to review, edit, and send yourself.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import drafting
from .drafting import DraftError
from .model import STAGES, Contact
from .store import StoreError
from . import db, store

app = typer.Typer(
    help="Booking-outreach CRM: draft and track personalised outreach. Never sends.",
    no_args_is_help=True,
)

DEFAULT_DB = Path("outreach.db")
DEFAULT_DRAFTS_DIR = Path("drafts")
DEFAULT_PROFILE = Path("outreach-profile.toml")


def _open(db_path: Path):
    return db.connect(db_path)


def _parse_date(value: str) -> str:
    """Validate a YYYY-MM-DD string and return it normalised."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise typer.BadParameter(f"{value!r} is not a date (use YYYY-MM-DD)")


def _fmt_row(pr, root_today: date) -> str:
    c, s = pr.contact, pr.state
    where = " / ".join(x for x in (c.org, c.city) if x) or "—"
    tail = ""
    if s.next_followup:
        overdue = s.next_followup <= root_today.isoformat()
        tail = f"   next: {s.next_followup}" + ("  (due)" if overdue else "")
    return f"  [{c.id:>3}] {c.name:<24} {s.stage:<12} {where}{tail}"


# --- commands -------------------------------------------------------------


@app.command()
def add(
    name: Annotated[str, typer.Argument(help="Contact / booker name")],
    org: Annotated[Optional[str], typer.Option("--org", help="Venue / promoter / agency")] = None,
    role: Annotated[Optional[str], typer.Option("--role", help="Their role, e.g. 'talent buyer'")] = None,
    city: Annotated[Optional[str], typer.Option("--city", help="City / scene")] = None,
    capacity: Annotated[Optional[int], typer.Option("--capacity", help="Venue capacity")] = None,
    genre_fit: Annotated[Optional[str], typer.Option("--genre-fit", help="Why your sound fits them")] = None,
    source: Annotated[Optional[str], typer.Option("--source", help="Where you found them")] = None,
    notes: Annotated[Optional[str], typer.Option("--notes", help="Anything to remember")] = None,
    followup_in: Annotated[int, typer.Option("--followup-in", help="Days until first contact is owed (0 = now)")] = 0,
    db_path: Annotated[Path, typer.Option("--db", help="Pipeline database")] = DEFAULT_DB,
) -> None:
    """Add a contact as a new 'lead' in the pipeline."""
    conn = _open(db_path)
    try:
        contact = store.add_contact(
            conn,
            Contact(name=name, org=org, role=role, city=city, venue_capacity=capacity,
                    genre_fit=genre_fit, source=source, notes=notes),
            followup_in=followup_in,
        )
    except StoreError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"Added [{contact.id}] {contact.name} as a lead.", fg="green")
    typer.echo(f"Draft an intro when ready:  outreach draft {contact.id}")


@app.command("import")
def import_(
    csv_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="CSV with a 'name' column (org, role, city, venue_capacity, genre_fit, source, notes optional)")],
    db_path: Annotated[Path, typer.Option("--db", help="Pipeline database")] = DEFAULT_DB,
) -> None:
    """Bulk-add contacts from a CSV file. Rows with no name are skipped."""
    with csv_file.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    conn = _open(db_path)
    added = store.import_contacts(conn, rows)
    typer.secho(f"Imported {len(added)} contacts (of {len(rows)} rows).", fg="green")


@app.command("list")
def list_(
    stage: Annotated[Optional[str], typer.Option("--stage", help=f"Filter by stage: {', '.join(STAGES)}")] = None,
    db_path: Annotated[Path, typer.Option("--db", help="Pipeline database")] = DEFAULT_DB,
) -> None:
    """List the pipeline, ordered down the funnel."""
    conn = _open(db_path)
    try:
        rows = store.list_contacts(conn, stage)
    except StoreError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)
    if not rows:
        typer.echo("No contacts yet — add one with:  outreach add \"Name\" --org …")
        return
    today = date.today()
    header = f"Pipeline ({len(rows)} contacts" + (f", stage={stage}" if stage else "") + ")"
    typer.echo(header)
    for pr in rows:
        typer.echo(_fmt_row(pr, today))


@app.command()
def due(
    on: Annotated[Optional[str], typer.Option("--on", help="Treat this YYYY-MM-DD as 'today'")] = None,
    db_path: Annotated[Path, typer.Option("--db", help="Pipeline database")] = DEFAULT_DB,
) -> None:
    """Show follow-ups owed — non-terminal contacts whose next nudge is due."""
    today = date.fromisoformat(_parse_date(on)) if on else date.today()
    conn = _open(db_path)
    rows = store.due(conn, today=today)
    if not rows:
        typer.secho("Nothing due — inbox zero on outreach. ✦", fg="green")
        return
    typer.echo(f"{len(rows)} follow-up(s) owed as of {today.isoformat()}:")
    for pr in rows:
        typer.echo(_fmt_row(pr, today))
    typer.echo("\nDraft a nudge with:  outreach draft <id> --template followup")


@app.command()
def log(
    contact: Annotated[str, typer.Argument(help="Contact id or name")],
    note: Annotated[str, typer.Argument(help="What happened, e.g. 'sent intro email'")],
    channel: Annotated[Optional[str], typer.Option("--channel", help="email / DM / call …")] = None,
    stage: Annotated[Optional[str], typer.Option("--stage", help=f"Advance to a stage: {', '.join(STAGES)}")] = None,
    followup_in: Annotated[int, typer.Option("--followup-in", help="Days until the next nudge (default 7)")] = store.DEFAULT_FOLLOWUP_DAYS,
    on: Annotated[Optional[str], typer.Option("--on", help="Schedule the next follow-up on this YYYY-MM-DD")] = None,
    no_followup: Annotated[bool, typer.Option("--no-followup", help="Don't schedule a follow-up")] = False,
    db_path: Annotated[Path, typer.Option("--db", help="Pipeline database")] = DEFAULT_DB,
) -> None:
    """Record a touch; updates last-touch and schedules the next follow-up."""
    conn = _open(db_path)
    try:
        c = store.get_contact(conn, contact)
        state = store.log_touch(
            conn, c.id, note, channel=channel, advance_to=stage,
            followup_in=followup_in, followup_on=_parse_date(on) if on else None,
            no_followup=no_followup,
        )
    except StoreError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"Logged on [{c.id}] {c.name} → stage '{state.stage}'.", fg="green")
    if state.next_followup:
        typer.echo(f"Next follow-up: {state.next_followup}")
    else:
        typer.echo("No follow-up scheduled.")


@app.command()
def draft(
    contact: Annotated[str, typer.Argument(help="Contact id or name")],
    template: Annotated[str, typer.Option("--template", help="Template name (cold, followup) or path to a .md file")] = drafting.DEFAULT_TEMPLATE,
    profile: Annotated[Optional[Path], typer.Option("--profile", help="Sender profile TOML (your name/links); see outreach-profile.example.toml")] = None,
    out_dir: Annotated[Path, typer.Option("--out", help="Where to write the draft")] = DEFAULT_DRAFTS_DIR,
    db_path: Annotated[Path, typer.Option("--db", help="Pipeline database")] = DEFAULT_DB,
) -> None:
    """Write a personalised email DRAFT to a file. Never sends — you review and
    send it yourself."""
    conn = _open(db_path)
    profile_path = profile if profile is not None else (DEFAULT_PROFILE if DEFAULT_PROFILE.exists() else None)
    try:
        c = store.get_contact(conn, contact)
        state = store.get_state(conn, c.id)
        touches = store.recent_touches(conn, c.id, limit=3)
        rendered = drafting.render_draft(
            c, template=template, state=state, touches=touches,
            profile=drafting.load_profile(profile_path),
        )
        dest = drafting.write_draft(rendered, out_dir, c, template)
    except (StoreError, DraftError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"Draft written: {dest}", fg="green")
    if rendered.unfilled:
        typer.secho(
            f"Fill before sending ({len(rendered.unfilled)}): "
            + ", ".join(rendered.unfilled),
            fg="yellow",
        )
    typer.echo("Review, edit, and send it yourself — outreach never sends.")
    typer.echo(f"After you send:  outreach log {c.id} \"sent intro\" --stage contacted")


@app.command()
def export(
    csv_file: Annotated[Path, typer.Argument(dir_okay=False, help="Where to write the CSV backup")],
    db_path: Annotated[Path, typer.Option("--db", help="Pipeline database")] = DEFAULT_DB,
) -> None:
    """Export the whole pipeline to CSV for backup."""
    conn = _open(db_path)
    rows = store.export_rows(conn)
    with csv_file.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(store.EXPORT_FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    typer.secho(f"Exported {len(rows)} contacts → {csv_file}", fg="green")


if __name__ == "__main__":
    app()
