"""rekordbox_sync — write pulse's work back into rekordbox (master.db) directly:
seamlessly sync the .m3u crates/genres into rekordbox playlists, and auto-rate
tracks by how often you actually play them off your sticks.

SAFETY: rekordbox MUST be closed. pyrekordbox writes the live SQLite DB, and a
running rekordbox will conflict and can corrupt it. Every apply path refuses to
run while rekordbox is open and backs the DB up first. dry_run (default) only
reads + plans.
"""
from __future__ import annotations

import collections
import re
import shutil
import subprocess
from pathlib import Path

RB_DIR = Path.home() / "Library" / "Pioneer" / "rekordbox"


class RekordboxOpenError(RuntimeError):
    pass


def rekordbox_running() -> bool:
    r = subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def backup_db(dst: Path) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for f in RB_DIR.glob("master.db*"):
        shutil.copy2(f, dst / f.name)
    return dst


def _guard(dry_run: bool, backup_to: Path | None):
    if dry_run:
        return
    if rekordbox_running():
        raise RekordboxOpenError("rekordbox is running — quit it before writing to the database")
    backup_db(backup_to or (Path.home() / "DJ" / "_rb-db-backup"))


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _artist(c):
    return getattr(c, "ArtistName", None) or (c.Artist.Name if getattr(c, "Artist", None) else "") or ""


def _index(db):
    by_path, by_at = {}, collections.defaultdict(list)
    for c in db.get_content():
        fp = getattr(c, "FolderPath", None)
        if fp:
            by_path[fp] = c
        by_at[_norm(_artist(c) + " " + (getattr(c, "Title", "") or ""))].append(c)
    return by_path, by_at


def _find_playlists(db, name):
    from pyrekordbox.db6 import tables
    return db.session.query(tables.DjmdPlaylist).filter(tables.DjmdPlaylist.Name == name).all()


def _get_or_create_folder(db, name):
    existing = [p for p in _find_playlists(db, name)]
    if existing:
        return existing[0]
    return db.create_playlist_folder(name)


def sync_playlists(db, playlists_dir: Path, *, dry_run: bool = True,
                   folder_name: str = "DJ Library", backup_to: Path | None = None) -> list[dict]:
    """Mirror every .m3u in ``playlists_dir`` into a rekordbox playlist (under one
    folder), matched to the collection by file path. Idempotent — re-running
    replaces the playlists, so it stays in sync."""
    by_path, _ = _index(db)
    plans = []
    for m3u in sorted(playlists_dir.glob("*.m3u")):
        paths = [ln.strip() for ln in m3u.read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.startswith("#")]
        cids = [by_path[p].ID for p in paths if p in by_path]
        plans.append({"name": m3u.stem, "tracks": len(paths), "matched": len(cids), "_cids": cids})

    if not dry_run:
        _guard(dry_run, backup_to)
        folder = _get_or_create_folder(db, folder_name)
        for p in plans:
            for ex in _find_playlists(db, p["name"]):
                if ex.ID != folder.ID:
                    db.delete_playlist(ex)
            pl = db.create_playlist(p["name"], parent=folder)
            for cid in p["_cids"]:
                db.add_to_playlist(pl, cid)
        db.commit()
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in plans]


def usb_play_counts() -> collections.Counter:
    """Plays per normalised artist+title across mounted sticks — combining BOTH
    Device Library and Device Library Plus (Opus) history."""
    from . import pulse_usb
    plays = collections.Counter()
    for vol in pulse_usb.find_usbs():
        sessions, _meta, _fmts = pulse_usb.read_stick(vol)
        for s in sessions:
            for label in s:
                plays[_norm(label)] += 1
    return plays


def _stars(n: int) -> int:
    return 5 if n >= 5 else 4 if n >= 3 else 3 if n >= 2 else 2 if n >= 1 else 0


def auto_rate(db, *, dry_run: bool = True, plays: collections.Counter | None = None,
              backup_to: Path | None = None) -> dict:
    """Rate collection tracks by how often you play them off your sticks
    (>=5 plays=5★, 3-4=4★, 2=3★, 1=2★). Matches USB plays to the collection by
    artist+title. Leaves never-played tracks untouched."""
    plays = plays if plays is not None else usb_play_counts()
    _, by_at = _index(db)
    if not dry_run:
        _guard(dry_run, backup_to)  # confirm rekordbox closed + back up BEFORE any write
    changes = collections.Counter()
    examples = []
    for key, n in plays.items():
        if n < 1 or key == "|":
            continue
        st = _stars(n)
        for c in by_at.get(key, []):
            if getattr(c, "Rating", 0) == st:
                continue
            changes[st] += 1
            if len(examples) < 12:
                examples.append({"track": f"{_artist(c)} - {c.Title}".strip(" -"),
                                 "plays": n, "stars": st})
            if not dry_run:
                c.Rating = st
    if not dry_run:
        db.commit()
    return {"by_stars": dict(sorted(changes.items(), reverse=True)),
            "total": sum(changes.values()), "examples": examples}
