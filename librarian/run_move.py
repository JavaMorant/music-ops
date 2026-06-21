"""Build (and optionally apply) the move into ~/DJ/library/<genre>/.

Keepers move to library/<parent>/<leaf>/, dupes go to _quarantine (never deleted),
and each taggable keeper gets its genre written (inbox files also get their clean
artist/title). Reuses the librarian engine's tested move/undo, but APPLIES IN
CHUNKS (the engine re-journals per step, so one 16k-action plan would be O(n^2)).

  python run_move.py            # DRY RUN: write plan + print tree/sample
  APPLY=1 python run_move.py     # apply in ~400-file chunks (backup off; undo per chunk)
"""
from __future__ import annotations

import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from librarian import engine, paths as P  # noqa: E402
from librarian.model import Action, MOVE, Plan, QUARANTINE, TagEdit  # noqa: E402
from librarian import tags as TG  # noqa: E402

HOME = Path.home()
LIB = HOME / "DJ" / "library"
QUAR = HOME / "DJ" / "_quarantine"
RUNS = HOME / "DJ" / ".librarian-runs"
CLASSIFY = ["/tmp/lib-djinbox/classify-plan.json", "/tmp/lib-musicnew/classify-plan.json"]
DEDUPE = "/tmp/lib-dedupe/dedupe-plan.json"
FULLPLAN = "/tmp/lib-djinbox/full-plan.json"
PLAN_OUT = "/tmp/lib-move/move-plan.json"
CHUNK = 400

REMAP = {"Trance": "EDM / Big Room", "Bass / Dubstep": "Drum & Bass"}
PARENT = {}
for g in ("UK Drill & New Rap", "Afro Swing", "Grime & UK Classics", "Trap (Melodic)",
          "Trap (Hard/Rage)", "US Rap (Throwback)", "US Rap (Modern)", "Rap (Other)"):
    PARENT[g] = "Rap & Hip-Hop"
for g in ("EDM / Big Room", "Tech House / Techno", "Drum & Bass"):
    PARENT[g] = "Dance & Electronic"
for g in ("Edits (House)", "Edits (Afro/Amapiano)", "Edits (Jersey/Club)",
          "Edits (Brazilian/Phonk)", "Edits (Pop/Throwback)"):
    PARENT[g] = "Edits & Remixes"
PARENT["Pop (pre-2015)"] = PARENT["Pop (2015+)"] = "Pop"
PARENT["R&B (Throwback)"] = PARENT["R&B (Modern/Alt)"] = "R&B"


def leaf_subfolder(leaf: str) -> str:
    for pre in ("Pop (", "R&B (", "Edits ("):
        if leaf.startswith(pre) and leaf.endswith(")"):
            return leaf[len(pre):-1]
    return leaf


def dest_dir(leaf: str) -> Path:
    parent = PARENT.get(leaf)
    if parent is None:
        return LIB / P.sanitize_component(leaf)
    return LIB / P.sanitize_component(parent) / P.sanitize_component(leaf_subfolder(leaf))


