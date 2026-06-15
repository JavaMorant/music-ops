"""Domain model for the booking CRM.

A ``Contact`` is a promoter / venue / booker. Its ``PipelineState`` is where it
sits in the funnel, and ``Touch`` rows are the contacts you've logged against it.

This tool **drafts and tracks** outreach — it never sends. Nothing in the model,
the store, or the drafter ever opens a mail connection; that invariant is pinned
by tests/test_never_sends.py.
"""

from __future__ import annotations

from dataclasses import dataclass

# Pipeline stages, in funnel order. Every new contact starts at LEAD; the last
# two are terminal — no follow-up is ever owed once a contact is booked or dead.
LEAD = "lead"
CONTACTED = "contacted"
REPLIED = "replied"
NEGOTIATING = "negotiating"
BOOKED = "booked"
DEAD = "dead"

STAGES = (LEAD, CONTACTED, REPLIED, NEGOTIATING, BOOKED, DEAD)
TERMINAL_STAGES = (BOOKED, DEAD)


def stage_rank(stage: str) -> int:
    """Funnel position for ordering; unknown stages sort last."""
    try:
        return STAGES.index(stage)
    except ValueError:
        return len(STAGES)


@dataclass
class Contact:
    """A booking contact. ``id`` and ``created_at`` are assigned by the store on
    insert; everything else is user-supplied and any field but ``name`` may be
    None — we never invent details we don't have.
    """

    name: str
    org: str | None = None
    role: str | None = None
    city: str | None = None
    venue_capacity: int | None = None
    genre_fit: str | None = None
    source: str | None = None
    notes: str | None = None
    id: int | None = None
    created_at: str | None = None  # ISO date

    @classmethod
    def from_row(cls, row) -> "Contact":
        return cls(
            id=row["id"],
            name=row["name"],
            org=row["org"],
            role=row["role"],
            city=row["city"],
            venue_capacity=row["venue_capacity"],
            genre_fit=row["genre_fit"],
            source=row["source"],
            notes=row["notes"],
            created_at=row["created_at"],
        )


@dataclass
class PipelineState:
    """Where a contact sits in the funnel and when the next nudge is owed.

    ``last_touch`` and ``next_followup`` are ISO dates (or None). A contact in a
    terminal stage always has ``next_followup`` None, so it never shows in `due`.
    """

    contact_id: int
    stage: str = LEAD
    last_touch: str | None = None
    next_followup: str | None = None

    @classmethod
    def from_row(cls, row) -> "PipelineState":
        return cls(
            contact_id=row["contact_id"],
            stage=row["stage"],
            last_touch=row["last_touch"],
            next_followup=row["next_followup"],
        )


@dataclass
class Touch:
    """One logged interaction with a contact (a sent draft, a reply, a call)."""

    contact_id: int
    note: str
    channel: str | None = None
    ts: str | None = None  # ISO date
    id: int | None = None

    @classmethod
    def from_row(cls, row) -> "Touch":
        return cls(
            id=row["id"],
            contact_id=row["contact_id"],
            note=row["note"],
            channel=row["channel"],
            ts=row["ts"],
        )


@dataclass
class PipelineRow:
    """A contact joined to its pipeline state — the unit shown by `list`/`due`."""

    contact: Contact
    state: PipelineState
