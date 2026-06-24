"""Clean Artist/Title tags into a consistent format — reviewable, undoable.

Conservative by design: it strips download noise (Official Video / [Audio] /
(Lyrics) / emoji / 320kbps / uploader handles / copy-markers / trailing .mp3),
fills in a MISSING artist by splitting "Artist - Title", and drops a duplicated
artist prefix from the title — but it NEVER overwrites an artist you already have
and it never renames a file.

  .venv/bin/python run_tagclean.py            # DRY RUN — prints old -> new, writes nothing
  APPLY=1 .venv/bin/python run_tagclean.py     # write tags (journaled)

Undo:  librarian undo <run-id> --runs-dir ~/DJ/library/.librarian/runs
       (or the runs panel in the review app)
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from librarian import retag, tagclean  # noqa: E402
from librarian import tags as T  # noqa: E402
from librarian.engine import apply_plan  # noqa: E402
from librarian.journal import DONE  # noqa: E402

LIB = Path.home() / "DJ" / "library"
RUNS = LIB / ".librarian" / "runs"   # same place `librarian serve` keeps journals


def main() -> None:
    apply = os.environ.get("APPLY") == "1"
    print(f"=== tag cleanup ({'APPLY' if apply else 'DRY RUN'}) — {LIB} ===")
    plan, _report = retag.build_retag_plan(LIB, tagclean.build_proposals(LIB))
    edits = plan.tag_edits
    title_only = sum(1 for e in edits if set(e.fields) == {"title"})
    fill_artist = sum(1 for e in edits if "artist" in e.fields)
    print(f"{len(edits)} files to clean · {title_only} title-only · {fill_artist} fill a missing artist\n")

    for e in edits[:30]:
        cur = T.read_tags(e.path, ("artist", "title"))
        print("  " + " · ".join(f"{f}: {cur.get(f)!r}→{v!r}" for f, v in e.fields.items()))
    if len(edits) > 30:
        print(f"  … +{len(edits) - 30} more")

    if not edits:
        print("\nNothing to change — tags already consistent.")
        return
    if apply:
        journal = apply_plan(plan, RUNS, backup=False)   # reversible via the journal itself
        done = sum(1 for t in journal.tag_edits if t.status == DONE)
        print(f"\nAPPLIED: {done} files retagged · run {journal.run_id}")
        print(f"Undo:    librarian undo {journal.run_id} --runs-dir {RUNS}")
        print("Then in rekordbox: select all → right-click → Reload Tag, to pull the cleaned tags in.")
    else:
        print("\n(dry run — APPLY=1 to write, fully undoable)")


if __name__ == "__main__":
    main()
