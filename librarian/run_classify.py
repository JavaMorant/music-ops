"""DRY-RUN refined classifier over the real DJ library.

Reuses the clean artist/title already produced in /tmp/lib-djinbox/full-plan.json,
asks Claude for one leaf genre + crate flags per track (ai.classify_tracks),
runs batches concurrently, and writes a reviewable plan. Touches NO audio files.

  LIMIT=120 python run_classify.py     # sample (validate before the full spend)
  python run_classify.py               # full library
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from librarian import ai  # noqa: E402
from librarian import tags as T  # noqa: E402

INBOX = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "DJ" / "inbox")))
PLAN_IN = os.environ.get("PLAN_IN", "/tmp/lib-djinbox/full-plan.json")  # clean tags to reuse (optional)
PLAN_OUT = os.environ.get("PLAN_OUT", "/tmp/lib-djinbox/classify-plan.json")
AUDIO = {".mp3", ".m4a", ".mp4", ".flac", ".wav", ".aiff", ".ogg", ".aac", ".opus"}
BATCH = 60
WORKERS = 6

PARENT = {}
for g in ("UK Drill & New Rap", "Afro Swing", "Grime & UK Classics", "Trap (Melodic)",
          "Trap (Hard/Rage)", "US Rap (Throwback)", "US Rap (Modern)", "Rap (Other)"):
    PARENT[g] = "RAP & HIP-HOP"
for g in ("EDM / Big Room", "Tech House / Techno", "Drum & Bass", "Bass / Dubstep", "Trance"):
    PARENT[g] = "DANCE & ELECTRONIC"
for g in ("Edits (House)", "Edits (Afro/Amapiano)", "Edits (Jersey/Club)",
          "Edits (Brazilian/Phonk)", "Edits (Pop/Throwback)"):
    PARENT[g] = "EDITS & REMIXES"
PARENT["Pop (pre-2015)"] = PARENT["Pop (2015+)"] = "POP"
PARENT["R&B (Throwback)"] = PARENT["R&B (Modern/Alt)"] = "R&B"


def best_meta(path: Path, clean: dict) -> dict:
    c = clean.get(str(path.absolute()))
    if c is not None:
        return c
    try:
        t = T.read_tags(path, ["artist", "title", "genre"])
    except Exception:
        t = {}
    return {"artist": t.get("artist") or "", "title": t.get("title") or "",
            "genre": t.get("genre") or ""}


def main() -> None:
    clean = {}
    if PLAN_IN and os.path.exists(PLAN_IN):
        for e in json.load(open(PLAN_IN))["tag_edits"]:
            f = e["fields"]
            clean[e["path"]] = {"artist": f.get("artist", ""), "title": f.get("title", ""),
                                "genre": f.get("genre", "")}

    files = sorted(p for p in INBOX.rglob("*")
                   if p.is_file() and "_quarantine" not in p.parts
                   and p.suffix.lower() in AUDIO)
    limit = int(os.environ.get("LIMIT", "0"))
    if limit:
        files = files[:limit]

    # Resume: carry forward anything already classified in a prior PLAN_OUT so a
    # re-run after a credit top-up only pays for the files still missing.
    prior = {}
    if os.path.exists(PLAN_OUT):
        for it in json.load(open(PLAN_OUT)):
            if it.get("genre_leaf"):
                prior[it["path"]] = it

    cands, carried = [], []
    for i, p in enumerate(files):
        ap = str(p.absolute())
        if ap in prior:
            carried.append(prior[ap])
            continue
        m = best_meta(p, clean)
        cands.append({"index": i, "path": ap, "filename": p.name, **m})
    print(f"{len(files)} active files · {len(carried)} already done (resumed) · "
          f"{len(cands)} to classify "
          f"({sum(1 for c in cands if c['path'] in clean)} with clean tags reused)", flush=True)

    batches = [cands[i:i + BATCH] for i in range(0, len(cands), BATCH)]
    results: dict[int, ai.ClassifySuggestion] = {}
    lock = threading.Lock()
    state = {"done": 0, "fail": 0}

    def work(batch):
        payload = [{"index": c["index"], "filename": c["filename"], "artist": c["artist"],
                    "title": c["title"], "genre": c["genre"]} for c in batch]
        try:
            sugg = ai.classify_tracks(payload)
        except Exception as exc:
            with lock:
                state["fail"] += 1
                print(f"  ! batch @{batch[0]['index']} failed: {exc}", flush=True)
            return
        with lock:
            for s in sugg:
                results[s.index] = s
            state["done"] += 1
            print(f"  {state['done']}/{len(batches)} batches · {len(results)} classified", flush=True)

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(work, batches))

    items = list(carried)
    for c in cands:
        s = results.get(c["index"])
        if s is None:
            items.append({**{k: c[k] for k in ("path", "filename", "artist", "title")},
                          "genre_leaf": None, "crates": [], "confidence": None, "note": "no result"})
        else:
            items.append({"path": c["path"], "filename": c["filename"], "artist": c["artist"],
                          "title": c["title"], "genre_leaf": s.genre, "crates": list(s.crates),
                          "confidence": s.confidence, "note": s.note})
    os.makedirs(os.path.dirname(PLAN_OUT), exist_ok=True)
    json.dump(items, open(PLAN_OUT, "w"), ensure_ascii=False)

    by_genre, by_crate = collections.Counter(), collections.Counter()
    low = unclassified = 0
    for it in items:
        if not it.get("genre_leaf"):
            unclassified += 1
            continue
        by_genre[it["genre_leaf"]] += 1
        for cr in it.get("crates", []):
            by_crate[cr] += 1
        if it.get("confidence") == "low":
            low += 1

    print(f"\n=== classified {len(results)} · low-confidence {low} · "
          f"unclassified {unclassified} · failed-batches {state['fail']} ===")
    print("\n--- GENRE FOLDERS ---")
    groups = collections.defaultdict(list)
    for g, n in by_genre.items():
        groups[PARENT.get(g, g.upper())].append((n, g))
    order = ["RAP & HIP-HOP", "DANCE & ELECTRONIC", "POP", "R&B", "EDITS & REMIXES"]
    seen = set()
    for parent in order + sorted(k for k in groups if k not in order):
        if parent in seen:
            continue
        seen.add(parent)
        subs = sorted(groups[parent], reverse=True)
        tot = sum(n for n, _ in subs)
        if len(subs) == 1 and subs[0][1].upper() == parent:
            print(f"  {tot:5d}  {subs[0][1]}")
        else:
            print(f"  {tot:5d}  {parent}")
            for n, g in subs:
                print(f"         {n:5d}  └ {g}")
    print("\n--- CRATES (AI vote; play-history adds more later) ---")
    for cr, n in by_crate.most_common():
        print(f"  {n:5d}  {cr}")
    print(f"\nplan written: {PLAN_OUT}")


if __name__ == "__main__":
    main()
