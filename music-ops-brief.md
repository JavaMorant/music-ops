# Music Ops — Build Brief for Claude Code

## What we're building
A **music-ops monorepo**: a set of tools that run the operations side of my music
career — content production, DJ library intelligence, and bookings — so my time
goes into actually making music. Solo use, my machine (Mac), pragmatic over fancy.

Repo layout:
```
music-ops/
  clipper/      # content engine (Python CLI)
  librarian/    # DJ library tools (Python CLI)
  outreach/     # booking CRM (Python CLI + SQLite)
  site/         # EPK / music site (deployed to Vercel)
```

**Working principle:** same as my other projects — build in the order at the
bottom, finish each tool solidly before the next, commit after each working slice.

Shared standards: Python 3.12, Typer for CLIs, `pyproject.toml` per tool, pytest
for core logic, clear `--help` on every command.

---

## Tool 1 — `clipper` (content engine) — BUILD FIRST
Turns long set recordings into ready-to-post short clips, as a batch job.

Commands:
- `clipper analyze <video/audio>` — use librosa (RMS energy + onset strength) to
  find the highest-energy moments; print a ranked timeline of candidate segments
  with timestamps.
- `clipper cut <video> [--clips N] [--len 15..60] [--spacing 60s]` — ffmpeg-cut the
  top N segments (min spacing between them so clips aren't near-duplicates),
  crop/format to 9:16 (centre crop with optional manual x-offset), normalise
  loudness, output to `out/<date>/`.
- `clipper overlay` — optional text overlay template (event name, date, artist)
  burned in via ffmpeg drawtext; keep templates configurable in a small TOML file.
- `clipper artwork <image>` — Pillow batch-resize one artwork into every platform
  spec (square, story 9:16, banner, profile) in one go.
- Every run writes a `manifest.csv` (clip path, source timestamp, duration) plus a
  `captions.md` stub I fill in before posting.

Notes: handle long files efficiently (analyse audio track only, then cut video);
fail gracefully on weird codecs; never re-encode more than needed.

---

## Tool 2 — `librarian` (DJ library organiser + intelligence)
Mission: one **deep clean** of my existing library this summer, then a permanent
**inbox pipeline** so every new file gets organised automatically and I never have
to do this again.

### Safety model (non-negotiable — this tool WRITES, so it must be reversible)
- Every operation is a **dry-run by default**: it produces a reviewable plan
  (old → new for every move, rename, and tag change, with the reason).
- `--apply` executes a reviewed plan. Before the first apply of any run, take a
  full backup (or run in copy mode, leaving originals untouched).
- Every applied change is recorded in an **undo journal**; `librarian undo
  <run-id>` reverses a run completely.
- **Nothing is ever deleted.** Duplicates and rejects go to a quarantine folder.
- **rekordbox cue safety:** if files rekordbox already tracks get moved or renamed,
  emit an updated rekordbox XML with the new paths so playlists, hot cues, and
  memory cues survive. Never leave rekordbox pointing at dead paths.
- The tool never downloads music; low-quality files are flagged for me to
  re-acquire properly.

### Mode A — `librarian cleanup <library-root>` (the one-time summer job)
- Scan everything; analyse BPM / key / duration / bitrate. **Trust existing
  rekordbox analysis where present** (parse via pyrekordbox); use librosa/essentia
  only as fallback, and flag low-confidence key detections instead of guessing.
- Normalise tags with mutagen (artist, title, genre, key, BPM written to tags) and
  filenames to a consistent `Artist - Title` convention.
- Dedupe: exact (file hash) + fuzzy (metadata similarity, optional chromaprint
  audio fingerprint) → quarantine, keeping the highest-quality copy.
- Flag low-bitrate files (< 320kbps mp3 / non-lossless) in the report.
- Reorganise into a canonical folder structure (configurable; default `Genre/`).
- Output: cleaned library + updated rekordbox XML + a summary report.

### Mode B — `librarian inbox` (the forever pipeline)
A watched **Inbox/** folder: any file dropped in is analysed, tagged, renamed,
deduped against the library, filed into the structure, and added to a
"New This Week" playlist in the generated rekordbox XML. This is what stops the
library ever rotting again.

### The application
A **local web app** wrapping the same engine — FastAPI backend + a simple React
front: drag-and-drop files in, see the proposed plan as a review table (old → new
per file, with reasons), tick/untick rows, hit Apply, with Undo per run. The CLI
and the app share one core library so there's a single source of truth.

### Intelligence commands (on top of the organised library)
- `librarian report` — coverage: missing key/BPM, genre/BPM distribution.
- `librarian camelot` — map keys to the Camelot wheel; flag unreliable keys.
- `librarian path --from <track> --to <track> [--bpm-tolerance 6%]` — harmonic
  mixing route between two tracks (graph search over key compatibility + BPM),
  e.g. amapiano at 112 into UK garage at 132 via intermediate steps.
- `librarian setplan --playlist <name> --curve warmup|peak|journey --mins 90` —
  order a crate to an energy curve, respecting key/BPM transitions; printable plan
  + importable playlist file.
- `librarian usb <playlist> <dest>` — copy a playlist into a clean USB structure.

Tests: pytest on the plan engine, the undo journal, and the never-delete invariant.

---

## Tool 3 — `site` (EPK / music site)
A fast static site — Astro or Next.js (static export), deployed on Vercel. This is
the **music-side property** (separate from my tech portfolio): dark editorial
aesthetic consistent with my brand.

Sections: bio, press photos, embedded mixes (SoundCloud/Spotify embeds), highlight
reel from my events, selected stats, upcoming/past dates, **booking contact**
(form or mailto), and a downloadable press kit (zip: bio, photos, logos, tech
rider placeholder).

Keep content in markdown/JSON so I can update it without touching components.

---

## Tool 4 — `outreach` (booking CRM)
A small CLI + SQLite pipeline for booking outreach. **Hard rule: this tool drafts
and tracks — it never sends. I review and send every email myself.** Personal,
researched outreach only; no bulk blasting.

- Schema: contacts(name, org, role, city, venue_capacity, genre_fit, source,
  notes) + pipeline(contact_id, stage 'lead'|'contacted'|'replied'|'negotiating'|
  'booked'|'dead', last_touch, next_followup).
- `outreach add / import <csv> / list [--stage] / due` — manage the pipeline;
  `due` shows follow-ups owed.
- `outreach draft <contact>` — generate a personalised email draft from the
  contact's data + my notes + a template (templates in markdown with slots);
  output to a file for me to edit and send. Always reference something specific
  (their venue, their nights, why my sound fits).
- `outreach log <contact> <note>` — record touches; auto-set next follow-up.
- Export the pipeline to CSV for backup.

---

## Subagents (add later, once tasks recur — not up front)
- **clip-runner**: runs the clipper pipeline on new footage and reports back just
  the manifest summary (keeps verbose ffmpeg output out of the main context).
- **librarian**: read-only agent for library queries ("plan me a 60-min peak set
  from the amapiano crate").
- **outreach-drafter**: tools restricted to the outreach folder only; drafts
  follow-ups for everything `due`.

---

## Build order
1. `clipper` — analyze + cut + manifest working end-to-end on a real set recording
2. `clipper` overlay + artwork commands
3. `librarian` — plan/apply/undo engine first, proven on a COPY of a small library
   subset before ever touching the real one; then `cleanup`, then `inbox`, then the
   web app, then camelot/path/setplan/usb
4. `site` — built and deployed, press kit downloadable
5. `outreach` — schema + pipeline + drafting
6. Subagents only when a task is clearly recurring

Finish each tool solidly before the next.

---

## AMBITION TIER — build ONLY after the core of each tool runs
These are stretch goals that play to a long-running, self-verifying model. **Rule
that still holds: ship the boring core first.** Do not let these block a working
MVP — they're bonuses layered on top, each behind a feature flag. Several use the
Anthropic API (Claude inside the tools); pass model `claude-sonnet-4-6` for cost,
demand JSON output, and degrade gracefully if the key is missing.

**Clipper — make it think about the footage:**
- Whisper (faster-whisper) transcription of the set → auto-burned captions, and
  auto-detected talk/hype moments as extra clip candidates.
- For each candidate clip, a Claude call that writes a caption + platform-aware
  hashtags + a one-line "why this clip works" rationale → into `captions.md`.
- Beat-aware cutting: detect phrase/bar boundaries (librosa beat track) so clips
  start and end musically, not mid-phrase.
- Optional: rank the candidate clips with a Claude call describing each, so the
  top N are genuinely the most postable, not just the loudest.

**Librarian — make it understand the music:**
- Auto genre/mood/energy tagging from audio features (tempo, spectral, MFCC) so
  the whole library gets consistent tags it never had.
- A set recommender: "90-min peak-time set, amapiano into UK garage, my energy
  curve" → a full ordered set from the library, key- and BPM-aware.
- A weekly library-health digest (new additions, untagged tracks, gaps by genre).

**Outreach — make it research before it drafts:**
- A web-research step that enriches a promoter/venue contact (recent events,
  capacity, who they book) before drafting, so the outreach references something
  real and specific. Still drafts only — never sends.

**Unifying — a `studio` dashboard (web):**
- One local dashboard tying it together: releases pipeline, content performance
  (which clips converted), library health, outreach pipeline. The single pane of
  glass for the whole music operation. Reuses the FastAPI patterns from Summer Log.

Build order for the ambition tier: clipper-AI → librarian-AI → outreach-research →
studio dashboard. Each is a self-contained, well-specified task — ideal to hand to
a long autonomous session, then review the diff.
