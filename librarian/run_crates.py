"""Generate .m3u crate playlists for the organized library.

Maps each track's crate votes (from the classify plans, keyed by OLD path) to its
NEW library path via the run journals, tightens Floor Fillers to high-confidence
OR proven-in-play-history, and writes _Playlists/*.m3u (absolute paths, importable
into rekordbox/Serato). Read-only w.r.t. audio.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

HOME = Path.home()
LIB = HOME / "DJ" / "library"
PLAYLISTS = LIB / "_Playlists"
RUNS = HOME / "DJ" / ".librarian-runs"
CLASSIFY = ["/tmp/lib-djinbox/classify-plan.json", "/tmp/lib-musicnew/classify-plan.json"]
HISTORY = "/tmp/rb-history/combined.json"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main() -> None:
    # 1) authoritative old -> new path map from the move journals
    oldnew = {}
    for jdir in RUNS.iterdir():
        jf = jdir / "journal.json"
        if not jf.exists():
            continue
        for a in json.load(open(jf)).get("actions", []):
            if a["kind"] == "move" and a["status"] == "done":
                oldnew[a["src"]] = a["dest"]
    print(f"old->new moves mapped: {len(oldnew)}")

    # 2) play-history signatures (for Floor Fillers tightening)
    played = []
    if Path(HISTORY).exists():
        for e in json.load(open(HISTORY)):
            sig = norm(e.get("label", ""))
            if len(sig) >= 6:
                played.append((sig, e.get("total", 0)))

    def is_played(artist: str, title: str, minspins: int = 2) -> bool:
        sig = norm(f"{artist} {title}")
        if len(sig) < 6:
            return False
        return any(sp >= minspins and (ps in sig or sig in ps) for ps, sp in played)

    # 3) classify votes (keyed by old path)
    crates = collections.defaultdict(set)
    ff_high = ff_played = 0
    for pl in CLASSIFY:
        for it in json.load(open(pl)):
            new = oldnew.get(it["path"])
            if not new:
                continue  # quarantined dupe or unmoved
            conf = it.get("confidence")
            art, ti = it.get("artist", ""), it.get("title", "")
            for c in it.get("crates", []):
                if c == "Floor Fillers":
                    hi, pl_ = conf == "high", is_played(art, ti)
                    if hi or pl_:
                        crates["Floor Fillers"].add(new)
                        ff_high += hi
                        ff_played += pl_ and not hi
                else:
                    crates[c].add(new)

    # 4) write .m3u
    PLAYLISTS.mkdir(parents=True, exist_ok=True)
    print(f"\n--- crates written to {PLAYLISTS} ---")
    for name in sorted(crates, key=lambda k: -len(crates[k])):
        paths = sorted(crates[name])
        with open(PLAYLISTS / f"{name}.m3u", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write("\n".join(paths) + "\n")
        print(f"  {len(paths):5d}  {name}.m3u")
    print(f"\nFloor Fillers tightened: {ff_high} high-confidence + {ff_played} play-history "
          f"= {len(crates['Floor Fillers'])} (was 1,084 raw votes)")


if __name__ == "__main__":
    main()
