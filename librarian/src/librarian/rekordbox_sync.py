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
        by_at[_norm(getattr(c, "Title", "") or "")].append(c)  # title-keyed: stick artists differ
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
        sessions, meta, _fmts = pulse_usb.read_stick(vol)
        for s in sessions:
            for label in s["tracks"]:
                title = meta.get(_norm(label), {}).get("title", "")
                if title:
                    plays[_norm(title)] += 1
    return plays


def _stars(n: int) -> int:
    return 5 if n >= 5 else 4 if n >= 3 else 3 if n >= 2 else 2 if n >= 1 else 0


def auto_rate(db, *, dry_run: bool = True, plays: collections.Counter | None = None,
              backup_to: Path | None = None, rate_all: bool = True) -> dict:
    """Rate every collection track by how often you play it off your sticks
    (>=5 plays=5★, 3-4=4★, 2=3★, 1=2★, unplayed=1★ when rate_all). Matches USB
    plays to the collection by artist+title; combines DL + DL+ history."""
    plays = plays if plays is not None else usb_play_counts()
    if not dry_run:
        _guard(dry_run, backup_to)  # confirm rekordbox closed + back up BEFORE any write
    changes = collections.Counter()
    examples = []
    for c in db.get_content():
        key = _norm(getattr(c, "Title", "") or "")
        n = plays.get(key, 0)
        st = _stars(n)
        if st == 0:
            if not rate_all:
                continue
            st = 1  # every song gets a baseline; unplayed = 1 star
        if getattr(c, "Rating", 0) == st:
            continue
        changes[st] += 1
        if n > 0 and len(examples) < 12:
            examples.append({"track": f"{_artist(c)} - {c.Title}".strip(" -"),
                             "plays": n, "stars": st})
        if not dry_run:
            c.Rating = st
    if not dry_run:
        db.commit()
    return {"by_stars": dict(sorted(changes.items(), reverse=True)),
            "total": sum(changes.values()), "examples": examples}


def usb_track_stats(recent_n: int = 50):
    """Per-title (plays, sets, recently-played) across mounted sticks, DL + DL+."""
    from . import pulse_usb
    plays, sets, recent = collections.Counter(), collections.Counter(), set()
    for vol in pulse_usb.find_usbs():
        sess, meta, _ = pulse_usb.read_stick(vol)
        for s in sess:
            titles = set()
            for label in s["tracks"]:
                ttl = meta.get(_norm(label), {}).get("title", "")
                if ttl:
                    k = _norm(ttl)
                    plays[k] += 1
                    titles.add(k)
            sets.update(titles)
        for s in sess[-recent_n:]:
            for label in s["tracks"]:
                ttl = meta.get(_norm(label), {}).get("title", "")
                if ttl:
                    recent.add(_norm(ttl))
    return plays, sets, recent


def stamp_comments(db, *, dry_run: bool = True, backup_to: Path | None = None) -> dict:
    """Write play stats into each track's Comment ('▶ 13× · 13 sets'), so it shows
    on the CDJ. Matched by title (combined DL + DL+ plays)."""
    plays, sets, _ = usb_track_stats()
    if not dry_run:
        _guard(dry_run, backup_to)
    n, examples = 0, []
    for c in db.get_content():
        k = _norm(getattr(c, "Title", "") or "")
        p = plays.get(k, 0)
        comment = f"▶ {p}× · {sets.get(k, 0)} sets" if p else "▶ never played"
        if (getattr(c, "Commnt", None) or "") == comment:
            continue
        n += 1
        if p and len(examples) < 8:
            examples.append({"track": f"{_artist(c)} - {c.Title}".strip(" -"), "comment": comment})
        if not dry_run:
            c.Commnt = comment
    if not dry_run:
        db.commit()
    return {"updated": n, "examples": examples}


def _color_ids(db):
    from pyrekordbox.db6 import tables
    out = {}
    for col in db.session.query(tables.DjmdColor).all():
        nm = (getattr(col, "Commnt", None) or "").lower()
        if nm:
            out[nm] = col.ID
    return out


def colour_by_status(db, *, dry_run: bool = True, backup_to: Path | None = None,
                     recent_n: int = 50) -> dict:
    """Colour tracks by play status: recently-played = Red, played-but-cold = Blue,
    never-played = Green."""
    plays, _sets, recent = usb_track_stats(recent_n)
    cols = _color_ids(db)
    pick = {"hot": cols.get("red"), "cold": cols.get("blue"), "untouched": cols.get("green")}
    if not dry_run:
        _guard(dry_run, backup_to)
    counts = collections.Counter()
    for c in db.get_content():
        k = _norm(getattr(c, "Title", "") or "")
        status = "untouched" if k not in plays else ("hot" if k in recent else "cold")
        cid = pick[status]
        if cid is None or getattr(c, "ColorID", None) == cid:
            continue
        counts[status] += 1
        if not dry_run:
            c.ColorID = cid
    if not dry_run:
        db.commit()
    return {"by_status": dict(counts), "total": sum(counts.values()),
            "legend": {"hot": "red", "cold": "blue", "untouched": "green"}}


def build_analytics_crates(db, *, dry_run: bool = True, backup_to: Path | None = None,
                           recent_n: int = 50) -> list[dict]:
    """Auto-build rekordbox playlists from your play stats, under a 'pulse crates'
    folder: Floor Fillers (in 5+ sets), Resurface (was a staple, gone cold)."""
    plays, sets, recent = usb_track_stats(recent_n)
    _by_path, by_title = _index(db)
    crates = {
        "★ Floor Fillers": [k for k, n in sets.items() if n >= 5],
        "★ Resurface": [k for k, n in sets.items() if n >= 3 and k not in recent],
    }
    plans = []
    for name, keys in crates.items():
        cids = [c.ID for k in keys for c in by_title.get(k, [])]
        plans.append({"name": name, "tracks": len(cids), "_cids": cids})

    if not dry_run:
        _guard(dry_run, backup_to)
        folder = _get_or_create_folder(db, "pulse crates")
        for p in plans:
            for ex in _find_playlists(db, p["name"]):
                if ex.ID != folder.ID:
                    db.delete_playlist(ex)
            pl = db.create_playlist(p["name"], parent=folder)
            for cid in p["_cids"]:
                db.add_to_playlist(pl, cid)
        db.commit()
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in plans]
