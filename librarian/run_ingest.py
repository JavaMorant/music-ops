"""librarian ingest — fold a drop-folder of new tracks into the organized library.

The repeatable 'update' pipeline: classify -> acoustic-dedupe (within the batch
AND against the library) -> file into the flat genre folders (genre tag written,
filename kept) -> refresh the .m3u crates + genre playlists.

  python run_ingest.py [DROP]          # DRY RUN
  APPLY=1 python run_ingest.py [DROP]   # do it   (DROP defaults to inbox/_from-usb)
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "src")
from librarian import ai, paths as P, tags as TG  # noqa: E402
from run_flatten import FLAT  # noqa: E402
from run_move import REMAP  # noqa: E402

HOME = Path.home()
LIB = HOME / "DJ" / "library"
QUAR = HOME / "DJ" / "_quarantine"
PLAYLISTS = LIB / "_Playlists"
GENRES = PLAYLISTS  # crates + genre playlists kept in one folder
FP_LIB = "/tmp/lib-dedupe/fingerprints.jsonl"
DEDUPE = "/tmp/lib-dedupe/dedupe-plan.json"
AUDIO = {".mp3", ".m4a", ".flac", ".wav", ".aiff", ".ogg", ".aac", ".opus"}


def fpof(path):
    try:
        d = json.loads(subprocess.run(["fpcalc", "-raw", "-json", "-length", "120", str(path)],
                                      capture_output=True, text=True, timeout=90).stdout)
        return d.get("duration", 0.0), d.get("fingerprint", [])
    except Exception:
        return 0.0, []


def ber(a, b):
    n = min(len(a), len(b))
    if n < 25:
        return 1.0
    return sum((a[i] ^ b[i]).bit_count() for i in range(n)) / (32.0 * n)


def main():
    apply = os.environ.get("APPLY") == "1"
    drop = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
        else HOME / "DJ" / "inbox" / "_from-usb"
    files = sorted(p for p in drop.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO
                   and "_quarantine" not in p.parts)
    print(f"=== INGEST {drop} ({'APPLY' if apply else 'dry run'}) ===")
    print(f"drop files: {len(files)}", flush=True)
    if not files:
        return

    # 1) classify
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
        except Exception as exc:
            print(f"  classify batch failed: {exc}", flush=True)

    # 2) fingerprint the batch
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        fps = dict(zip(files, ex.map(fpof, files)))

    # library keeper fingerprints (bucketed) for vs-library dedupe
    quar = {q["path"] for q in json.load(open(DEDUPE))["quarantine"]} if Path(DEDUPE).exists() else set()
    kb = collections.defaultdict(list)
    if Path(FP_LIB).exists():
        for line in open(FP_LIB):
            r = json.loads(line)
            if r["p"] not in quar and r["fp"]:
                kb[int(round(r["d"]))].append((r["d"], r["fp"]))

    def in_library(dur, f):
        if not f:
            return False
        for sec in range(int(dur - 2), int(dur + 3)):
            for kd, kf in kb.get(sec, ()):
                if abs(kd - dur) <= 2 and ber(f, kf) < 0.25:
                    return True
        return False

    # 3) dedupe: drop library-dupes, then within-batch (keep first/larger)
    survivors, dupes = [], []
    seen = []  # (dur, fp, path)
    for p in files:
        dur, f = fps[p]
        if in_library(dur, f):
            dupes.append((p, "already in library"))
            continue
        match = next((sp for (sd, sf, sp) in seen if f and sf and abs(sd - dur) <= 2 and ber(f, sf) < 0.25), None)
        if match:
            dupes.append((p, f"dupe of {match.name}"))
            continue
        seen.append((dur, f, p))
        survivors.append(p)
    print(f"survivors: {len(survivors)} · dupes (library/batch): {len(dupes)}", flush=True)

    # plan destinations
    plan = []
    reserved = set()
    affected = set()
    for p in survivors:
        leaf, crates = cls.get(p, ("Other", []))
        flat = FLAT.get(leaf, "Other")
        dest = P.collision_free(LIB / flat / p.name, reserved)
        reserved.add(P.norm_key(dest))
        plan.append((p, dest, leaf, crates, flat))
        affected.add(flat)
    by_genre = collections.Counter(flat for *_, flat in plan)
    crate_adds = collections.Counter(c for *_, crates, _ in plan for c in crates)
    print("\n--- would file into ---")
    for g, n in by_genre.most_common():
        print(f"  {n:4d}  {g}")
    print("--- would add to crates ---")
    for c, n in crate_adds.most_common():
        print(f"  {n:4d}  {c}")

    if not apply:
        print("\n(dry run — APPLY=1 to execute)")
        return

    for p, dest, leaf, crates, flat in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if TG.is_taggable(p):
            try:
                TG.write_tags(p, {"genre": leaf})
            except TG.TagError:
                pass
        os.rename(p, dest)
    QUAR.mkdir(parents=True, exist_ok=True)
    for p, _ in dupes:
        if p.exists():
            d = P.collision_free(QUAR / p.name, reserved)
            reserved.add(P.norm_key(d))
            os.rename(p, d)
    # crates: append new members
    crate_members = collections.defaultdict(list)
    for p, dest, leaf, crates, flat in plan:
        for c in crates:
            crate_members[c].append(str(dest))
    PLAYLISTS.mkdir(parents=True, exist_ok=True)
    for c, adds in crate_members.items():
        m3u = PLAYLISTS / f"{c}.m3u"
        existing = [ln for ln in (m3u.read_text().splitlines() if m3u.exists() else []) if ln and not ln.startswith("#")]
        m3u.write_text("#EXTM3U\n" + "\n".join(sorted(set(existing + adds))) + "\n", encoding="utf-8")
    # regenerate affected genre playlists
    GENRES.mkdir(parents=True, exist_ok=True)
    for flat in affected:
        d = LIB / flat
        paths = sorted(str(x) for x in d.rglob("*") if x.is_file() and x.suffix.lower() in AUDIO)
        (GENRES / f"{flat}.m3u").write_text("#EXTM3U\n" + "\n".join(paths) + "\n", encoding="utf-8")
    print(f"\nfiled {len(plan)} · quarantined {len(dupes)} · "
          f"updated {len(crate_members)} crates + {len(affected)} genre playlists")


if __name__ == "__main__":
    main()
