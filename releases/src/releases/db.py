"""SQLite index for scanned projects, the schedule, and the status log.

The db file lives **outside** the scanned library (default ``~/.releases``) so
the read-only invariant over ProducerLibrary is never at risk. Re-scanning is
idempotent: project rows upsert by path and a manual stage override (set via
``releases status``) survives re-scans.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .model import Project

DEFAULT_DB = Path.home() / ".releases" / "releases.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    path          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    stage         TEXT NOT NULL,
    stage_manual  TEXT,
    genre         TEXT,
    bpm           INTEGER,
    "key"         TEXT,
    has_bounce    INTEGER NOT NULL DEFAULT 0,
    flp_count     INTEGER NOT NULL DEFAULT 0,
    last_modified REAL NOT NULL DEFAULT 0,
    scanned_at    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS status_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL,
    name       TEXT NOT NULL,
    from_stage TEXT,
    to_stage   TEXT NOT NULL,
    note       TEXT,
    ts         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL,
    name       TEXT NOT NULL,
    slot_date  TEXT NOT NULL,   -- ISO date YYYY-MM-DD
    slot_type  TEXT NOT NULL,   -- 'single' | 'ep'
    created_at REAL NOT NULL
);
"""


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _row_to_project(r: sqlite3.Row) -> Project:
    return Project(
        path=r["path"],
        name=r["name"],
        kind=r["kind"],
        stage=r["stage"],
        stage_manual=r["stage_manual"],
        genre=r["genre"] or "unknown",
        bpm=r["bpm"],
        key=r["key"],
        has_bounce=bool(r["has_bounce"]),
        flp_count=r["flp_count"],
        last_modified=r["last_modified"],
    )


def upsert_projects(conn: sqlite3.Connection, projects: list[Project]) -> int:
    """Insert/update scanned projects. Scan-derived fields are refreshed;
    ``stage_manual`` is preserved across re-scans (it's user state, not scanned).
    Returns the number of rows written."""
    now = time.time()
    rows = [
        (
            p.path, p.name, p.kind, p.stage, p.genre, p.bpm, p.key,
            int(p.has_bounce), p.flp_count, p.last_modified, now,
        )
        for p in projects
    ]
    conn.executemany(
        """
        INSERT INTO projects
            (path, name, kind, stage, genre, bpm, "key",
             has_bounce, flp_count, last_modified, scanned_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            name=excluded.name,
            kind=excluded.kind,
            stage=excluded.stage,
            genre=excluded.genre,
            bpm=excluded.bpm,
            "key"=excluded."key",
            has_bounce=excluded.has_bounce,
            flp_count=excluded.flp_count,
            last_modified=excluded.last_modified,
            scanned_at=excluded.scanned_at
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def all_projects(conn: sqlite3.Connection) -> list[Project]:
    cur = conn.execute("SELECT * FROM projects")
    return [_row_to_project(r) for r in cur.fetchall()]


def find_projects(conn: sqlite3.Connection, query: str) -> list[Project]:
    """Match projects by case-insensitive substring of name or path."""
    like = f"%{query.lower()}%"
    cur = conn.execute(
        "SELECT * FROM projects WHERE lower(name) LIKE ? OR lower(path) LIKE ? "
        "ORDER BY name",
        (like, like),
    )
    return [_row_to_project(r) for r in cur.fetchall()]


def set_stage(
    conn: sqlite3.Connection, project: Project, to_stage: str, note: str | None
) -> None:
    """Set a manual stage override on a project and log the move."""
    conn.execute(
        'UPDATE projects SET stage_manual=? WHERE path=?', (to_stage, project.path)
    )
    conn.execute(
        "INSERT INTO status_log (path, name, from_stage, to_stage, note, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (project.path, project.name, project.effective_stage, to_stage, note, time.time()),
    )
    conn.commit()


def replace_schedule(
    conn: sqlite3.Connection, entries: list[tuple[str, str, str, str]]
) -> None:
    """Replace the whole schedule. Each entry is (path, name, slot_date, slot_type)."""
    now = time.time()
    conn.execute("DELETE FROM schedule")
    conn.executemany(
        "INSERT INTO schedule (path, name, slot_date, slot_type, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(p, n, d, t, now) for (p, n, d, t) in entries],
    )
    conn.commit()


def get_schedule(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM schedule ORDER BY slot_date")
    return cur.fetchall()


def counts_by_stage(conn: sqlite3.Connection) -> dict[str, int]:
    """Project counts grouped by *effective* stage (manual override wins)."""
    cur = conn.execute(
        "SELECT COALESCE(stage_manual, stage) AS s, COUNT(*) AS n "
        "FROM projects GROUP BY s"
    )
    return {r["s"]: r["n"] for r in cur.fetchall()}
