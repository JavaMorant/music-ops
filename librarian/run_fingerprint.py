"""Acoustic-fingerprint every file in both roots, via chromaprint's fpcalc.

Writes one JSON object per line to fingerprints.jsonl (resumable: a re-run skips
paths already done). Parallel — fpcalc shells out to decode audio, so threads
help. Free, no API.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import subprocess
import sys
import threading

PLANS = ["/tmp/lib-djinbox/classify-plan.json", "/tmp/lib-musicnew/classify-plan.json"]
OUT = "/tmp/lib-dedupe/fingerprints.jsonl"
WORKERS = 10
LENGTH = "120"  # analyse first 120s — plenty to identify a recording


def fp(path: str):
    try:
        r = subprocess.run(["fpcalc", "-raw", "-json", "-length", LENGTH, path],
                           capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout)
        return {"p": path, "d": d.get("duration", 0.0), "fp": d.get("fingerprint", [])}
    except Exception:
        return {"p": path, "d": 0.0, "fp": []}


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                done.add(json.loads(line)["p"])
            except Exception:
                pass

    paths, seen = [], set()
    for plan in PLANS:
        for it in json.load(open(plan)):
            p = it["path"]
            if p not in seen and os.path.exists(p):
                seen.add(p)
                if p not in done:
                    paths.append(p)
    print(f"{len(seen)} files total · {len(done)} already fingerprinted · {len(paths)} to do", flush=True)

    out = open(OUT, "a")
    lock = threading.Lock()
    n = [0]

    def work(path):
        rec = fp(path)
        with lock:
            out.write(json.dumps(rec) + "\n")
            n[0] += 1
            if n[0] % 500 == 0:
                out.flush()
                print(f"  {n[0]}/{len(paths)}", flush=True)

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(work, paths))
    out.flush()
    out.close()
    empties = sum(1 for line in open(OUT) if json.loads(line)["fp"] == [])
    print(f"done · {n[0]} new · {empties} unfingerprintable (corrupt/short)", flush=True)


if __name__ == "__main__":
    main()
