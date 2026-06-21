"""Seamless rekordbox sync — mirror the .m3u playlists into rekordbox AND rate
tracks by how often you play them off your sticks. No more manual re-importing.

  python run_rb_sync.py            # DRY RUN (safe; rekordbox can stay open)
  APPLY=1 python run_rb_sync.py     # WRITE to master.db  — QUIT REKORDBOX FIRST
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from pyrekordbox import Rekordbox6Database  # noqa: E402
from librarian import rekordbox_sync as rb  # noqa: E402


def main():
    apply = os.environ.get("APPLY") == "1"
    if apply and rb.rekordbox_running():
        print("✗ rekordbox is OPEN — quit it first, then re-run. (Nothing written.)")
        return
    db = Rekordbox6Database()
    print(f"=== rekordbox sync ({'APPLY' if apply else 'dry run'}) ===")
    print(f"rekordbox running: {rb.rekordbox_running()}\n")

    pls = rb.sync_playlists(db, Path.home() / "DJ" / "library" / "_Playlists", dry_run=not apply)
    matched = sum(p["matched"] for p in pls)
    total = sum(p["tracks"] for p in pls)
    print(f"PLAYLISTS: {len(pls)} · {matched}/{total} tracks matched to collection")
    for p in sorted(pls, key=lambda x: -x["matched"])[:6]:
        print(f"  {p['matched']:4d}/{p['tracks']:<4d}  {p['name']}")

    rt = rb.auto_rate(db, dry_run=not apply)
    print(f"\nRATINGS: {rt['total']} tracks would change -> {rt['by_stars']}")
    for e in rt["examples"][:8]:
        print(f"  {'★' * e['stars']:<5} ({e['plays']}×)  {e['track'][:46]}")

    print("\nAPPLIED." if apply else "\n(dry run — quit rekordbox, then: APPLY=1 .venv/bin/python run_rb_sync.py)")


if __name__ == "__main__":
    main()
