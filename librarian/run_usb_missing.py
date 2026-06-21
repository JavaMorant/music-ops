"""Find tracks on BOTH USBs that are genuinely missing from ~/DJ/library, and
stage them in ~/DJ/inbox/_from-usb for later folding into the library.

Tag-matching over-reports missing (blank/inconsistent USB tags), so we acoustic-
check: take the both-stick tracks whose tags don't match the library, fingerprint
them, and keep only those with NO acoustic match among the library keepers. Copy
the genuinely-missing ones to the inbox (won't disturb an in-progress import).
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import json
import re
import shutil
import subprocess
from pathlib import Path

from run_pulse_usb import parse_pdb
from librarian import tags as TG

DEDUPE = "/tmp/lib-dedupe/dedupe-plan.json"
FP = "/tmp/lib-dedupe/fingerprints.jsonl"
INBOX = Path.home() / "DJ" / "inbox" / "_from-usb"
USBS = {"UNTITLED": "/Volumes/UNTITLED", "DIBSS": "/Volumes/DIBSS"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def key(a, t):
    return norm(a) + "|" + norm(t)


def fullpath(vol, fp):
    return vol + fp if fp.startswith("/") else f"{vol}/{fp}"


def fpof(path):
    try:
        d = json.loads(subprocess.run(["fpcalc", "-raw", "-json", "-length", "120", path],
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
    d, _ = parse_pdb(Path(USBS["DIBSS"]) / "PIONEER/rekordbox/export.pdb")
    u, _ = parse_pdb(Path(USBS["UNTITLED"]) / "PIONEER/rekordbox/export.pdb")
    dk = {key(t["artist"], t["title"]): t for t in d.values() if t["artist"] or t["title"]}
    uk = {key(t["artist"], t["title"]): t for t in u.values() if t["artist"] or t["title"]}
    both = (set(dk) & set(uk)) - {"|"}

    # library tag keys (confirmed in-library if a USB track's tags match one)
    LIB = Path.home() / "DJ" / "library"
    libk = set()
    for p in LIB.rglob("*"):
        if p.is_file() and p.suffix.lower() in TG.WRITABLE_EXTS:
            try:
                t = TG.read_tags(p, ["artist", "title"])
                libk.add(key(t.get("artist", ""), t.get("title", "")))
            except Exception:
                pass
    candidates = [k for k in both if k not in libk]  # tag-missing -> verify acoustically
    print(f"both USBs: {len(both)} · tag-missing (to acoustic-check): {len(candidates)}", flush=True)

    # library keeper fingerprints, bucketed by duration
    quar = {q["path"] for q in json.load(open(DEDUPE))["quarantine"]}
    kb = collections.defaultdict(list)
    for line in open(FP):
        r = json.loads(line)
        if r["p"] not in quar and r["fp"]:
            kb[int(round(r["d"]))].append((r["d"], r["fp"]))

    # fingerprint each candidate (prefer the higher-bitrate of the two USB copies by file size)
    def best_src(k):
        cands = []
        for vol, t in ((USBS["UNTITLED"], uk[k]), (USBS["DIBSS"], dk[k])):
            if t["path"]:
                p = Path(fullpath(vol, t["path"]))
                if p.exists():
                    cands.append(p)
        return max(cands, key=lambda p: p.stat().st_size) if cands else None

    srcs = {k: best_src(k) for k in candidates}
    srcs = {k: p for k, p in srcs.items() if p}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        fps = dict(zip(srcs, ex.map(lambda k: fpof(str(srcs[k])), srcs)))

    missing = []
    for k, (dur, f) in fps.items():
        hit = False
        if f:
            for sec in range(int(dur - 2), int(dur + 3)):
                for kd, kf in kb.get(sec, ()):
                    if abs(kd - dur) <= 2 and ber(f, kf) < 0.25:
                        hit = True
                        break
                if hit:
                    break
        if not hit:
            missing.append(k)
    print(f"genuinely missing (no acoustic match in library): {len(missing)}", flush=True)

    INBOX.mkdir(parents=True, exist_ok=True)
    copied = 0
    for k in missing:
        src = srcs[k]
        dst = INBOX / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            copied += 1
    print(f"copied to {INBOX}: {copied}")
    json.dump([{"artist": uk[k]["artist"], "title": uk[k]["title"]} for k in missing],
              open("/tmp/lib-loose/usb-missing.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
