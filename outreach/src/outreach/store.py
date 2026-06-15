"""The pipeline engine: add contacts, move them through stages, log touches,
surface what's due. This is the core logic the tests cover.

Every function takes an open ``sqlite3.Connection`` so the engine is trivially
testable against an in-memory DB. Dates are ISO strings; any function that needs
"today" takes a ``today`` argument (defaulting to the real date) so behaviour is
deterministic under test. Nothing here sends anything — it only reads and writes
the local database.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import date, timedelta

from .model import (
    LEAD,
    STAGES,
    TERMINAL_STAGES,
    Contact,
    PipelineRow,
    PipelineState,
    Touch,
    stage_rank,
)

# Default cadence: if you log a touch and don't say otherwise, nudge again in a
# week. A brand-new lead is due for first contact immediately (next_followup =
# the day it was added).
DEFAULT_FOLLOWUP_DAYS = 7


class StoreError(RuntimeError):
    """A pipeline operation could not be completed (bad ref, unknown stage…)."""


def _today(today: date | None) -> date:
    return today or date.today()


def _next_followup(stage: str, ref: date, days: int) -> str | None:
    """The next-follow-up date for ``stage``: None once terminal, else ref+days."""
    if stage in TERMINAL_STAGES:
        return None
    return (ref + timedelta(days=days)).isoformat()


# --- create ---------------------------------------------------------------


def add_contact(
    conn: sqlite3.Connection,
    contact: Contact,
    *,
    today: date | None = None,
    followup_in: int = 0,
) -> Contact:
    """Insert a contact and open its pipeline row at stage 'lead'.

    ``followup_in`` is days from today until the first contact is owed; the
    default 0 means the new lead shows up in `due` right away as "reach out".
    Returns a fresh Contact carrying the assigned ``id`` and ``created_at`` (the
    caller's input is left untouched).
    """
    if not contact.name or not contact.name.strip():
        raise StoreError("a contact needs a name")
    name = contact.name.strip()
    day = _today(today)
    created = day.isoformat()
    cur = conn.execute(
        """INSERT INTO contacts
           (name, org, role, city, venue_capacity, genre_fit, source, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name,
            contact.org,
            contact.role,
            contact.city,
            contact.venue_capacity,
            contact.genre_fit,
            contact.source,
            contact.notes,
            created,
        ),
    )
    contact_id = cur.lastrowid
    conn.execute(
        "INSERT INTO pipeline (contact_id, stage, last_touch, next_followup) VALUES (?, 'lead', NULL, ?)",
        (contact_id, _next_followup("lead", day, followup_in)),
    )
    conn.commit()
    return dataclasses.replace(contact, name=name, id=contact_id, created_at=created)


def import_contacts(
    conn: sqlite3.Connection,
    rows: list[dict],
    *,
    today: date | None = None,
) -> list[Contact]:
    """Bulk-add contacts from already-parsed CSV rows (dict per row).

    Recognised columns map onto contact fields; anything else is ignored. A row
    with no ``name`` is skipped. Returns the contacts that were added.

    If a row carries pipeline-state columns (``stage`` / ``last_touch`` /
    ``next_followup`` — the ones `export` writes), they are restored too, so an
    `export` → `import` into a fresh database is a faithful backup rather than
    resetting every contact to a new lead. Plain bulk-add CSVs that lack those
    columns just create leads, as before.
    """
    # Validate any stage values up front so a corrupt backup aborts before we
    # write anything half-way (each add commits, so this can't be transactional).
    for row in rows:
        stage = (row.get("stage") or "").strip()
        if stage and stage not in STAGES:
            raise StoreError(
                f"row for {row.get('name')!r}: unknown stage {stage!r}; valid: {', '.join(STAGES)}"
            )

    added: list[Contact] = []
    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        cap = row.get("venue_capacity")
        try:
            capacity = int(cap) if cap not in (None, "") else None
        except (TypeError, ValueError):
            capacity = None
        contact = add_contact(
            conn,
            Contact(
                name=name,
                org=(row.get("org") or None),
                role=(row.get("role") or None),
                city=(row.get("city") or None),
                venue_capacity=capacity,
                genre_fit=(row.get("genre_fit") or None),
                source=(row.get("source") or None),
                notes=(row.get("notes") or None),
            ),
            today=today,
        )
        # Restore pipeline state if the backup carried it.
        stage = (row.get("stage") or "").strip()
        last_touch = (row.get("last_touch") or "").strip() or None
        next_followup = (row.get("next_followup") or "").strip() or None
        if stage or last_touch or next_followup:
            final_stage = stage or LEAD
            # A terminal stage never carries a follow-up — normalise on restore.
            nf = None if final_stage in TERMINAL_STAGES else next_followup
            conn.execute(
                "UPDATE pipeline SET stage = ?, last_touch = ?, next_followup = ? WHERE contact_id = ?",
                (final_stage, last_touch, nf, contact.id),
            )
            conn.commit()
        added.append(contact)
    return added


