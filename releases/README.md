# releases

Turn ~150 idle FL Studio projects into a shipping schedule.

`releases` scans `~/ProducerLibrary/projects` **read-only**, infers each
project's stage from the existing folder taxonomy, parses BPM/key/genre from
filenames, ranks the ones nearest done, and plans a single-every-N-weeks
calendar toward an EP deadline.

> **The library is read-only by default.** Scanning, listing, planning, status,
> and release curation never touch `ProducerLibrary` — all that state lives in a
> separate SQLite db (default `~/.releases/releases.db`, refused if you point it
> inside the library). The **one** exception is `releases organize`, the opt-in
> on-disk folder mover: it is dry-run-first, journaled, never deletes or
> overwrites, and is fully reversible with `undo` (see below).

## Install

```bash
cd releases
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Commands

```bash
releases scan                                   # index the library (read-only)
releases list --closest                         # what's nearest done, ranked
releases list --stage complete --limit 20       # filter by stage
releases plan --cadence single/3w --target "EP by Aug 31"
releases status "Encara" scheduled --note "first single"
releases dashboard                              # counts, schedule, overdue

# curate a release (single/EP) by hand, then schedule it:
releases release new "Summer EP" --kind ep
releases release add "Summer EP" --top 3        # grab the 3 closest-to-done
releases release add "Summer EP" "Hong Tonky"   # or by name
releases release reorder "Summer EP" "Iswear"   # set the lead single
releases plan --release "Summer EP" --target "EP by Aug 31"
```

Every command takes `--db PATH` to point at a different index.

### `scan`
Walks the projects tree and upserts a row per project. Re-scanning is
idempotent and **preserves** any manual stage you set with `status`.

What counts as a project:
- **folder-project** — a folder with one or more `.flp` files (multiple `.flp`
  in one folder are treated as versions of a single project).
- **standalone `.flp`** — a loose `.flp` in a structural folder (root, a stage
  folder).
- **standalone bounce** — a loose render (`.mp3/.wav/…`) in a stage folder with
  no `.flp` beside it (the finished tracks in `Track List`).

Pruned as noise: `Backup/` autosaves, `Samples/`, and sample-pack / preset /
FL-template areas (`Drum Kits`, `Diva Presets`, `Loops`, …).

### `list --closest`
Ranks by **closeness to done** = stage weight (the dominant signal) + a bounce
bonus + a recency bonus (newer `.flp` mtime scores higher). A complete track
with a render that you touched recently floats to the top.

### `plan`
`--cadence "single/3w"` = one single every 3 weeks. Units: `d` days, `w` weeks,
`m` months. Singles land on **Fridays** (release-day convention), drawn from the
top of the ranking. With `--target "EP by Aug 31"` the run fills until the
deadline and caps with the EP; without a target, `--count N` sets how many
singles to schedule. The calendar is saved so `dashboard` can show it; pass
`--no-save` to preview only.

### `release …` — curate singles/EPs by hand

A **release** is a named container (single or EP) holding an ordered set of
chosen tracks (references to scanned projects). It's how you *bundle* tracks
toward a drop — entirely in the index, **no files are moved**.

```bash
releases release new [NAME] [--kind single|ep]   # NAME omitted → "Untitled N"
releases release ls                              # all releases + status + counts
releases release show <release>                  # ordered tracklist + readiness
releases release add <release> <query>... [--top N] [--at POS]
releases release move <track> --to <release> [--at POS]
releases release rm <release> <query>...
releases release reorder <release> <query>...    # unnamed tracks keep order after
releases release rename <release> <new-name> [--kind single|ep]
releases release ship <release>                  # mark released (cascades tracks)
releases release delete <release> [--force]      # container only; projects untouched
```

`<release>` resolves by id or unique name substring; `<query>` matches a project
the same way `status` does (ambiguous matches are listed, not guessed). `--top N`
bulk-adds the N closest-to-done projects not already in any release — the fast
way to fill a release. A track whose project later vanishes from a scan shows as
`MISSING` (its snapshot name is kept) rather than being silently dropped.

Schedule a curated release with `plan --release <name>`: its tracks lay onto the
Friday cadence **in your order** (not the closeness ranking), with the EP slot
now referencing the real member tracks. `--together` drops all tracks on the EP
date instead of as lead singles. Scheduling sets the release to `scheduled`;
`ship` sets it `released` and logs each track's transition.

### `organize …` — reorganize folders on disk (the one writer)

The only part of `releases` that writes to the library — and it borrows the
**librarian**'s safety engine: dry-run → review → apply → undo.

```bash
releases organize by-stage                      # propose filing each project into its stage's folder
releases organize file "sketchy" --to-stage complete
releases organize rename "sketchy" "Better Name"
releases organize apply organize-plan.json      # execute the reviewed plan (journaled)
releases organize undo <run-id>                 # reverse a run completely
releases organize runs                          # list applied runs
```

The workflow: triage stages cheaply in the index (`status`, or set them however
you like), then `organize by-stage` turns those decisions into a reviewable plan
of folder moves (old → new + reason). Nothing moves until you `apply` a plan.

Safety invariants (proven on a copy, never the real library during dev):
- **Dry-run by default** — `by-stage`/`file`/`rename` only print + write a plan.
- **Never deletes, never overwrites** — every action is an atomic same-volume
  rename; a move onto an existing path is refused (collisions are skipped, noted).
- **Containment** — sources and destinations must stay inside the library root.
- **Journaled + reversible** — each move is recorded (atomic, fsync'd) before and
  after it happens, so a crash is recoverable and `undo <run-id>` reverses the run.
- **Index stays linked** — after a move the project, its release memberships, and
  any scheduled slot are re-pointed to the new path; `undo` re-points them back.
  Run `releases scan` afterward to refresh inferred stages from the new locations.

### `status <project> <stage>`
Moves a project along the pipeline and logs the change. `<project>` is a
case-insensitive substring of the name or path (ambiguous matches are listed,
not guessed). Valid stages include the inferred ones plus `scheduled` and
`released`. This writes only to the index, never to the library.

### `dashboard`
Counts by stage, the closest-to-done five, the saved schedule, and anything
**overdue** (a single whose date has passed).

## Stages

Inferred from the taxonomy under `Beats/Tracks/`:

| stage | from folder | weight |
|---|---|---|
| `complete` | Complete Tracks | 95 |
| `track-list` | Track List | 88 |
| `remix` | Remixes In Progress | 62 |
| `need-arranged` | Need Arranged : Deep Progress | 52 |
| `return-to` | Return to (Potential nudged above "If really bored") | 40 |
| `uncategorized` | anything else | 36 |
| `mels` | Mels | 30 |
| `bones` | Project bones | 12 |

Plus two you set by hand: `scheduled` (85) and `released` (100, excluded from
"closest to done" and from future single slots).

## Tests

```bash
pytest -q
```

Covers stage/BPM/key/genre inference, the scan (grouping, pruning, bounce
detection, **never-writes-the-library**), scoring, calendar planning, and the
CLI end-to-end against a synthetic library. A real-library test runs if
`~/ProducerLibrary/projects` is present and skips otherwise.

## Ambition tier (not built yet)

`pip install -e '.[ai]'` reserves the Anthropic client for a flagged
"what this needs to ship" estimator (arrangement / mix / vocal checklist from a
project's bounce). Core ships first.
