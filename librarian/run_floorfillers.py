"""Build the Floor Fillers crate = proven (played >=5 sets) + AI-curated anthems.

The raw AI floor-filler vote was too loose (~1,035). So we anchor on tracks
actually played in >=5 of the user's sets, then run a SELECTIVE AI pass over the
remaining floor-filler candidates, keeping only true crowd-uniting anthems.
Writes _Playlists/Floor Fillers.m3u. Small AI spend.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, "src")
from librarian import ai  # noqa: E402

HOME = Path.home()
PLAYLISTS = HOME / "DJ" / "library" / "_Playlists"
RUNS = HOME / "DJ" / ".librarian-runs"
CLASSIFY = ["/tmp/lib-djinbox/classify-plan.json", "/tmp/lib-musicnew/classify-plan.json"]
HISTORY = "/tmp/rb-history/combined.json"

_SCHEMA = {"type": "object", "properties": {"tracks": {"type": "array", "items": {
    "type": "object", "properties": {"index": {"type": "integer"}, "keep": {"type": "boolean"}},
    "required": ["index", "keep"], "additionalProperties": False}}},
    "required": ["tracks"], "additionalProperties": False}

_SYS = (
    "You curate a DJ's FLOOR FILLERS crate: globally-recognisable anthems that reliably unite "
    "and pack a dancefloor (Mr Brightside, I Gotta Feeling, One Dance, Titanium, Sweet Caroline, "
    "Niggas in Paris, Sandstorm tier). For each track set keep=true ONLY if it's a true crowd-"
    "uniting floor-filler the average partygoer recognises and reacts to. Be SELECTIVE — niche, "
    "album, deep, or merely-good tracks are keep=false. Return one entry per track by its index."
)


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

    played = [(norm(e["label"]), e.get("total", 0)) for e in json.load(open(HISTORY))]
    played = [(s, sp) for s, sp in played if len(s) >= 6]

    def spins(art, ti):
        sig = norm(f"{art} {ti}")
        if len(sig) < 6:
            return 0
        return max((sp for ps, sp in played if ps in sig or sig in ps), default=0)

    proven, candidates = {}, []
    for pl in CLASSIFY:
        for it in json.load(open(pl)):
            new = oldnew.get(it["path"])
            if not new:
                continue
            art, ti = it.get("artist", ""), it.get("title", "")
            if spins(art, ti) >= 5:
                proven[new] = True
            elif "Floor Fillers" in it.get("crates", []) and it.get("confidence") == "high":
                candidates.append({"new": new, "artist": art, "title": ti, "fname": Path(it["path"]).name})
    print(f"proven (>=5 sets): {len(proven)} · AI candidates: {len(candidates)}", flush=True)

    kept = set()
    lock = threading.Lock()
    batches = [candidates[i:i + 60] for i in range(0, len(candidates), 60)]

    def work(batch):
        payload = [{"index": i, "filename": c["fname"], "artist": c["artist"], "title": c["title"]}
                   for i, c in enumerate(batch)]
        try:
            import anthropic
            client = anthropic.Anthropic()
            lines = ["Decide keep for each track:"]
            for p in payload:
                lines.append(f"\n{p['index']}: {p['artist']} - {p['title']}  [{p['filename']}]")
            r = client.messages.create(model=ai.MODEL, max_tokens=ai._tags_max_tokens(len(batch)),
                                       system=_SYS, messages=[{"role": "user", "content": "\n".join(lines)}],
                                       output_config={"format": {"type": "json_schema", "schema": _SCHEMA}})
            data = json.loads("".join(b.text for b in r.content if getattr(b, "type", None) == "text"))
            with lock:
                for t in data["tracks"]:
                    if t.get("keep"):
                        kept.add(batch[int(t["index"])]["new"])
        except Exception as exc:
            print(f"  batch failed: {exc}", flush=True)

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, batches))

    members = sorted(set(proven) | kept)
    PLAYLISTS.mkdir(parents=True, exist_ok=True)
    with open(PLAYLISTS / "Floor Fillers.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n" + "\n".join(members) + "\n")
    print(f"\nFloor Fillers: {len(proven)} proven + {len(kept)} AI-kept = {len(members)} total")
    print(f"written: {PLAYLISTS / 'Floor Fillers.m3u'}")


if __name__ == "__main__":
    main()
