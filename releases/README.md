# releases

Turn ~150 idle FL Studio projects into a shipping schedule.

`releases` scans `~/ProducerLibrary/projects` **read-only**, infers each
project's stage from the existing folder taxonomy, parses BPM/key/genre from
filenames, ranks the ones nearest done, and plans a single-every-N-weeks
calendar toward an EP deadline.

> **Read-only on the library.** The scan never moves, renames, or writes
> anything under `ProducerLibrary`. All state (the index, the schedule, the
> status log) lives in a separate SQLite db — default `~/.releases/releases.db`,
> never inside the library.

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
