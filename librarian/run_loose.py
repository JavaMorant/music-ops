"""Fold the loose, parent-genre-only trial files into the pipeline.

These ~298 files (filed by an earlier run as just 'Rap & Hip-Hop', 'Pop', ...)
sit loose in the split-genre roots and never got deduped or sub-classified.
Fingerprint them, drop any that acoustically match a library keeper, and
classify the survivors into leaf genres. Writes loose-plan.json. Moves nothing.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "src")
from librarian import ai, tags  # noqa: E402

HOME = Path.home()
LIB = HOME / "DJ" / "library"
SPLIT_ROOTS = ["Rap & Hip-Hop", "Dance & Electronic", "Pop", "R&B", "Edits & Remixes"]
FP = "/tmp/lib-dedupe/fingerprints.jsonl"
DEDUPE = "/tmp/lib-dedupe/dedupe-plan.json"
OUT = "/tmp/lib-loose/loose-plan.json"
BER_THRESH, DUR_TOL, MIN_FRAMES = 0.25, 2.0, 25


def ber(a, b):
    n = min(len(a), len(b))
    if n < MIN_FRAMES:
        return 1.0
    return sum((a[i] ^ b[i]).bit_count() for i in range(n)) / (32.0 * n)


def fpof(path):
    try:
        d = json.loads(subprocess.run(["fpcalc", "-raw", "-json", "-length", "120", path],
                                      capture_output=True, text=True, timeout=90).stdout)
        return d.get("duration", 0.0), d.get("fingerprint", [])
    except Exception:
        return 0.0, []


def main():
    loose = [p for r in SPLIT_ROOTS for p in (LIB / r).glob("*")
             if p.is_file() and p.suffix.lower() in (tags.WRITABLE_EXTS | {".wav", ".aiff"})]
    print(f"loose files: {len(loose)}", flush=True)

    # keeper fingerprints (library survivors): all fp entries minus the quarantined
    quar = {q["path"] for q in json.load(open(DEDUPE))["quarantine"]}
    keepers = []  # (dur, fp)
    for line in open(FP):
        r = json.loads(line)
        if r["p"] not in quar and r["fp"]:
            keepers.append((r["d"], r["fp"]))
    kb = {}
    for d, f in keepers:
        kb.setdefault(int(round(d)), []).append((d, f))
    print(f"keeper fingerprints: {len(keepers)}", flush=True)

    # fingerprint the loose files
    fps = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for p, (d, f) in zip(loose, ex.map(lambda p: fpof(str(p)), loose)):
            fps[str(p)] = (d, f)

    dupes, survivors = [], []
    for p in loose:
        d, f = fps[str(p)]
        hit = False
        if f:
            for sec in range(int(d - DUR_TOL), int(d + DUR_TOL) + 1):
                for kd, kf in kb.get(sec, ()):
                    if abs(kd - d) <= DUR_TOL and ber(f, kf) < BER_THRESH:
                        hit = True
                        break
                if hit:
                    break
        (dupes if hit else survivors).append(p)
    print(f"loose dupes of library: {len(dupes)} · survivors to re-file: {len(survivors)}", flush=True)

    # classify survivors into leaf genres
    metas = []
    for p in survivors:
        try:
            t = tags.read_tags(p, ["artist", "title"])
        except Exception:
            t = {}
        metas.append({"path": str(p), "filename": p.name,
                      "artist": t.get("artist") or "", "title": t.get("title") or "", "genre": ""})
    classified = {}
    batches = [metas[i:i + 60] for i in range(0, len(metas), 60)]
    for b in batches:
        payload = [{"index": i, **{k: m[k] for k in ("filename", "artist", "title", "genre")}}
                   for i, m in enumerate(b)]
        try:
            for s in ai.classify_tracks(payload):
                classified[b[s.index]["path"]] = {"genre_leaf": s.genre, "crates": list(s.crates)}
        except Exception as exc:
            print(f"  classify batch failed: {exc}", flush=True)

    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    json.dump({"dupes": [str(p) for p in dupes],
               "survivors": [{"path": str(p), **classified.get(str(p), {"genre_leaf": "Other", "crates": []})}
                             for p in survivors]}, open(OUT, "w"), ensure_ascii=False)
    print(f"\nplan: {OUT}  ({len(classified)} classified)")


if __name__ == "__main__":
    main()
