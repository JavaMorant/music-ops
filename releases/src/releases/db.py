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

-- A 'release' is a hand-curated single/EP: a named container holding an ordered
-- set of scanned projects (its 'tracks'). Index-only — no files are moved.
CREATE TABLE IF NOT EXISTS releases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,                     -- 'Untitled N' until renamed
    kind        TEXT NOT NULL DEFAULT 'ep',        -- 'single' | 'ep'
    status      TEXT NOT NULL DEFAULT 'planning',  -- 'planning' | 'scheduled' | 'released'
    target_date TEXT,                              -- ISO date, set when scheduled
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS release_tracks (
    release_id  INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,        -- references projects.path; NOT a hard FK
    position    INTEGER NOT NULL,     -- 1-based order within the release
    name_at_add TEXT NOT NULL,        -- snapshot, survives the project vanishing
    added_at    REAL NOT NULL,
    PRIMARY KEY (release_id, path)
);

CREATE INDEX IF NOT EXISTS idx_release_tracks_order
    ON release_tracks (release_id, position);
"""


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # honour release_tracks CASCADE
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive column adds (sqlite has no ADD COLUMN IF NOT EXISTS).
    Lets a curated release tag the schedule slots it produced."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(schedule)")}
    if "release_id" not in cols:
        conn.execute("ALTER TABLE schedule ADD COLUMN release_id INTEGER")
    if "position" not in cols:
        conn.execute("ALTER TABLE schedule ADD COLUMN position INTEGER")
    # Per-project marks (genre override + mix/master state) set via `mark`.
    # Like stage_manual, these are user state preserved across re-scans.
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    for col in ("genre_manual", "mix_state", "master_state", "artists", "pack_month"):
        if col not in pcols:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {col} TEXT")
    conn.commit()


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
        genre_manual=r["genre_manual"] if "genre_manual" in r.keys() else None,
        mix_state=r["mix_state"] if "mix_state" in r.keys() else None,
        master_state=r["master_state"] if "master_state" in r.keys() else None,
        artists=r["artists"] if "artists" in r.keys() else None,
        pack_month=r["pack_month"] if "pack_month" in r.keys() else None,
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


def set_marks(
    conn: sqlite3.Connection,
    path: str,
    *,
    genre: str | None = None,
    mix: str | None = None,
    master: str | None = None,
    artists: str | None = None,
    month: str | None = None,
) -> None:
    """Set per-project marks (only the ones provided). An empty string clears a
    free-text mark (artists/month). Index-only."""
    sets, vals = [], []
    if genre is not None:
        sets.append("genre_manual = ?"); vals.append(genre)
    if mix is not None:
        sets.append("mix_state = ?"); vals.append(mix)
    if master is not None:
        sets.append("master_state = ?"); vals.append(master)
    if artists is not None:
        sets.append("artists = ?"); vals.append(artists or None)
    if month is not None:
        sets.append("pack_month = ?"); vals.append(month or None)
    if not sets:
        return
    vals.append(path)
    conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE path = ?", vals)
    conn.commit()


def replace_schedule(conn: sqlite3.Connection, entries: list[dict]) -> None:
    """Replace the auto-picked schedule slots (those with no release_id), leaving
    any curated-release slots intact so the two coexist. Each entry is a dict
    with keys path, name, slot_date, slot_type, and optional release_id /
    position (None for auto-picked singles and legacy EP labels)."""
    now = time.time()
    conn.execute("DELETE FROM schedule WHERE release_id IS NULL")
    conn.executemany(
        "INSERT INTO schedule (path, name, slot_date, slot_type, release_id, position, created_at) "
        "VALUES (:path, :name, :slot_date, :slot_type, :release_id, :position, :created_at)",
        [{"release_id": None, "position": None, **e, "created_at": now} for e in entries],
    )
    conn.commit()


def replace_release_schedule(
    conn: sqlite3.Connection, release_id: int, entries: list[dict]
) -> None:
    """Replace only the slots owned by one release, leaving every other
    release's (and the auto-pick) slots intact. Used by ``plan --release`` so
    scheduling one curated release doesn't wipe the rest of the calendar."""
    now = time.time()
    conn.execute("DELETE FROM schedule WHERE release_id = ?", (release_id,))
    conn.executemany(
        "INSERT INTO schedule (path, name, slot_date, slot_type, release_id, position, created_at) "
        "VALUES (:path, :name, :slot_date, :slot_type, :release_id, :position, :created_at)",
        [{"release_id": None, "position": None, **e, "created_at": now} for e in entries],
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


# --- releases (hand-curated singles/EPs) -----------------------------------

def next_untitled_name(conn: sqlite3.Connection) -> str:
    """Next free 'Untitled N'. Derived from the highest existing N (not the row
    count) so deletes never make the generator reuse a name."""
    hi = 0
    for r in conn.execute("SELECT name FROM releases WHERE name LIKE 'Untitled %'"):
        suffix = r["name"][len("Untitled "):].strip()
        if suffix.isdigit():
            hi = max(hi, int(suffix))
    return f"Untitled {hi + 1}"


def create_release(conn: sqlite3.Connection, name: str, kind: str) -> int:
    now = time.time()
    cur = conn.execute(
        "INSERT INTO releases (name, kind, status, created_at, updated_at) "
        "VALUES (?, ?, 'planning', ?, ?)",
        (name, kind, now, now),
    )
    conn.commit()
    return cur.lastrowid


def list_releases(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT r.*, COUNT(rt.path) AS n_tracks "
        "FROM releases r LEFT JOIN release_tracks rt ON rt.release_id = r.id "
        "GROUP BY r.id ORDER BY r.status, r.created_at"
    ).fetchall()


def get_release(conn: sqlite3.Connection, release_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()


def find_release(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    """Resolve a release by exact id (if numeric) or case-insensitive name
    substring. A numeric query tries the id first but falls through to a name
    match, so a release literally named '2024' stays reachable."""
    if query.isdigit():
        r = get_release(conn, int(query))
        if r:
            return [r]
    like = f"%{query.lower()}%"
    return conn.execute(
        "SELECT * FROM releases WHERE lower(name) LIKE ? ORDER BY name", (like,)
    ).fetchall()


def _touch(conn: sqlite3.Connection, release_id: int) -> None:
    conn.execute("UPDATE releases SET updated_at = ? WHERE id = ?", (time.time(), release_id))


def ordered_paths(conn: sqlite3.Connection, release_id: int) -> list[str]:
    return [
        r["path"]
        for r in conn.execute(
            "SELECT path FROM release_tracks WHERE release_id = ? ORDER BY position",
            (release_id,),
        )
    ]


def set_track_order(conn: sqlite3.Connection, release_id: int, order: list[str]) -> None:
    """Rewrite positions to 1..n in the given path order (contiguous)."""
    for i, path in enumerate(order, 1):
        conn.execute(
            "UPDATE release_tracks SET position = ? WHERE release_id = ? AND path = ?",
            (i, release_id, path),
        )


def add_tracks(
    conn: sqlite3.Connection,
    release_id: int,
    items: list[tuple[str, str]],
    at: int | None = None,
) -> tuple[list[str], list[str]]:
    """Add (path, name) items to a release. Skips paths already present.
    ``at`` inserts at a 1-based position; otherwise appends. Returns
    (added_paths, skipped_paths)."""
    existing = ordered_paths(conn, release_id)
    present = set(existing)
    added, skipped, now = [], [], time.time()
    new_paths = []
    for path, name in items:
        if path in present or path in new_paths:
            skipped.append(path)
            continue
        conn.execute(
            "INSERT INTO release_tracks (release_id, path, position, name_at_add, added_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (release_id, path, name, now),
        )
        new_paths.append(path)
        added.append(path)
    if new_paths:
        idx = len(existing) if at is None else max(0, min(at - 1, len(existing)))
        order = existing[:idx] + new_paths + existing[idx:]
        set_track_order(conn, release_id, order)
        _touch(conn, release_id)
    conn.commit()
    return added, skipped


def remove_track(conn: sqlite3.Connection, release_id: int, path: str) -> None:
    conn.execute(
        "DELETE FROM release_tracks WHERE release_id = ? AND path = ?", (release_id, path)
    )
    set_track_order(conn, release_id, ordered_paths(conn, release_id))  # re-pack
    _touch(conn, release_id)
    conn.commit()


def reorder_release(conn: sqlite3.Connection, release_id: int, order: list[str]) -> None:
    set_track_order(conn, release_id, order)
    _touch(conn, release_id)
    conn.commit()


def rename_release(
    conn: sqlite3.Connection, release_id: int, name: str | None = None, kind: str | None = None
) -> None:
    if name is not None:
        conn.execute("UPDATE releases SET name = ? WHERE id = ?", (name, release_id))
    if kind is not None:
        conn.execute("UPDATE releases SET kind = ? WHERE id = ?", (kind, release_id))
    _touch(conn, release_id)
    conn.commit()


def delete_release(conn: sqlite3.Connection, release_id: int) -> None:
    # Clear any schedule slots this release owns first, so a deleted release
    # leaves no dangling rows that the dashboard would flag overdue forever.
    conn.execute("DELETE FROM schedule WHERE release_id = ?", (release_id,))
    conn.execute("DELETE FROM releases WHERE id = ?", (release_id,))  # tracks cascade
    conn.commit()


def move_track_atomic(
    conn: sqlite3.Connection,
    from_release_id: int,
    to_release_id: int,
    path: str,
    name: str,
    at: int | None = None,
) -> None:
    """Move a track between releases in ONE transaction — an interruption rolls
    back the remove, so the track can never be lost from both releases."""
    with conn:  # commits on success, rolls back on any exception
        conn.execute(
            "DELETE FROM release_tracks WHERE release_id = ? AND path = ?",
            (from_release_id, path),
        )
        set_track_order(conn, from_release_id, ordered_paths(conn, from_release_id))
        existing = ordered_paths(conn, to_release_id)
        conn.execute(
            "INSERT INTO release_tracks (release_id, path, position, name_at_add, added_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (to_release_id, path, name, time.time()),
        )
        idx = len(existing) if at is None else max(0, min(at - 1, len(existing)))
        set_track_order(conn, to_release_id, existing[:idx] + [path] + existing[idx:])
        _touch(conn, from_release_id)
        _touch(conn, to_release_id)


def set_release_status(
    conn: sqlite3.Connection, release_id: int, status: str, target_date: str | None = None
) -> None:
    conn.execute(
        "UPDATE releases SET status = ?, target_date = COALESCE(?, target_date), updated_at = ? "
        "WHERE id = ?",
        (status, target_date, time.time(), release_id),
    )
    conn.commit()


def repath(conn: sqlite3.Connection, old_path: str, new_path: str) -> int:
    """Point every index reference from ``old_path`` to ``new_path`` after a
    project folder is moved/renamed on disk, so the project, its release
    memberships, and any scheduled slot stay linked to it. Path-only — the
    inferred ``stage`` is refreshed by the next ``scan``; this keeps repath
    trivially reversible by swapping the arguments.

    Runs in one transaction. Any stale row already sitting at ``new_path`` (a
    dead/duplicate index entry from a prior scan of that location) is cleared
    first so the projects PK update can't collide. Returns the number of
    ``projects`` rows moved (0 ⇒ the index didn't know ``old_path`` — caller
    should warn and re-scan)."""
    with conn:
        if old_path != new_path:
            conn.execute("DELETE FROM projects WHERE path = ?", (new_path,))
        cur = conn.execute("UPDATE projects SET path = ? WHERE path = ?", (new_path, old_path))
        moved = cur.rowcount
        conn.execute("UPDATE release_tracks SET path = ? WHERE path = ?", (new_path, old_path))
        conn.execute("UPDATE schedule SET path = ? WHERE path = ?", (new_path, old_path))
    return moved


def release_track_paths(conn: sqlite3.Connection) -> set[str]:
    """Every project path that's already a track in *some* release."""
    return {r["path"] for r in conn.execute("SELECT DISTINCT path FROM release_tracks")}


def find_release_tracks(conn: sqlite3.Connection, query: str) -> list[dict]:
    """Resolve a track across ALL releases by name/path substring (for `move`)."""
    like = f"%{query.lower()}%"
    rows = conn.execute(
        "SELECT rt.release_id, r.name AS release_name, rt.path, rt.name_at_add, rt.position "
        "FROM release_tracks rt JOIN releases r ON r.id = rt.release_id "
        "WHERE lower(rt.name_at_add) LIKE ? OR lower(rt.path) LIKE ? "
        "ORDER BY r.name, rt.position",
        (like, like),
    ).fetchall()
    return [dict(r) for r in rows]


def get_release_tracks(conn: sqlite3.Connection, release_id: int) -> list[dict]:
    """Ordered tracks with live project data joined in. A track whose project
    has left the index comes back ``missing=True`` with its snapshot name."""
    rows = conn.execute(
        "SELECT p.*, rt.position AS rt_position, rt.name_at_add AS name_at_add, "
        "       rt.path AS rt_path "
        "FROM release_tracks rt LEFT JOIN projects p ON p.path = rt.path "
        "WHERE rt.release_id = ? ORDER BY rt.position",
        (release_id,),
    ).fetchall()
    out = []
    for r in rows:
        missing = r["path"] is None  # p.path NULL when the project vanished
        out.append(
            {
                "path": r["rt_path"],
                "position": r["rt_position"],
                "name": r["name_at_add"],
                "missing": missing,
                "project": None if missing else _row_to_project(r),
            }
        )
    return out
