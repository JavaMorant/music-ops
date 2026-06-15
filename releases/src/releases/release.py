"""Engine for hand-curated releases (singles/EPs) — resolution + ergonomics.

A *release* is a named container holding an ordered set of scanned projects
(its *tracks*). This module is the logic layer over ``db``'s release helpers:
fuzzy-resolving a release or a track by query (with the same ambiguity guard as
``status``), bulk-adding the closest-to-done, and moving a track between
releases. It never touches the music library — only the index.
"""

from __future__ import annotations

import sqlite3

from . import db
from .score import rank

VALID_KINDS = {"single", "ep"}


class ReleaseError(Exception):
    """A resolution/usage problem to surface cleanly (no traceback)."""


def resolve_release(conn: sqlite3.Connection, query: str) -> sqlite3.Row:
    """Resolve a release by id or unique name substring, else raise."""
    matches = db.find_release(conn, query)
    if not matches:
        raise ReleaseError(f"No release matches {query!r}.  (releases release ls)")
    if len(matches) > 1:
        lines = "\n".join(f"  #{m['id']}  {m['name']}" for m in matches[:12])
        raise ReleaseError(f"{query!r} is ambiguous — {len(matches)} releases:\n{lines}")
    return matches[0]


def top_candidates(conn: sqlite3.Connection, n: int) -> list[tuple[str, str]]:
    """The n closest-to-done projects not already a track in *any* release."""
    taken = db.release_track_paths(conn)
    pool = [
        p for p in db.all_projects(conn)
        if p.effective_stage != "released" and p.path not in taken
    ]
    return [(s.project.path, s.project.name) for s in rank(pool)[:n]]


def collect_add_items(
    conn: sqlite3.Connection, queries: list[str], top: int | None
) -> tuple[list[tuple[str, str]], list[str]]:
    """Resolve what to add: ``top`` bulk-adds the closest-to-done, then each
    query resolves one project. A bad query is reported as a problem and skipped
    — a typo never aborts the whole batch. Returns (items, problems)."""
    items: list[tuple[str, str]] = []
    problems: list[str] = []
    seen: set[str] = set()

    def _take(path: str, name: str) -> None:
        if path not in seen:
            seen.add(path)
            items.append((path, name))

    if top is not None:
        for path, name in top_candidates(conn, top):
            _take(path, name)
    for q in queries:
        matches = db.find_projects(conn, q)
        if not matches:
            problems.append(f"no project matches {q!r}")
        elif len(matches) > 1:
            cand = ", ".join(m.name for m in matches[:6])
            problems.append(f"{q!r} is ambiguous ({len(matches)}): {cand} …")
        else:
            _take(matches[0].path, matches[0].name)
    return items, problems


def move_track(
    conn: sqlite3.Connection, track_query: str, to_query: str, at: int | None
) -> tuple[str, str, str]:
    """Move a track out of whatever release holds it into ``to_query``.
    Returns (track_name, from_release_name, to_release_name)."""
    to = resolve_release(conn, to_query)
    found = db.find_release_tracks(conn, track_query)
    if not found:
        raise ReleaseError(f"No release track matches {track_query!r}.")
    distinct = {(f["release_id"], f["path"]) for f in found}
    if len(distinct) > 1:
        lines = "\n".join(f"  {f['name_at_add']}  (in {f['release_name']})" for f in found[:12])
        raise ReleaseError(f"{track_query!r} is ambiguous:\n{lines}")
    f = found[0]
    from_rid, path, name = f["release_id"], f["path"], f["name_at_add"]
    if from_rid == to["id"]:
        raise ReleaseError(f"{name!r} is already in {to['name']!r}.")
    db.move_track_atomic(conn, from_rid, to["id"], path, name, at=at)
    return name, f["release_name"], to["name"]


def reorder(conn: sqlite3.Connection, release_id: int, queries: list[str]) -> None:
    """Reorder a release's tracks front-to-back by query; unnamed tracks keep
    their relative order after the listed ones."""
    current = db.ordered_paths(conn, release_id)
    tracks = db.get_release_tracks(conn, release_id)
    chosen: list[str] = []
    for q in queries:
        ql = q.lower()
        m = [
            t for t in tracks
            if (ql in t["name"].lower() or ql in t["path"].lower()) and t["path"] not in chosen
        ]
        if not m:
            raise ReleaseError(f"no track in this release matches {q!r}")
        if len(m) > 1:
            raise ReleaseError(f"{q!r} is ambiguous within this release")
        chosen.append(m[0]["path"])
    rest = [p for p in current if p not in chosen]
    db.reorder_release(conn, release_id, chosen + rest)
