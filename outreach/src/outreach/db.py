"""SQLite storage for the outreach pipeline: connection + schema.

One plain SQLite file on the user's Mac. Open it with any SQLite browser; back it
up by copying the file or running `outreach export`. The schema is created on
demand and is safe to (re)apply — every statement is IF NOT EXISTS.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    org             TEXT,
    role            TEXT,
    city            TEXT,
    venue_capacity  INTEGER,
    genre_fit       TEXT,
    source          TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline (
    contact_id      INTEGER PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
    stage           TEXT NOT NULL,
    last_touch      TEXT,
    next_followup   TEXT
);

CREATE TABLE IF NOT EXISTS touches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id      INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    ts              TEXT NOT NULL,
    channel         TEXT,
    note            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_stage    ON pipeline(stage);
CREATE INDEX IF NOT EXISTS idx_pipeline_followup ON pipeline(next_followup);
CREATE INDEX IF NOT EXISTS idx_touches_contact   ON touches(contact_id);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the outreach database with sane pragmas.

    Pass ``":memory:"`` for an ephemeral DB (used by the tests). Rows come back
    as ``sqlite3.Row`` so columns are addressable by name, and foreign keys are
    enforced so deleting a contact cascades to its pipeline + touches.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema if it isn't there yet. Idempotent."""
    conn.executescript(_SCHEMA)
    conn.commit()
