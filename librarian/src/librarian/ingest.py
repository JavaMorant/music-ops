"""Incremental ingest — fold a drop folder of new tracks into the organized,
flat-genre library. The package home of the `run_ingest.py` pipeline, callable
from the CLI and the pulse web GUI.

  plan = plan_ingest(drop, library_root)   # classify + dedupe, moves nothing
  apply_ingest(library_root, plan)          # files survivors + refreshes crates
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import json
import os
import subprocess
from pathlib import Path

from . import ai, paths as P, tags as TG

AUDIO = {".mp3", ".m4a", ".flac", ".wav", ".aiff", ".ogg", ".aac", ".opus"}
FP_LIB = "/tmp/lib-dedupe/fingerprints.jsonl"   # library keeper fingerprints (best-effort backstop)
DEDUPE = "/tmp/lib-dedupe/dedupe-plan.json"

REMAP = {"Trance": "EDM / Big Room", "Bass / Dubstep": "Drum & Bass"}
FLAT = {
    "US Rap (Modern)": "Rap - US Modern", "US Rap (Throwback)": "Rap - US Throwback",
    "UK Drill & New Rap": "Rap - UK Drill", "Afro Swing": "Rap - Afro Swing",
    "Grime & UK Classics": "Rap - Grime & UK", "Trap (Melodic)": "Rap - Trap Melodic",
    "Trap (Hard/Rage)": "Rap - Trap Hard", "Rap (Other)": "Rap - Other",
    "EDM / Big Room": "Dance - EDM", "Tech House / Techno": "Dance - Tech House",
    "Drum & Bass": "Dance - DnB",
    "Pop (2015+)": "Pop - 2015+", "Pop (pre-2015)": "Pop - pre2015",
    "R&B (Modern/Alt)": "R&B - Modern", "R&B (Throwback)": "R&B - Throwback",
    "Edits (House)": "Edits - House", "Edits (Brazilian/Phonk)": "Edits - Brazilian Phonk",
    "Edits (Pop/Throwback)": "Edits - Pop Throwback", "Edits (Jersey/Club)": "Edits - Jersey Club",
    "Edits (Afro/Amapiano)": "Edits - Afro Amapiano",
    "House": "House", "Latin & Brazilian": "Latin & Brazilian", "Other": "Other", "K-Pop": "K-Pop",
    "Afrobeats": "Afrobeats", "UK Garage": "UK Garage", "Dancehall": "Dancehall",
    "Funk & Disco": "Funk & Disco", "Afro House": "Afro House", "Amapiano": "Amapiano",
}


def _fpof(path):
    try:
        d = json.loads(subprocess.run(["fpcalc", "-raw", "-json", "-length", "120", str(path)],
                                      capture_output=True, text=True, timeout=90).stdout)
        return d.get("duration", 0.0), d.get("fingerprint", [])
    except Exception:
        return 0.0, []


def _ber(a, b):
    n = min(len(a), len(b))
    if n < 25:
        return 1.0
    return sum((a[i] ^ b[i]).bit_count() for i in range(n)) / (32.0 * n)


def scan(drop: Path):
    return sorted(p for p in drop.rglob("*") if p.is_file()
                  and p.suffix.lower() in AUDIO and "_quarantine" not in p.parts)


def _lib_buckets():
    quar = {q["path"] for q in json.load(open(DEDUPE))["quarantine"]} if Path(DEDUPE).exists() else set()
    kb = collections.defaultdict(list)
    if Path(FP_LIB).exists():
        for line in open(FP_LIB):
            r = json.loads(line)
            if r["p"] not in quar and r["fp"]:
                kb[int(round(r["d"]))].append((r["d"], r["fp"]))
    return kb


def plan_ingest(drop: Path, library_root: Path) -> dict:
    """Classify + acoustic-dedupe the drop folder. Moves nothing."""
    files = scan(drop)
    if not files:
        return {"drop": str(drop), "total": 0, "survivors": [], "dupes": [],
                "by_genre": {}, "by_crate": {}}

    metas = []
    for i, p in enumerate(files):
        try:
            t = TG.read_tags(p, ["artist", "title"])
        except Exception:
            t = {}
        metas.append({"index": i, "filename": p.name, "artist": t.get("artist") or "",
                      "title": t.get("title") or "", "genre": ""})
    cls = {}
    for b in [metas[i:i + 60] for i in range(0, len(metas), 60)]:
        try:
            for s in ai.classify_tracks(b):
                cls[files[s.index]] = (REMAP.get(s.genre, s.genre), list(s.crates))
        except Exception:
            pass

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        fps = dict(zip(files, ex.map(_fpof, files)))
    kb = _lib_buckets()

    def in_library(dur, f):
        if not f:
            return False
        for sec in range(int(dur - 2), int(dur + 3)):
            for kd, kf in kb.get(sec, ()):
                if abs(kd - dur) <= 2 and _ber(f, kf) < 0.25:
                    return True
        return False

    survivors, dupes, seen, reserved = [], [], [], set()
    for p in files:
        dur, f = fps[p]
        if in_library(dur, f):
            dupes.append({"src": str(p), "reason": "already in library"})
            continue
        if any(sf and f and abs(sd - dur) <= 2 and _ber(f, sf) < 0.25 for sd, sf, _ in seen):
            dupes.append({"src": str(p), "reason": "duplicate in this batch"})
            continue
        seen.append((dur, f, p))
        leaf, crates = cls.get(p, ("Other", []))
        flat = FLAT.get(leaf, "Other")
        dest = P.collision_free(library_root / flat / p.name, reserved)
        reserved.add(P.norm_key(dest))
        survivors.append({"src": str(p), "dest": str(dest), "genre": flat,
                          "leaf": leaf, "crates": crates})

    by_genre = collections.Counter(s["genre"] for s in survivors)
    by_crate = collections.Counter(c for s in survivors for c in s["crates"])
    return {"drop": str(drop), "total": len(files), "survivors": survivors, "dupes": dupes,
            "by_genre": dict(by_genre.most_common()), "by_crate": dict(by_crate.most_common())}


def apply_ingest(library_root: Path, plan: dict) -> dict:
    """Execute a plan from plan_ingest: file survivors, quarantine dupes, refresh crates."""
    quar_dir = library_root.parent / "_quarantine"
    playlists = library_root / "_Playlists"
    genres = library_root / "_Genres"
    reserved, affected = set(), set()
    crate_adds = collections.defaultdict(list)

    for s in plan["survivors"]:
        src, dest = Path(s["src"]), Path(s["dest"])
        if not src.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if TG.is_taggable(src):
            try:
                TG.write_tags(src, {"genre": s["leaf"]})
            except TG.TagError:
                pass
        os.rename(src, dest)
        affected.add(s["genre"])
        for c in s["crates"]:
            crate_adds[c].append(str(dest))
    quar_dir.mkdir(parents=True, exist_ok=True)
    for d in plan["dupes"]:
        p = Path(d["src"])
        if p.exists():
            dst = P.collision_free(quar_dir / p.name, reserved)
            reserved.add(P.norm_key(dst))
            os.rename(p, dst)

    playlists.mkdir(parents=True, exist_ok=True)
    for c, adds in crate_adds.items():
        m3u = playlists / f"{c}.m3u"
        existing = [ln for ln in (m3u.read_text().splitlines() if m3u.exists() else [])
                    if ln and not ln.startswith("#")]
        m3u.write_text("#EXTM3U\n" + "\n".join(sorted(set(existing + adds))) + "\n", encoding="utf-8")
    genres.mkdir(parents=True, exist_ok=True)
    for flat in affected:
        d = library_root / flat
        paths = sorted(str(x) for x in d.rglob("*") if x.is_file() and x.suffix.lower() in AUDIO)
        (genres / f"{flat}.m3u").write_text("#EXTM3U\n" + "\n".join(paths) + "\n", encoding="utf-8")

    return {"filed": len(plan["survivors"]), "quarantined": len(plan["dupes"]),
            "crates_updated": sorted(crate_adds), "genres_updated": sorted(affected)}
