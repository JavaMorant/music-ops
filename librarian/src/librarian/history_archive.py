"""Persistent per-stick play-history archive — survives USB formats / re-exports.

The CDJ writes history onto the stick; a format wipes it. So pulse snapshots a
stick's history to the laptop every time it reads it, keyed by a content hash so
re-snapshotting never duplicates. Resets become safe and history accumulates
across every stick the user ever uses.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

DIR = Path.home() / "DJ" / ".librarian" / "history-archive"


def sig(tracks) -> str:
    norm = "\x1f".join(re.sub(r"[^a-z0-9]", "", (t or "").lower()) for t in tracks)
    return hashlib.md5(norm.encode()).hexdigest()


def load(stick: str) -> dict:
    f = DIR / f"{stick}.json"
    if f.exists():
        try:
            d = json.loads(f.read_text())
            return {"sessions": d.get("sessions", []), "meta": d.get("meta", {})}
        except Exception:
            pass
    return {"sessions": [], "meta": {}}


def snapshot(stick: str, sessions: list[dict], meta: dict) -> int:
    """Add any sessions not already archived (by tracklist content); merge meta.
    Returns the number of new sessions archived."""
    arch = load(stick)
    seen = {sig(s["tracks"]) for s in arch["sessions"]}
    added = 0
    for s in sessions:
        if s.get("tracks") and sig(s["tracks"]) not in seen:
            arch["sessions"].append(s)
            seen.add(sig(s["tracks"]))
            added += 1
    for k, v in meta.items():
        arch["meta"].setdefault(k, v)
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{stick}.json").write_text(json.dumps(arch, ensure_ascii=False))
    return added
