# librarian

A reversible organiser for the DJ music library. It **plans** changes, lets you
review them, **applies** only what you approve, and can **undo** any run
completely. It writes to the music library, so every operation is built to be
safe and reversible first, useful second.

> Status: the **plan / apply / undo engine**, the one-time **`cleanup`** scan,
> the **`inbox`** forever-pipeline, the local **review web app** (`serve`), the
> AI **`organize`** front-door, reversible AI **`retag`** (tag repair), and the
> newer **`organise`** engine — AcoustID/chromaprint fingerprint **identity** →
> recording-level **`dedup_v2`** → AI **`classify_v2`** genres → reviewable plans,
> plus guest-USB `.m3u8` export — are built and tested (215 passing). So
> audio-fingerprint / fuzzy dedupe **is** built (via `organise`). Not yet built:
> reading rekordbox **database** BPM/key (pyrekordbox is used only for read-only
> playlist lookup today) and a librosa/essentia key/BPM fallback.

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

## Try it on the testbed

Exercise the whole engine safely on the disposable sample library at
`~/dev/librarian-testbed/sample-library` — **never** the real `~/Music` library.
`cleanup` is dry-run (no changes); `apply` backs up first and is fully reversible
with `undo`, so nothing here is destructive.

**On the command line** — dry-run → apply → undo:

```bash
LIB=~/dev/librarian-testbed/sample-library
.venv/bin/librarian cleanup "$LIB"                              # dry-run: writes plan.json + cleanup-report.md
.venv/bin/librarian apply plan.json --runs-dir /tmp/lib-runs    # backs up first, journals every move
.venv/bin/librarian runs --runs-dir /tmp/lib-runs               # find the run id
.venv/bin/librarian undo <run-id> --runs-dir /tmp/lib-runs      # reverse it completely
```

**In the browser** — the same engine with a visual review table:

```bash
.venv/bin/librarian serve "$LIB" --runs-dir /tmp/lib-runs
# open http://127.0.0.1:8765 — pick a mode, Scan, untick rows you don't want, Apply, Undo
```

Keep `--runs-dir` outside the library (here, `/tmp`), and keep `apply` on the
testbed until you've trialled it against your own rekordbox export (below).

## Commands

| Command | What it does |
|---|---|
| `librarian plan <root>` | Minimal plan: strip download-junk filenames, quarantine copy-markers. Dry-run. |
| `librarian cleanup <root>` | Full deep-clean plan: exact-dup → quarantine, rename to `Artist - Title`, refile into `Genre/`, + a report. Dry-run. |
| `librarian organize <root> "…"` | Plan from a plain-English instruction (AI turns it into rules; the engine applies them). Dry-run. |
| `librarian organise <root>` | **Different engine** (British spelling): fingerprint-identify (AcoustID) → recording-level dedup → AI genre classify → reviewable dedup + reorg plans (+ guest-USB `.m3u8` when pointed at a stick). Dry-run; `--apply` gated. |
| `librarian retag <root> ["…"]` | Repair messy/missing artist/title/genre tags from filenames (AI proposes; you review). Reversible. Dry-run. |
| `librarian inbox <root>` | Drain `<root>/Inbox`: dedupe new drops against the library, file them, add them to rekordbox + a "New This Week" playlist. Dry-run. |
| `librarian apply <plan.json>` | Execute a reviewed plan, journaled + backed up. |
| `librarian undo <run-id>` | Reverse a run completely (files + rekordbox XML). |
| `librarian runs` | List apply runs and their status. |
| `librarian serve <root>` | Run the local review web app over this library (127.0.0.1). |

Every command has `--help`.

> **`organise` vs `organize` — two different engines, mind the spelling.**
> `organise` (British) is the newer four-stage engine — `organise.py` +
> `dedup_v2.py` + `classify_v2.py` + `identity.py` (AcoustID fingerprinting).
> `organize` (American) is the older AI plain-English rule-set path —
> `organize.py` + `dedupe.py` (exact-match dedup, pyrekordbox playlist lookup).
> When editing, confirm which family you mean; the names are one letter apart.

### `organize` — describe it in plain English (AI)

One of the ambition-tier AI features (see also `retag` and `organise --classify`).
You describe how you want the library arranged
and Claude turns it into a small **rule-set**, which is applied deterministically
to produce the same reviewable plan as everything else. The AI only *authors
rules* — it never touches files, never sees the apply path, and never guesses
musical key/BPM. Every move still goes through the engine's preflight, journal and
undo, so all the safety invariants hold unchanged.

```bash
pip install -e '.[ai]'                 # one-time: install the anthropic SDK
export ANTHROPIC_API_KEY=sk-…          # your key — nothing runs without it

librarian organize ~/path/to/library \
  "put all Avicii in Festival/, quarantine the _spotdown rips, file the rest by genre"
# review plan.json + organize-report.md, then:  librarian apply plan.json
```

