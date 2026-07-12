# src/librarian/setplan/store.py
"""Persistence for setplan: plan artifacts (runs/setplan-<id>.json) and the
pool cache (tag reads are the slow part of a 9k-file run).

The cache stores RAW tag strings (bpm/key as tagged), so parser fixes apply on
the next load without invalidating the cache. Writes are atomic (tmp+rename).
Nothing here ever writes inside the library root.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict
from pathlib import Path

from ..metadata import read_meta
from ..paths import audio_files
from .. import camelot
from .pool import Candidate, history_stats, norm, _to_bpm
from .search import SetPlan, Slot
from .spec import GigSpec

SETPLAN_ID_RE = re.compile(r"^sp-[0-9a-f]{8}$")
CACHE_VERSION = 1


def new_plan_id() -> str:
    return "sp-" + uuid.uuid4().hex[:8]


def _cand_dict(c) -> dict:
    return {"path": str(c.path), "artist": c.artist, "title": c.title,
            "genre": c.genre, "bpm": c.bpm,
            "camelot": list(c.camelot) if c.camelot else None,
            "length_s": c.length_s, "plays": c.plays}


def plan_to_dict(plan: SetPlan, plan_id: str) -> dict:
    return {
        "id": plan_id,
        "spec": asdict(plan.spec),
        "unmatched": list(plan.unmatched),
        "slots": [{
            "index": s.index, "clock_min": s.clock_min, "reason": s.reason,
            "candidate": _cand_dict(s.candidate),
            "alternates": [{"reason": a["reason"], "candidate": _cand_dict(a["candidate"])}
                           for a in s.alternates],
        } for s in plan.slots],
    }


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_plan(plan: SetPlan, runs_dir: Path, plan_id: str | None = None) -> str:
    pid = plan_id or new_plan_id()
    if not SETPLAN_ID_RE.fullmatch(pid):
        raise ValueError(f"bad plan id: {pid!r}")
    runs_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(runs_dir / f"setplan-{pid}.json",
                  json.dumps(plan_to_dict(plan, pid), indent=1))
    return pid


def load_plan_dict(plan_id: str, runs_dir: Path) -> dict | None:
    if not SETPLAN_ID_RE.fullmatch(plan_id):
        raise ValueError(f"bad plan id: {plan_id!r}")
    f = runs_dir / f"setplan-{plan_id}.json"
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def spec_from_dict(d: dict) -> GigSpec:
    known = {k: v for k, v in d.items() if k in GigSpec.__dataclass_fields__}
    if known.get("journey"):
        known["journey"] = [tuple(b) for b in known["journey"]]
    if known.get("bpm_range"):
        known["bpm_range"] = tuple(known["bpm_range"])
    return GigSpec(**known)


# --- pool cache -------------------------------------------------------------

def _stat_sig(p: Path) -> list[int]:
    st = p.stat()
    return [st.st_mtime_ns, st.st_size]


def load_pool_cached(root: Path, cache_path: Path,
                     sessions: list[list[str]] | None = None) -> tuple[list[Candidate], dict]:
    """build_pool, but tag reads are served from cache_path when (mtime,size) match."""
    cached: dict = {}
    try:
        blob = json.loads(cache_path.read_text(encoding="utf-8"))
        if blob.get("version") == CACHE_VERSION and blob.get("root") == str(root):
            cached = blob.get("files", {})
    except (OSError, ValueError):
        cached = {}

    plays, positions, follows = history_stats(sessions or [])
    files_out: dict = {}
    cands: list[Candidate] = []
    dirty = False
    for p in audio_files(root):
        rel = str(p.relative_to(root))
        sig = _stat_sig(p)
        entry = cached.get(rel)
        if entry and entry.get("sig") == sig:
            meta = entry["meta"]
        else:
            m = read_meta(p)
            meta = {"artist": m.artist, "title": m.title, "genre": m.genre,
                    "bpm": m.bpm, "key": m.key, "length_s": m.length_s,
                    "low_bitrate": m.low_bitrate}
            dirty = True
        files_out[rel] = {"sig": sig, "meta": meta}
        title = meta["title"] or p.stem
        nk = norm(f"{meta['artist'] or ''} {title}")
        cands.append(Candidate(
            path=p, artist=meta["artist"] or "", title=title,
            genre=(meta["genre"] or "").strip(), bpm=_to_bpm(meta["bpm"]),
            camelot=camelot.parse_key(meta["key"]), length_s=meta["length_s"],
            norm_key=nk, low_bitrate=bool(meta["low_bitrate"]),
            plays=plays.get(nk, 0), positions=positions.get(nk, []),
        ))
    if dirty or set(files_out) != set(cached):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(cache_path, json.dumps(
            {"version": CACHE_VERSION, "root": str(root), "files": files_out}))
    return cands, follows
