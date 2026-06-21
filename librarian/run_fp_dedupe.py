"""Acoustic dedupe from chromaprint fingerprints.

Two files are the same recording iff their raw fingerprints have a low
bit-error-rate (BER). Different songs — even same album, same length — have
BER ~0.45; transcodes/re-rips of one recording have BER < ~0.15. We greedily
cluster within duration buckets and keep the best-quality copy of each cluster.
Writes dedupe-plan.json (consumed by run_move.py). MOVES NOTHING.

  python run_fp_dedupe.py            # cluster + write plan + summary
  AUDIT=1 python run_fp_dedupe.py    # also print BER calibration samples
"""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

FP = "/tmp/lib-dedupe/fingerprints.jsonl"
PLAN_OUT = "/tmp/lib-dedupe/dedupe-plan.json"
LOSSLESS = {".flac", ".wav", ".aiff", ".aif", ".alac", ".ape"}
DUR_TOL = 2.0
BER_THRESH = 0.25      # below this = same recording
MIN_FRAMES = 25        # need enough overlap to trust the BER


def ber(a, b) -> float:
    n = min(len(a), len(b))
    if n < MIN_FRAMES:
        return 1.0
    bits = 0
    for i in range(n):
        bits += (a[i] ^ b[i]).bit_count()
    return bits / (32.0 * n)


def quality(path: str):
    p = path.lower()
    junk = ("_spotdown" in p) + bool(re.search(r"\(\d+\)\.[a-z0-9]+$", p))
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return (Path(path).suffix.lower() in LOSSLESS, size, -junk, -len(path))


def main() -> None:
    recs = []
    for line in open(FP):
        r = json.loads(line)
        recs.append(r)
    print(f"loaded {len(recs)} fingerprints", flush=True)

    fp_recs = [r for r in recs if r["fp"]]
    no_fp = [r for r in recs if not r["fp"]]
    fp_recs.sort(key=lambda r: r["d"])

    # greedy clustering, indexed by integer-second duration bucket
    clusters = []  # each: {"rep": fp list, "d": dur, "members": [path,...]}
    buckets = collections.defaultdict(list)  # sec -> [cluster idx]
    for r in fp_recs:
        d, f = r["d"], r["fp"]
        joined = False
        for sec in range(int(d - DUR_TOL), int(d + DUR_TOL) + 1):
            for ci in buckets.get(sec, ()):
                c = clusters[ci]
                if abs(c["d"] - d) <= DUR_TOL and ber(f, c["rep"]) < BER_THRESH:
                    c["members"].append(r["p"])
                    joined = True
                    break
            if joined:
                break
        if not joined:
            ci = len(clusters)
            clusters.append({"rep": f, "d": d, "members": [r["p"]]})
            buckets[int(round(d))].append(ci)
    print(f"{len(clusters)} clusters from {len(fp_recs)} fingerprinted files", flush=True)

    quarantine, keepers = [], 0
    dup_groups = reclaim = 0
    for c in clusters:
        if len(c["members"]) < 2:
            keepers += 1
            continue
        dup_groups += 1
        members = sorted(c["members"], key=quality, reverse=True)
        keeper, dupes = members[0], members[1:]
        keepers += 1
        for dpath in dupes:
            try:
                reclaim += os.path.getsize(dpath)
            except OSError:
                pass
            quarantine.append({"path": dpath, "keeper": keeper, "reason": "acoustic dupe"})

    os.makedirs(os.path.dirname(PLAN_OUT), exist_ok=True)
    json.dump({"keepers": [], "quarantine": quarantine}, open(PLAN_OUT, "w"), ensure_ascii=False)

    by_root = collections.Counter("inbox" if "/DJ/inbox/" in q["path"] else "new" for q in quarantine)
    print(f"\n=== ACOUSTIC DEDUPE ===")
    print(f"  fingerprinted:    {len(fp_recs)}   (+{len(no_fp)} unfingerprintable, all kept)")
    print(f"  dupe groups:      {dup_groups}")
    print(f"  to quarantine:    {len(quarantine)}  (inbox {by_root['inbox']} · new {by_root['new']})")
    print(f"  space reclaimed:  {reclaim / 1e9:.1f} GB")
    print(f"  unique after:     {len(recs) - len(quarantine)}")

    if os.environ.get("AUDIT") == "1":
        print("\n--- sample dupe groups (same recording?) ---")
        shown = 0
        for c in clusters:
            if len(c["members"]) < 2:
                continue
            print("  • " + "  |  ".join(Path(m).name[:38] for m in c["members"][:3]))
            shown += 1
            if shown >= 14:
                break


if __name__ == "__main__":
    main()