# --- read -----------------------------------------------------------------


def get_contact(conn: sqlite3.Connection, ref: str) -> Contact:
    """Resolve a contact by numeric id or (case-insensitive) name.

    Raises StoreError if nothing matches, or if a name is ambiguous — better to
    refuse than draft to the wrong promoter.
    """
    ref = str(ref).strip()
    if ref.isdigit():
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (int(ref),)).fetchone()
        if not row:
            raise StoreError(f"no contact with id {ref}")
        return Contact.from_row(row)

    rows = conn.execute(
        "SELECT * FROM contacts WHERE name = ? COLLATE NOCASE ORDER BY id", (ref,)
    ).fetchall()
    if not rows:
        raise StoreError(f"no contact named {ref!r} (try the numeric id)")
    if len(rows) > 1:
        ids = ", ".join(str(r["id"]) for r in rows)
        raise StoreError(f"{ref!r} matches several contacts (ids: {ids}); use the id")
    return Contact.from_row(rows[0])


def get_state(conn: sqlite3.Connection, contact_id: int) -> PipelineState:
    row = conn.execute(
        "SELECT * FROM pipeline WHERE contact_id = ?", (contact_id,)
    ).fetchone()
    if not row:
        raise StoreError(f"contact {contact_id} has no pipeline row")
    return PipelineState.from_row(row)


def list_contacts(
    conn: sqlite3.Connection, stage: str | None = None
) -> list[PipelineRow]:
    """All contacts (optionally filtered to one stage), ordered down the funnel
    then by name."""
    if stage is not None and stage not in STAGES:
        raise StoreError(f"unknown stage {stage!r}; valid: {', '.join(STAGES)}")
    sql = (
        "SELECT c.*, p.stage, p.last_touch, p.next_followup "
        "FROM contacts c JOIN pipeline p ON p.contact_id = c.id"
    )
    params: tuple = ()
    if stage is not None:
        sql += " WHERE p.stage = ?"
        params = (stage,)
    rows = conn.execute(sql, params).fetchall()
    out = [
        PipelineRow(
            contact=Contact.from_row(r),
            state=PipelineState(
                contact_id=r["id"],
                stage=r["stage"],
                last_touch=r["last_touch"],
                next_followup=r["next_followup"],
            ),
        )
        for r in rows
    ]
    out.sort(key=lambda pr: (stage_rank(pr.state.stage), pr.contact.name.lower()))
    return out


def due(conn: sqlite3.Connection, today: date | None = None) -> list[PipelineRow]:
    """Contacts with a follow-up owed: a non-terminal stage and a
    ``next_followup`` on or before today. Soonest (most overdue) first."""
    cutoff = _today(today).isoformat()
    rows = conn.execute(
        "SELECT c.*, p.stage, p.last_touch, p.next_followup "
        "FROM contacts c JOIN pipeline p ON p.contact_id = c.id "
        "WHERE p.next_followup IS NOT NULL AND p.next_followup <= ? "
        "ORDER BY p.next_followup ASC, c.id ASC",
        (cutoff,),
    ).fetchall()
    return [
        PipelineRow(
            contact=Contact.from_row(r),
            state=PipelineState(
                contact_id=r["id"],
                stage=r["stage"],
                last_touch=r["last_touch"],
                next_followup=r["next_followup"],
            ),
        )
        for r in rows
    ]


