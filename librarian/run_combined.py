"""Final combined breakdown: both roots, minus near-dupes, with agreed tweaks.

Merges the two classify plans, removes the files the near-dupe pass flags for
quarantine, applies the genre merges the user signed off (Trance -> EDM,
Bass/Dubstep -> Drum & Bass), and prints the real shape of ~/DJ/library/.
Read-only.
"""
from __future__ import annotations

import collections
import json
import os

PLANS = ["/tmp/lib-djinbox/classify-plan.json", "/tmp/lib-musicnew/classify-plan.json"]
DEDUPE = "/tmp/lib-dedupe/dedupe-plan.json"

REMAP = {"Trance": "EDM / Big Room", "Bass / Dubstep": "Drum & Bass"}

PARENT = {}
for g in ("UK Drill & New Rap", "Afro Swing", "Grime & UK Classics", "Trap (Melodic)",
          "Trap (Hard/Rage)", "US Rap (Throwback)", "US Rap (Modern)", "Rap (Other)"):
    PARENT[g] = "RAP & HIP-HOP"
for g in ("EDM / Big Room", "Tech House / Techno", "Drum & Bass"):
    PARENT[g] = "DANCE & ELECTRONIC"
for g in ("Edits (House)", "Edits (Afro/Amapiano)", "Edits (Jersey/Club)",
          "Edits (Brazilian/Phonk)", "Edits (Pop/Throwback)"):
    PARENT[g] = "EDITS & REMIXES"
PARENT["Pop (pre-2015)"] = PARENT["Pop (2015+)"] = "POP"
PARENT["R&B (Throwback)"] = PARENT["R&B (Modern/Alt)"] = "R&B"


def main() -> None:
    quarantined = set()
    if os.path.exists(DEDUPE):
        for q in json.load(open(DEDUPE))["quarantine"]:
            quarantined.add(q["path"])

    by_genre, by_crate = collections.Counter(), collections.Counter()
    per_root = collections.Counter()
    kept = dropped = 0
    seen = set()
    for plan in PLANS:
        for it in json.load(open(plan)):
            p = it["path"]
            if p in seen:
                continue
            seen.add(p)
            if p in quarantined:
                dropped += 1
                continue
            g = it.get("genre_leaf")
            if not g:
                continue
            g = REMAP.get(g, g)
            kept += 1
            by_genre[g] += 1
            per_root["inbox" if "/DJ/inbox/" in p else "new"] += 1
            for cr in it.get("crates", []):
                by_crate[cr] += 1

    print(f"=== COMBINED LIBRARY (post-dedupe) ===")
    print(f"  kept: {kept}  (inbox {per_root['inbox']} · new {per_root['new']})  |  "
          f"dupes removed: {dropped}\n")

    groups = collections.defaultdict(list)
    for g, n in by_genre.items():
        groups[PARENT.get(g, g.upper())].append((n, g))
    order = ["RAP & HIP-HOP", "DANCE & ELECTRONIC", "POP", "R&B", "EDITS & REMIXES"]
    printed = set()
    rows = []
    for parent in order + sorted(k for k in groups if k not in order):
        if parent in printed:
            continue
        printed.add(parent)
        subs = sorted(groups[parent], reverse=True)
        tot = sum(n for n, _ in subs)
        rows.append((tot, parent, subs))
    # singles (single-leaf parents) sorted after the grouped ones by size
    for tot, parent, subs in sorted(rows, key=lambda r: -r[0]):
        if len(subs) == 1 and subs[0][1].upper() == parent:
            print(f"  {tot:5d}  {subs[0][1]}")
        else:
            label = parent + (" (incl. Bass/Dubstep)" if parent == "DANCE & ELECTRONIC" else "")
            print(f"  {tot:5d}  {parent}")
            for n, g in subs:
                tag = " (+Trance)" if g == "EDM / Big Room" else (
                    " (+Bass/Dubstep)" if g == "Drum & Bass" else "")
                print(f"         {n:5d}  └ {g}{tag}")
    print("\n--- CRATES (raw AI vote; Floor Fillers tightened at build time) ---")
    for cr, n in by_crate.most_common():
        print(f"  {n:5d}  {cr}")


if __name__ == "__main__":
    main()
