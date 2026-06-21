"""Find PioneerDJ 'Imported from Device' edits genuinely missing from the library
(and not already staged), and copy them to ~/DJ/inbox/_from-pioneerdj.

Acoustic-checks each against the library keepers + the already-staged USB batch,
drops within-batch dupes, and copies only true new ones. Originals untouched.
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import json
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
SRC = HOME / "Music" / "PioneerDJ" / "Imported from Device"
STAGED = HOME / "DJ" / "inbox" / "_from-usb"
OUT = HOME / "DJ" / "inbox" / "_from-pioneerdj"
FP_LIB = "/tmp/lib-dedupe/fingerprints.jsonl"
DEDUPE = "/tmp/lib-dedupe/dedupe-plan.json"
AUDIO = {".mp3", ".m4a", ".flac", ".wav", ".aiff", ".ogg", ".aac", ".opus"}


def fpof(path):
    try:
        d = json.loads(subprocess.run(["fpcalc", "-raw", "-json", "-length", "120", str(path)],
                                      capture_output=True, text=True, timeout=90).stdout)
        return d.get("duration", 0.0), d.get("fingerprint", [])
    except Exception:
        return 0.0, []


def ber(a, b):
    n = min(len(a), len(b))
    if n < 25:
        return 1.0
    return sum((a[i] ^ b[i]).bit_count() for i in range(n)) / (32.0 * n)


def main():
    # reference = library keepers + already-staged USB batch
    quar = {q["path"] for q in json.load(open(DEDUPE))["quarantine"]}
    ref = collections.defaultdict(list)
    for line in open(FP_LIB):
        r = json.loads(line)
        if r["p"] not in quar and r["fp"]:
            ref[int(round(r["d"]))].append((r["d"], r["fp"]))
    staged = [p for p in STAGED.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO]
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for d, f in ex.map(fpof, staged):
            if f:
                ref[int(round(d))].append((d, f))
    print(f"reference fingerprints: library keepers + {len(staged)} staged", flush=True)

    def in_ref(dur, f):
        if not f:
            return False
        for sec in range(int(dur - 2), int(dur + 3)):
            for kd, kf in ref.get(sec, ()):
                if abs(kd - dur) <= 2 and ber(f, kf) < 0.25:
                    return True
        return False

    src = sorted(p for p in SRC.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO)
    print(f"PioneerDJ source files: {len(src)}", flush=True)
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        sfps = dict(zip(src, ex.map(fpof, src)))

    new, seen = [], []
    for p in src:
        d, f = sfps[p]
        if in_ref(d, f):
            continue
        if any(sf and f and abs(sd - d) <= 2 and ber(f, sf) < 0.25 for sd, sf, _ in seen):
            continue
        seen.append((d, f, p))
        new.append(p)
    print(f"genuinely new (not in library, not staged, not batch-dupe): {len(new)}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    copied = 0
    for p in new:
        dst = OUT / p.name
        if not dst.exists():
            shutil.copy2(p, dst)
            copied += 1
    sz = sum((OUT / Path(p).name).stat().st_size for p in new if (OUT / Path(p).name).exists())
    print(f"copied to {OUT}: {copied}  ({sz / 1e9:.1f} GB)")


if __name__ == "__main__":
    main()
