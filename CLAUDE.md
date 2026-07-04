# music-ops

Monorepo of tools that run the operations side of a solo music career — content
production, DJ library intelligence, and bookings. Personal use, single Mac,
pragmatic over fancy. Full spec lives in `music-ops-brief.md`; this file is the
operating summary.

## Layout

```
clipper/      # content engine: set recordings → short clips (Python CLI)
librarian/    # DJ library organiser + intelligence (Python CLI + local web app)
releases/     # FL Studio projects → ship schedule + beat-pack player (Python CLI + web app)
outreach/     # booking CRM (Python CLI + SQLite)
site/         # EPK / music site (Astro static export → Vercel)
```

## Current state (2026-07-02) — read this first

All five tools have a working, tested core. Open work and recent history live in
`TODO.md`; this session's notes/gotchas live in the project memory (auto-loaded).

- **Tests are green:** librarian 225, clipper 199, releases 188, outreach 48.
  There is **no system `python`** — each tool has its own `.venv`; run tests with
  `cd <tool> && .venv/bin/python -m pytest -q`.
- **Biggest recent change:** the librarian *organisation overhaul* (identity →
  dedup v2 → classify v2 → output engine, `librarian organise`), merged to `main`.
- **Foot-guns (don't trip on these):**
  - librarian has **two** similarly-named module families — `organise.py` /
    `dedup_v2.py` / `classify_v2.py` (the NEW engine behind `librarian organise`)
    vs `organize.py` / `dedupe.py` (the OLDER AI rule-based `librarian organize`
    and metadata-dedup web path). Check which one you mean before editing.
  - librarian is **plans-only on `~/DJ`**: the real library (`~/DJ/library` ≈ 10k
    files) is never auto-applied to. Every run writes reviewable plans; a human
    runs `--apply`.
  - The ~19 `run_*.py` at the librarian root are one-off drivers (library was built
    with them), dry-run / `APPLY=1`-gated — not part of the CLI.
  - The classify_v2 *live* Anthropic path has no automated test (all tests inject a
    fake call); verify the model id + `output_config` against the installed SDK
    before any paid classify run.

## Stack & conventions

- Python 3.12+ for all CLI tools; one `pyproject.toml` per tool. (librarian's `.venv`
  currently runs 3.13; `requires-python` is `>=3.12`.)
- CLIs built with **Typer**; every command must have clear `--help`.
- **pytest** for core logic (not exhaustive — focus on the engines:
  clipper segment selection, librarian plan/undo, outreach pipeline).
- Media work: ffmpeg + librosa (clipper), mutagen + pyrekordbox (librarian),
  Pillow (artwork). Never re-encode more than needed; analyse the audio track
  only on long files, then cut the video.
- `site/` keeps all content in markdown/JSON so it can be updated without
  touching components. Dark editorial aesthetic; this is the music-side
  property, separate from the tech portfolio.
- AI features (ambition tier) use the Anthropic API with model
  `claude-sonnet-4-6`, demand JSON output, sit behind feature flags, and
  degrade gracefully when no API key is present.

## Build order (the ORIGINAL plan — most cores are now built; see "Current state" + `TODO.md`)

1. `clipper` — analyze + cut + manifest end-to-end on a real set recording
2. `clipper` — overlay + artwork commands
3. `librarian` — plan/apply/undo engine FIRST, proven on a COPY of a small
   library subset before ever touching the real library; then `cleanup`, then
   `inbox`, then the web app, then camelot/path/setplan/usb
4. `site` — built and deployed, press kit downloadable
5. `outreach` — schema + pipeline + drafting
6. `releases` — added after the original plan: FL-project ship schedule + beat-pack
   / turntable / reel web player + Demucs stem separation (all built)
7. Subagents only once a task is clearly recurring — never up front

Ambition-tier features (Whisper captions, Claude clip ranking, auto-tagging,
set recommender, outreach research, studio dashboard) come ONLY after the core
of each tool runs. Ship the boring core first.

## Safety invariants — `librarian` (non-negotiable: this tool writes to the music library)

- **Dry-run by default.** Every operation produces a reviewable plan
  (old → new for each move/rename/tag change, with the reason). Only
  `--apply` executes a reviewed plan.
- **Backup before first apply** of any run (or run in copy mode, leaving
  originals untouched).
- **Undo journal.** Every applied change is recorded; `librarian undo <run-id>`
  reverses a run completely.
- **Nothing is ever deleted.** Duplicates and rejects go to quarantine, keeping
  the highest-quality copy.
- **rekordbox cue safety.** If files rekordbox tracks are moved or renamed,
  emit an updated rekordbox XML with the new paths so playlists, hot cues, and
  memory cues survive. Never leave rekordbox pointing at dead paths.
- **Trust existing rekordbox analysis** (via pyrekordbox) for BPM/key where
  present; librosa/essentia only as fallback. Flag low-confidence key
  detections — never guess. _(Intended rule for when BPM/key handling lands — no
  current command reads BPM/key yet; the wiring is an open item in `TODO.md`.)_
- **Never download music.** Low-quality files (< 320kbps mp3 / non-lossless)
  are flagged for manual re-acquisition.
- pytest must cover the plan engine, the undo journal, and the never-delete
  invariant.

## Hard rule — `outreach`

Drafts and tracks only — **it never sends email**. Every draft goes to a file
for manual review and sending. Personalised, researched outreach only; no bulk
blasting. (The ambition-tier research step also only enriches drafts.)