Rules match one field (`artist`, `title`, `genre`, `filename`, `extension`,
`quality`) with one op (`contains`, `equals`, `startswith`, `endswith`,
`is_low_quality`, `any`) and take one action (`folder`, `by_genre`,
`rename_artist_title`, `quarantine`); the first matching rule wins and the rest
fall to a default. Save the inferred rule-set with `--save-spec rules.json` and
replay it later with `--spec rules.json` — no API call, fully deterministic.

It degrades gracefully: with no key (or without the `.[ai]` extra) the command
says so and points you at `cleanup`, the non-AI deep-clean. In the web app the
same box appears at the top once AI is configured; otherwise it shows a hint.
*Privacy:* the AI step sends a sample of your filenames + the genres already in
the library to Anthropic, using your own key; nothing else leaves the machine.

### `retag` — repair tags from filenames (AI)

The one feature that writes *inside* your files — so it's built to be as reversible
as a move. Claude reads each file's name + current tags and proposes clean
**artist / title / genre**; you review every change and it's only written on
`apply`, with `undo` restoring the exact previous values (a tag that was absent is
deleted again). The AI never touches files, and **musical key/BPM are never
written** — they're not even in the writable set.

```bash
pip install -e '.[ai]' && export ANTHROPIC_API_KEY=sk-…   # same as organize
librarian retag ~/path/to/library
#   …or with guidance:
librarian retag ~/path/to/library "set genre to Amapiano for the SA artists"
# review plan.json + retag-report.md (old → new per field, with confidence), then:
librarian apply plan.json      # writes the tags
librarian undo  <run-id>       # restores them exactly
```

Only writable identity fields change, and only where the new value actually
differs from the current one. `--limit N` caps how many files go to the AI per run
(untagged first); `--save-spec props.json` saves the proposals so you can replay
them with `--spec props.json` — no API call, fully deterministic and reviewable.
The pre-apply backup snapshots every retagged file too, so there are two
independent ways back (the journaled old values, and the file backup).
*Privacy:* the AI step sends each file's filename + current artist/title/genre
tags to Anthropic, using your own key; nothing else leaves the machine.

### `serve` — the local review web app

A FastAPI + single-page UI wrapping the **same** engine, for reviewing plans
visually instead of reading `plan.json`:

```bash
pip install -e ".[web]"          # one-time: install the web extra
librarian serve ~/path/to/library --rekordbox-xml ~/rekordbox_export.xml
# open http://127.0.0.1:8765
```

Pick a mode (`plan` / `cleanup` / `inbox`), hit **Scan** to see the proposed
changes as a review table (old → new per row, with the reason), **untick** any
rows you don't want, then **Apply** — and **Undo** any run from the runs list. In
`inbox` mode you can drag-and-drop files into the page to stage them in `Inbox/`.

Safety: the server binds **127.0.0.1 only**, the library root is fixed at launch
(no endpoint accepts a library path), the built plan is cached server-side and
applied by id (client input is only which rows to keep), mutating routes carry an
origin/host guard, and uploads are confined to the inbox. It is the same dry-run →
review → apply → undo flow as the CLI — the web layer adds no new way to mutate
the library. Unticking a row also drops that row's rekordbox addition/redirect, so
the collection never ends up referencing a track that didn't move.

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
playlist, byte-for-byte inbox round-trip), adversarial path sanitisation, the
rekordbox trials, the plan-subset filter (`select_actions`), and the web API
(dry-run, subset-apply drops an unticked import's rekordbox track, byte-for-byte
undo, origin guard, safe upload).

## What this is **not** (yet)

- Tag *writing* is limited to identity fields (artist/title/genre) via `retag`,
  and it's fully reversible. It never writes musical **key/BPM** (those are
  trusted, never guessed) and never downloads. The report still flags missing
  key / tags / low bitrate for manual attention.
- The `cleanup`/`inbox` path dedupes **exact** (byte-identical) + a copy-marker
  heuristic. Fuzzy / audio-fingerprint dedupe lives in the separate `organise`
  engine (AcoustID + recording-level `dedup_v2`).
- BPM/key come from existing tags only; reading rekordbox **database** BPM/key via
  pyrekordbox and a librosa fallback are not wired up (pyrekordbox is used only for
  read-only playlist lookup in `dedupe.py`).
- `inbox` is a one-shot batch (run on demand), not a live watch daemon.
- The web app is local-only (127.0.0.1), single-user; no auth token yet (origin/
  host guard + localhost bind only).

Always run `cleanup`/`plan` (dry-run) and read the plan before `apply`, and keep
`apply` on a copy until you've trialled it against your own rekordbox export.