def build():
    clean = {}
    for e in json.load(open(FULLPLAN))["tag_edits"]:
        f = e["fields"]
        clean[e["path"]] = {"artist": f.get("artist", ""), "title": f.get("title", ""),
                            "genre": f.get("genre", "")}
    dupes = {q["path"] for q in json.load(open(DEDUPE))["quarantine"]}
    items = {}
    for plan in CLASSIFY:
        for it in json.load(open(plan)):
            items.setdefault(it["path"], it)

    def dest_name(path: str) -> str:
        # Keep the ORIGINAL filename: it preserves DJ version markers ([Bass],
        # [Drums], (Intro Dirty), (Clean Extended)) that a clean Artist-Title
        # rename would drop, collapsing distinct edits onto each other. Clean
        # metadata still lands in the tags below, which is what rekordbox browses.
        return Path(path).name

    units = []  # (Action, TagEdit|None)
    tree = collections.Counter()
    reserved: set[str] = set()
    collisions = wav_skipped = 0
    for path, it in items.items():
        src = Path(path).absolute()
        if not src.exists():
            continue  # already moved by a prior run — resume past it
        if path in dupes:
            dest = P.collision_free(QUAR / Path(path).name, reserved)
            reserved.add(P.norm_key(dest))
            units.append((Action(QUARANTINE, src, dest.absolute(), "near-dupe: lower-quality copy"), None))
            continue
        leaf = REMAP.get(it.get("genre_leaf") or "Other", it.get("genre_leaf") or "Other")
        ddir = dest_dir(leaf)
        want = ddir / dest_name(path)
        dest = P.collision_free(want, reserved)
        if dest.name != want.name:
            collisions += 1
        reserved.add(P.norm_key(dest))
        units.append((Action(MOVE, src, dest.absolute(), f"genre: {leaf}"), None))
        tree[str(ddir.relative_to(LIB))] += 1
        if TG.is_taggable(src):
            fields = {}
            c = clean.get(path)
            if c:
                if c.get("artist"):
                    fields["artist"] = c["artist"]
                if c.get("title"):
                    fields["title"] = c["title"]
            fields["genre"] = leaf
            units[-1] = (units[-1][0], TagEdit(path=src, fields=fields,
                                               reason="genre" + ("+clean tags" if c else "")))
        else:
            wav_skipped += 1
    return units, tree, collisions, wav_skipped


def main():
    units, tree, collisions, wav_skipped = build()
    moves = [u for u in units if u[0].kind == MOVE]
    quars = [u for u in units if u[0].kind == QUARANTINE]
    tags = [u for u in units if u[1] is not None]

    os.makedirs(os.path.dirname(PLAN_OUT), exist_ok=True)
    json.dump({"library_root": str(HOME),
               "actions": [a.to_dict() for a, _ in units],
               "tag_edits": [t.to_dict() for _, t in units if t]},
              open(PLAN_OUT, "w"), ensure_ascii=False)

    print(f"=== MOVE PLAN (dry run) ===")
    print(f"  moves -> library:   {len(moves)}")
    print(f"  quarantine (dupes): {len(quars)}")
    print(f"  genre tags written: {len(tags)}  (WAV/untaggable skipped: {wav_skipped})")
    print(f"  name collisions auto-resolved with (2),(3)...: {collisions}\n")
    print("--- library tree (folder -> tracks) ---")
    for folder, n in sorted(tree.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {n:5d}  {folder}")
    print("\n--- sample moves (src name -> dest) ---")
    shown = 0
    for a, t in units:
        if a.kind != MOVE:
            continue
        rel = str(a.dest).replace(str(HOME) + "/DJ/library/", "")
        tg = f"   [genre tag: {t.fields.get('genre')}]" if t else "   [no tag: wav]"
        print(f"  {Path(a.src).name[:42]:42}  ->  {rel[:55]}{tg}")
        shown += 1
        if shown >= 22:
            break

    if os.environ.get("APPLY") == "1":
        print(f"\n=== APPLYING in chunks of {CHUNK} (backup off; rely on external backup + undo) ===",
              flush=True)
        chunks = [units[i:i + CHUNK] for i in range(0, len(units), CHUNK)]
        run_ids, failed = [], 0
        for ci, ch in enumerate(chunks):
            p = Plan(library_root=HOME, actions=[a for a, _ in ch],
                     tag_edits=[t for _, t in ch if t])
            try:
                jr = engine.apply_plan(p, RUNS, backup=False)
                run_ids.append(jr.run_id)
                print(f"  chunk {ci + 1}/{len(chunks)} ✓ {jr.run_id} "
                      f"({len([a for a, _ in ch])} acts, {len([t for _, t in ch if t])} tags)", flush=True)
            except Exception as exc:
                failed += 1
                print(f"  chunk {ci + 1}/{len(chunks)} ✗ FAILED (skipped, others continue): {exc}", flush=True)
        json.dump(run_ids, open("/tmp/lib-move/run-ids.json", "w"))
        print(f"\nAPPLIED · {len(run_ids)} runs ok · {failed} chunks failed · "
              f"ids -> /tmp/lib-move/run-ids.json")


if __name__ == "__main__":
    main()