def recent_touches(
    conn: sqlite3.Connection, contact_id: int, limit: int = 5
) -> list[Touch]:
    rows = conn.execute(
        "SELECT * FROM touches WHERE contact_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
        (contact_id, limit),
    ).fetchall()
    return [Touch.from_row(r) for r in rows]


# --- update ---------------------------------------------------------------


def log_touch(
    conn: sqlite3.Connection,
    contact_id: int,
    note: str,
    *,
    channel: str | None = None,
    advance_to: str | None = None,
    today: date | None = None,
    followup_in: int = DEFAULT_FOLLOWUP_DAYS,
    followup_on: str | None = None,
    no_followup: bool = False,
) -> PipelineState:
    """Record a touch and recompute the pipeline state.

    Sets ``last_touch`` to today, optionally advances the stage, and schedules
    the next follow-up: explicit ``followup_on`` wins, else today+``followup_in``;
    ``no_followup`` (or reaching a terminal stage) clears it entirely.
    """
    if not note or not note.strip():
        raise StoreError("a touch needs a note")
    state = get_state(conn, contact_id)  # validates the contact exists
    stage = state.stage
    if advance_to is not None:
        if advance_to not in STAGES:
            raise StoreError(
                f"unknown stage {advance_to!r}; valid: {', '.join(STAGES)}"
            )
        stage = advance_to

    day = _today(today)
    if no_followup or stage in TERMINAL_STAGES:
        next_followup = None
    elif followup_on is not None:
        next_followup = followup_on
    else:
        next_followup = _next_followup(stage, day, followup_in)

    conn.execute(
        "INSERT INTO touches (contact_id, ts, channel, note) VALUES (?, ?, ?, ?)",
        (contact_id, day.isoformat(), channel, note.strip()),
    )
    conn.execute(
        "UPDATE pipeline SET stage = ?, last_touch = ?, next_followup = ? WHERE contact_id = ?",
        (stage, day.isoformat(), next_followup, contact_id),
    )
    conn.commit()
    return PipelineState(contact_id, stage, day.isoformat(), next_followup)


def set_stage(
    conn: sqlite3.Connection, contact_id: int, stage: str, *, today: date | None = None
) -> PipelineState:
    """Move a contact to ``stage`` without logging a touch. Clears the follow-up
    when the new stage is terminal."""
    if stage not in STAGES:
        raise StoreError(f"unknown stage {stage!r}; valid: {', '.join(STAGES)}")
    state = get_state(conn, contact_id)
    next_followup = None if stage in TERMINAL_STAGES else state.next_followup
    conn.execute(
        "UPDATE pipeline SET stage = ?, next_followup = ? WHERE contact_id = ?",
        (stage, next_followup, contact_id),
    )
    conn.commit()
    return PipelineState(contact_id, stage, state.last_touch, next_followup)


# --- export ---------------------------------------------------------------

EXPORT_FIELDS = (
    "id",
    "name",
    "org",
    "role",
    "city",
    "venue_capacity",
    "genre_fit",
    "source",
    "stage",
    "last_touch",
    "next_followup",
    "notes",
)


def export_rows(conn: sqlite3.Connection) -> list[dict]:
    """The whole pipeline as flat dicts (contact + current state) for CSV backup,
    ordered down the funnel then by name."""
    return [
        {
            "id": pr.contact.id,
            "name": pr.contact.name,
            "org": pr.contact.org or "",
            "role": pr.contact.role or "",
            "city": pr.contact.city or "",
            "venue_capacity": pr.contact.venue_capacity if pr.contact.venue_capacity is not None else "",
            "genre_fit": pr.contact.genre_fit or "",
            "source": pr.contact.source or "",
            "stage": pr.state.stage,
            "last_touch": pr.state.last_touch or "",
            "next_followup": pr.state.next_followup or "",
            "notes": pr.contact.notes or "",
        }
        for pr in list_contacts(conn)
    ]
