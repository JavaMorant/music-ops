"""Flatten the genre tree to one level with grouped 'Parent - Leaf' names, fold
in the re-filed loose survivors, quarantine loose dupes, and rewrite the crates.

  python run_flatten.py          # DRY RUN: print the moves
  APPLY=1 python run_flatten.py   # do it
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "src")
from librarian import paths as P  # noqa: E402
from librarian import tags as TG  # noqa: E402
from run_move import REMAP, dest_dir  # noqa: E402  (current nested path per leaf)

HOME = Path.home()
LIB = HOME / "DJ" / "library"
QUAR = HOME / "DJ" / "_quarantine"
PLAYLISTS = LIB / "_Playlists"
LOOSE_PLAN = "/tmp/lib-loose/loose-plan.json"
SPLIT_PARENTS = ["Rap & Hip-Hop", "Dance & Electronic", "Pop", "R&B", "Edits & Remixes"]

# canonical leaf genre -> flat top-level folder name
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


def main():
    apply = os.environ.get("APPLY") == "1"
    # 1) directory remap: current nested leaf dir -> flat dir (string prefixes for crate rewrite)
    dir_remap = {}
    folder_moves = []
    for leaf, flat in FLAT.items():
        cur = dest_dir(leaf)
        dst = LIB / flat
        if cur.resolve() == dst.resolve():
            continue  # single genre, already top-level
        dir_remap[str(cur)] = str(dst)
        if cur.is_dir():
            folder_moves.append((cur, dst))

    print(f"=== FLATTEN ({'APPLY' if apply else 'dry run'}) ===")
    print(f"folder moves (nested leaf -> flat): {len(folder_moves)}")
    for cur, dst in folder_moves[:6]:
        print(f"  {cur.relative_to(LIB)}  ->  {dst.name}")
    print("  ...")

    # 2) loose survivors -> flat leaf; loose dupes -> quarantine
    survivors, dupes = [], []
    if Path(LOOSE_PLAN).exists():
        lp = json.load(open(LOOSE_PLAN))
        dupes = lp["dupes"]
        survivors = lp["survivors"]
    print(f"loose survivors to re-file: {len(survivors)} · loose dupes -> quarantine: {len(dupes)}")

    if not apply:
        print("\n(dry run — re-run with APPLY=1)")
        # still emit the dir_remap so crate rewrite can be previewed
        json.dump(dir_remap, open("/tmp/lib-loose/dir-remap.json", "w"))
        return

    reserved = set()
    # move leaf folders up
    for cur, dst in folder_moves:
        if dst.exists():
            for f in list(cur.iterdir()):
                d = P.collision_free(dst / f.name, reserved)
                reserved.add(P.norm_key(d))
                os.rename(f, d)
            cur.rmdir()
        else:
            os.rename(cur, dst)
    # loose survivors
    for s in survivors:
        src = Path(s["path"])
        if not src.exists():
            continue
        leaf = REMAP.get(s.get("genre_leaf") or "Other", s.get("genre_leaf") or "Other")
        flat = FLAT.get(leaf, "Other")
        dst = P.collision_free(LIB / flat / src.name, reserved)
        reserved.add(P.norm_key(dst))
        dst.parent.mkdir(parents=True, exist_ok=True)
        if TG.is_taggable(src):
            try:
                TG.write_tags(src, {"genre": leaf})  # upgrade parent-genre tag to the leaf
            except TG.TagError:
                pass
        os.rename(src, dst)
    # loose dupes -> quarantine
    QUAR.mkdir(parents=True, exist_ok=True)
    for d in dupes:
        src = Path(d)
        if src.exists():
            dst = P.collision_free(QUAR / src.name, reserved)
            reserved.add(P.norm_key(dst))
            os.rename(src, dst)
    # remove now-empty split parents
    for parent in SPLIT_PARENTS:
        pp = LIB / parent
        if pp.is_dir() and not any(pp.iterdir()):
            pp.rmdir()

    # 3) rewrite crate .m3u paths (nested -> flat)
    rewritten = 0
    for m3u in PLAYLISTS.glob("*.m3u"):
        lines = m3u.read_text(encoding="utf-8").splitlines()
        out = []
        for ln in lines:
            for old, new in dir_remap.items():
                if ln.startswith(old + "/"):
                    ln = new + ln[len(old):]
                    break
            out.append(ln)
        m3u.write_text("\n".join(out) + "\n", encoding="utf-8")
        rewritten += 1
    print(f"\nflattened · crates rewritten: {rewritten}")
    print("done.")


if __name__ == "__main__":
    main()
