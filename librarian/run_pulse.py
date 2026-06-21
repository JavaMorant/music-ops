"""Thin runner for librarian.pulse — library intelligence over rekordbox.

  python run_pulse.py [--out DIR] [--last N] [--recs N]
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from pyrekordbox import Rekordbox6Database  # noqa: E402
from librarian import pulse  # noqa: E402


def main():
    args = sys.argv[1:]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else Path.home() / "DJ" / "library" / "_Playlists"
    last = int(args[args.index("--last") + 1]) if "--last" in args else 15
    recs = int(args[args.index("--recs") + 1]) if "--recs" in args else 10
    db = Rekordbox6Database()
    report = pulse.run_pulse(db, runs_dir=Path.home() / "DJ" / ".librarian-runs",
                             out_dir=out, last_n=last, recommend=recs)
    print("report written:", report)
    print(report.read_text()[:1400])


if __name__ == "__main__":
    main()
