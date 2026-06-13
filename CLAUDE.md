# music-ops

Monorepo of tools that run the operations side of a solo music career — content
production, DJ library intelligence, and bookings. Personal use, single Mac,
pragmatic over fancy. Full spec lives in `music-ops-brief.md`; this file is the
operating summary.

## Layout

```
clipper/      # content engine: set recordings → short clips (Python CLI)
librarian/    # DJ library organiser + intelligence (Python CLI + local web app)
outreach/     # booking CRM (Python CLI + SQLite)
site/         # EPK / music site (Astro or Next.js static export → Vercel)
```

## Stack & conventions

- Python 3.12 for all CLI tools; one `pyproject.toml` per tool.
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

## Build order (finish each tool solidly before the next; commit after each working slice)

1. `clipper` — analyze + cut + manifest end-to-end on a real set recording
2. `clipper` — overlay + artwork commands
3. `librarian` — plan/apply/undo engine FIRST, proven on a COPY of a small
   library subset before ever touching the real library; then `cleanup`, then
   `inbox`, then the web app, then camelot/path/setplan/usb
4. `site` — built and deployed, press kit downloadable
5. `outreach` — schema + pipeline + drafting
6. Subagents only once a task is clearly recurring — never up front

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
  detections — never guess.
- **Never download music.** Low-quality files (< 320kbps mp3 / non-lossless)
  are flagged for manual re-acquisition.
- pytest must cover the plan engine, the undo journal, and the never-delete
  invariant.

## Hard rule — `outreach`

Drafts and tracks only — **it never sends email**. Every draft goes to a file
for manual review and sending. Personalised, researched outreach only; no bulk
blasting. (The ambition-tier research step also only enriches drafts.)
