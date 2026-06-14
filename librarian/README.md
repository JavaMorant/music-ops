# librarian

A reversible organiser for the DJ music library. It **plans** changes, lets you
review them, **applies** only what you approve, and can **undo** any run
completely. It writes to the music library, so every operation is built to be
safe and reversible first, useful second.

> Status: the **plan / apply / undo engine**, the one-time **`cleanup`** scan,
> and the **`inbox`** forever-pipeline are built, tested (60 pytest tests), and
> proven on a copy of the testbed. Not yet built: tag *writing*,
> fuzzy/fingerprint dedupe, rekordbox **database** analysis (BPM/key via
> pyrekordbox), and the web app.

## Safety model (non-negotiable)

- **Dry-run by default.** `plan` and `cleanup` only ever produce a reviewable
  plan (old → new for every move, with a reason) plus a report. They change
  nothing on disk.
- **Nothing is ever deleted.** Duplicates and rejects are *moved* to a
  `_quarantine/` folder, keeping the highest-quality copy. The engine contains
  no delete primitive at all.
- **Never clobber.** A move that would overwrite an existing file, cross a
  volume (non-atomic), land on a directory, or collide on a case-insensitive
  name is refused up front — the whole plan, all-or-nothing.
- **Journal before execute.** A run is written to disk (and fsync'd) *before*
  the first file moves, and re-flushed after every move, so an interrupted run
  is always recoverable.
- **Backup before apply.** `apply` copies every at-risk file into the run's
  backup directory before anything moves (or the whole library with
  `--full-backup`). Backups live outside the library.
- **rekordbox cue safety.** When you pass an exported rekordbox XML, moves
  rewrite the track `Location`s — in the collection *and* in location-keyed
  playlists — so playlists, hot cues, memory cues and beatgrids survive. A
  quarantined duplicate's cues are repointed to the copy that's kept. The
  original XML is backed up and restored byte-for-byte on undo.

## Install

```bash
cd librarian
python3.12 -m venv .venv            # or your 3.12+ interpreter
.venv/bin/pip install -e ".[dev]"
.venv/bin/librarian --help
```

## Commands

| Command | What it does |
|---|---|
| `librarian plan <root>` | Minimal plan: strip download-junk filenames, quarantine copy-markers. Dry-run. |
| `librarian cleanup <root>` | Full deep-clean plan: exact-dup → quarantine, rename to `Artist - Title`, refile into `Genre/`, + a report. Dry-run. |
| `librarian inbox <root>` | Drain `<root>/Inbox`: dedupe new drops against the library, file them, add them to rekordbox + a "New This Week" playlist. Dry-run. |
| `librarian apply <plan.json>` | Execute a reviewed plan, journaled + backed up. |
| `librarian undo <run-id>` | Reverse a run completely (files + rekordbox XML). |
| `librarian runs` | List apply runs and their status. |

Every command has `--help`.

### `inbox` — the forever pipeline (Mode B)

Drop new tracks into `<library-root>/Inbox/`, then:

```bash
librarian inbox ~/path/to/library --rekordbox-xml ~/rekordbox_export.xml
# review plan.json + inbox-report.md
librarian apply plan.json
librarian undo <run-id>   # if needed
```

Each dropped file is deduped against the existing library (exact content hash)
and against the rest of the drop — duplicates go to quarantine (the kept copy's
cues are redirected to it), never deleted. Genuinely new tracks are renamed to
`Artist - Title`, filed into `Genre/`, **added** to the rekordbox collection, and
appended to a TrackID-keyed "New This Week" playlist (`--playlist` to rename it).
`Tonality` is written only when the file is actually tagged with a key — never
guessed. Low-bitrate / missing-key / missing-tag files are still filed and
flagged in the report for re-acquisition.

This is a **one-shot batch** run on demand, not a background daemon: a watcher
can't be dry-run/reviewed, which the safety model requires. The Inbox must live
**inside** the library root and on the **same volume** (so moves are atomic
renames and stay contained); `inbox` validates this up front.

### Typical flow

```bash
# 1. Review what cleanup proposes (writes plan.json + cleanup-report.md, no changes)
librarian cleanup ~/path/to/library

# 2. Read plan.json and cleanup-report.md. Then apply (backs up first)
librarian apply plan.json

#    -> prints a run id, e.g. 20260614-035312-ce39e1

# 3. Changed your mind? Reverse it completely
librarian undo 20260614-035312-ce39e1
```

`--runs-dir <dir>` controls where journals + backups go (default `.librarian/runs`).
Keep it **outside** the library.

## rekordbox cue safety — dry-run trial first

Before trusting the path rewrite on your real collection, do a dry run on a copy.

1. In rekordbox: **File → Export Collection in xml format** → e.g.
   `~/rekordbox_export.xml`. (The internal rekordbox database is *not* this file;
   you must export.)
2. Work on a **copy** of a library subset, never the original:
   ```bash
   cp -R "~/real/subset" /tmp/lib-copy
   librarian cleanup /tmp/lib-copy --rekordbox-xml ~/rekordbox_export.xml
   ```
3. Read `plan.json` + `cleanup-report.md`. Then, still on the copy:
   ```bash
   librarian apply plan.json --runs-dir /tmp/runs
   # inspect the rewritten XML, then reverse it:
   librarian undo <run-id> --runs-dir /tmp/runs
   ```

The automated equivalent lives in `tests/test_rekordbox_trial.py`, which drives a
realistic exported collection (beatgrids, hot/memory/loop cues, TrackID- and
location-keyed playlists in nested folders, literal-space and percent-encoded
Locations, accented/NFD filenames) through cleanup → apply → undo and asserts
cues/playlists survive and only moved tracks change.

> Only `Location` (and location-keyed playlist `Key`) attributes are rewritten.
> Beatgrids, cues and TrackID-keyed playlists are path-independent and are left
> untouched. Matching is case- and Unicode-normalisation-insensitive (handles
> macOS NFD vs NFC).

## Testing

```bash
.venv/bin/python -m pytest -q
```

The suite covers the engine round-trip (byte-for-byte on a copy of the real
testbed), the never-delete / never-clobber invariants, journal-before-execute and
crash recovery, the backup safeguard, the cleanup planner (dedupe / rename /
refile / report), the inbox pipeline (dedupe-against-library, rekordbox add +
playlist, byte-for-byte inbox round-trip), adversarial path sanitisation, and the
rekordbox trials.

## What this is **not** (yet)

- It does **not** write tags into files (byte-level mutation is a separate,
  carefully-reversible slice). The report flags missing key / tags / low bitrate;
  it never guesses or downloads.
- Dedupe is **exact** (byte-identical) + a copy-marker heuristic. Fuzzy metadata
  / audio-fingerprint dedupe is not built.
- BPM/key come from existing tags only; rekordbox **database** analysis via
  pyrekordbox and librosa fallback are not wired up.
- `inbox` is a one-shot batch (run on demand), not a live watch daemon. No web
  app yet.

Always run `cleanup`/`plan` (dry-run) and read the plan before `apply`, and keep
`apply` on a copy until you've trialled it against your own rekordbox export.
