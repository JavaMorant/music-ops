"""Refresh the library fingerprint index over the CURRENT library (~8k tracks),
so ingest dedupe-vs-library is airtight (the old index only covered the first
7,459). Resumable, parallel. Writes library-fingerprints.jsonl.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import subprocess
import threading
from pathlib import Path

LIB = Path.home() / "DJ" / "library"
OUT = "/tmp/lib-dedupe/library-fingerprints.jsonl"
AUDIO = {".mp3", ".m4a", ".flac", ".wav", ".aiff", ".ogg", ".aac", ".opus"}


def fp(path):
    try:
        d = json.loads(subprocess.run(["fpcalc", "-raw", "-json", "-length", "120", path],
                                      capture_output=True, text=True, timeout=90).stdout)
        return {"p": path, "d": d.get("duration", 0.0), "fp": d.get("fingerprint", [])}
    except Exception:
        return {"p": path, "d": 0.0, "fp": []}


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                done.add(json.loads(line)["p"])
            except Exception:
                pass
    files = [str(p) for p in LIB.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO
             and "_Playlists" not in p.parts and str(p) not in done]
    print(f"{len(files)} to fingerprint ({len(done)} already done)", flush=True)
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
                print(f"  {n[0]}/{len(files)}", flush=True)

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(work, files))
    out.flush(); out.close()
    print(f"done · {n[0]} fingerprinted -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
