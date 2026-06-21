"""Extend crates from the user's existing rekordbox playlists.

Some crates (Scottish, Tropical House, Jungle, Jazzy House, Gyalist) live mostly
in curated rekordbox playlists, not in the AI votes. Match those playlists'
tracks (by artist+title) into the NEW library and union them into the .m3u.
Reading several same-named playlists merges the duplicates automatically.
"""
from __future__ import annotations

import collections
import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from pyrekordbox import Rekordbox6Database  # noqa: E402
from pyrekordbox.db6 import tables  # noqa: E402

HOME = Path.home()
PLAYLISTS = HOME / "DJ" / "library" / "_Playlists"
RUNS = HOME / "DJ" / ".librarian-runs"
CLASSIFY = ["/tmp/lib-djinbox/classify-plan.json", "/tmp/lib-musicnew/classify-plan.json"]

# rekordbox playlist name (lowercase substring) -> crate .m3u
MAP = {"gyalist": "Gyalist", "jazzy house": "Jazzy House", "tropical house": "Tropical House",
       "vic classics": "Scottish", "jungle is massive": "Jungle"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main():
    oldnew = {}
    for jd in RUNS.iterdir():
        jf = jd / "journal.json"
        if jf.exists():
            for a in json.load(open(jf)).get("actions", []):
                if a["kind"] == "move" and a["status"] == "done":
                    oldnew[a["src"]] = a["dest"]
    libidx = {}
    for pl in CLASSIFY:
        for it in json.load(open(pl)):
            new = oldnew.get(it["path"])
            if not new:
                continue
            k = norm(f"{it.get('artist', '')}{it.get('title', '')}")
            if len(k) >= 5:
                libidx.setdefault(k, new)
    print(f"library index: {len(libidx)} tracks")

    db = Rekordbox6Database()
    P, SP = tables.DjmdPlaylist, tables.DjmdSongPlaylist
    rb = collections.defaultdict(list)
    src_playlists = collections.defaultdict(set)
    for p in db.session.query(P).all():
        nm = (p.Name or "").strip().lower()
        crate = next((cr for key, cr in MAP.items() if key in nm), None)
        if not crate:
            continue
        src_playlists[crate].add(p.Name)
        for sp in db.session.query(SP).filter(SP.PlaylistID == p.ID).all():
            c = db.get_content(ID=sp.ContentID)
            if not c:
                continue
            art = getattr(c, "ArtistName", None) or (c.Artist.Name if getattr(c, "Artist", None) else "") or ""
            rb[crate].append((art, getattr(c, "Title", "") or ""))

    print(f"\n--- extending crates ---")
    for crate, tracks in rb.items():
        m3u = PLAYLISTS / f"{crate}.m3u"
        members = set()
        if m3u.exists():
            members = {ln.strip() for ln in open(m3u) if ln.strip() and not ln.startswith("#")}
        before, matched = len(members), 0
        for art, ti in tracks:
            np = libidx.get(norm(f"{art}{ti}"))
            if np:
                if np not in members:
                    matched += 1
                members.add(np)
        out = sorted(members)
        m3u.parent.mkdir(parents=True, exist_ok=True)
        m3u.write_text("#EXTM3U\n" + "\n".join(out) + "\n", encoding="utf-8")
        merged = len(src_playlists[crate])
        print(f"  {crate}: {before} -> {len(out)}  (+{matched} from {len(tracks)} rb tracks "
              f"across {merged} merged playlist copies)")


if __name__ == "__main__":
    main()
