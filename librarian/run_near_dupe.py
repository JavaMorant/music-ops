"""DRY-RUN fuzzy near-dupe finder across both library roots (v2).

Tag-based keys fail when copies have inconsistent artist/title (''/'Meek Mill'/
'Meek Mill, Drake' for the same song). So we dedupe on what IS consistent:
  - duration (same recording => same length, within a tolerance),
  - the bag of significant tokens from artist + title + filename (robust to
    word-order and feat/ft noise), and
  - a version marker set (dirty/clean/intro/extended/remix/[bass]/[drums]...),
    which keeps genuine variants SEPARATE (the user wants those).
Two files merge only if same version-set, within the duration tolerance, and
their token sets are subset/strongly-overlapping. Keeper = best quality; the
rest are proposed for quarantine. Writes a plan — MOVES NOTHING.
"""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

from mutagen import File as MFile

PLANS = ["/tmp/lib-djinbox/classify-plan.json", "/tmp/lib-musicnew/classify-plan.json"]
PLAN_OUT = "/tmp/lib-dedupe/dedupe-plan.json"
LOSSLESS = {".flac", ".wav", ".aiff", ".aif", ".alac", ".ape"}
DUR_TOL = 2.0  # seconds

STOP = {"feat", "ft", "featuring", "the", "a", "an", "x", "vs", "versus", "prod", "by",
        "official", "video", "audio", "lyrics", "lyric", "hd", "hq", "full", "mp3", "wav",
        "m4a", "with", "and", "of", "to", "in", "on", "org", "spotdown", "www", "com",
        "free", "download", "dl"}
VERSION = {"dirty", "clean", "intro", "outro", "extended", "instrumental", "acapella",
           "acappella", "radio", "edit", "vip", "bootleg", "slowed", "sped", "reverb",
           "remix", "flip", "mashup", "bass", "drums", "vocals", "other", "live",
           "refresh", "refreshed", "short", "snippet", "loop", "transition"}


def tokens(text: str):
    toks = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1 and not t.isdigit()]
    core = frozenset(t for t in toks if t not in STOP and t not in VERSION)
    ver = frozenset(t for t in toks if t in VERSION)
    return core, ver


def media_info(path: str):
    try:
        f = MFile(path)
        info = getattr(f, "info", None)
        return int(getattr(info, "bitrate", 0) or 0), float(getattr(info, "length", 0.0) or 0.0)
    except Exception:
        return 0, 0.0


def quality_score(r: dict):
    p = r["path"].lower()
    junk = ("_spotdown" in p) + bool(re.search(r"\(\d+\)\.[a-z0-9]+$", p))
    return (r["lossless"], r["bitrate"], r["size"], -junk, -len(r["path"]))


def similar(a: dict, b: dict) -> bool:
    if a["ver"] != b["ver"]:
        return False
    ca, cb = a["core"], b["core"]
    if not ca or not cb:
        return Path(a["path"]).stem.lower() == Path(b["path"]).stem.lower()
    small, big = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    if len(small) >= 2 and small <= big:
        return True
    inter, union = len(ca & cb), len(ca | cb)
    return union > 0 and inter / union >= 0.6


def main() -> None:
    recs, seen = [], set()
    for plan in PLANS:
        if not os.path.exists(plan):
            continue
        for it in json.load(open(plan)):
            p = it["path"]
            if p in seen or not os.path.exists(p):
                continue
            seen.add(p)
            br, dur = media_info(p)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            core, ver = tokens(f"{it.get('artist', '')} {it.get('title', '')} {Path(p).stem}")
            recs.append({"path": p, "bitrate": br, "dur": dur, "size": size,
                         "lossless": Path(p).suffix.lower() in LOSSLESS,
                         "core": core, "ver": ver,
                         "root": "inbox" if "/DJ/inbox/" in p else "new"})
    print(f"read {len(recs)} files", flush=True)

    # union-find over a duration-sorted sliding window
    recs.sort(key=lambda r: r["dur"])
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    n = len(recs)
    for i in range(n):
        di = recs[i]["dur"]
        j = i + 1
        while j < n and recs[j]["dur"] - di <= DUR_TOL:
            if find(i) != find(j) and similar(recs[i], recs[j]):
                parent[find(i)] = find(j)
            j += 1
    print("clustered", flush=True)

    clusters = collections.defaultdict(list)
    for i, r in enumerate(recs):
        clusters[find(i)].append(r)

    quarantine, keepers = [], []
    dup_groups = cross_root = reclaim = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        dup_groups += 1
        members.sort(key=quality_score, reverse=True)
        keeper, dupes = members[0], members[1:]
        keepers.append(keeper["path"])
        if len({m["root"] for m in members}) > 1:
            cross_root += 1
        for d in dupes:
            reclaim += d["size"]
            quarantine.append({"path": d["path"], "keeper": keeper["path"],
                               "dup_kbps": d["bitrate"] // 1000, "keeper_kbps": keeper["bitrate"] // 1000,
                               "reason": f"near-dupe of {keeper['bitrate'] // 1000}kbps copy"})

    os.makedirs(os.path.dirname(PLAN_OUT), exist_ok=True)
    json.dump({"keepers": keepers, "quarantine": quarantine}, open(PLAN_OUT, "w"), ensure_ascii=False)

    by_root = collections.Counter("inbox" if "/DJ/inbox/" in q["path"] else "new" for q in quarantine)
    print(f"\n=== NEAR-DUPE DRY RUN (v2) ===")
    print(f"  files scanned:       {len(recs)}")
    print(f"  dupe groups:         {dup_groups}")
    print(f"  of which cross-root: {cross_root}")
    print(f"  to quarantine:       {len(quarantine)}  (inbox {by_root['inbox']} · new {by_root['new']})")
    print(f"  space reclaimed:     {reclaim / 1e9:.1f} GB")
    print(f"  unique after:        {len(recs) - len(quarantine)}")
    print(f"\nplan: {PLAN_OUT}")


if __name__ == "__main__":
    main()
