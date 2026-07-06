# Music Ops — Extension Brief (release tracker, scheduler, sample finder, gig analytics, website)

Add these to the existing `music-ops` monorepo. They share its standards (Python 3.12,
Typer CLIs, pytest) and reuse the same SQLite/FastAPI patterns. The website is a separate
deploy. **Build order and discipline unchanged: the clipper ships first; these come after a
working clipper, one at a time. Several touch the same library/projects on disk — reuse the
librarian's read-only-by-default and propose-then-apply safety, never blind writes.**

New repo additions:
```
music-ops/
  releases/      # release tracker + pipeline (Python CLI + SQLite)
  scheduler/     # content scheduler + repurposer (Python CLI + SQLite)
  finder/        # sample/loop tagger + search (Python CLI, reads ProducerLibrary)
  gigs/          # set / gig analytics (Python CLI + SQLite)
  web/           # public website (separate Vercel deploy)
```

---

## Tool 5 — `releases` (release tracker + pipeline) — BUILD THIS ONE FIRST of the five
The highest-leverage tool: turns ~150 idle projects into a shipping schedule.
- **Ingest** the projects in `~/ProducerLibrary/projects` (read-only scan) into SQLite:
  name, stage (from the existing folder taxonomy: complete / need-arranged / return-to /
  remix / etc.), BPM/key if inferable from filename, last-modified, has-bounce?, genre guess.
- `releases list [--stage] [--closest]` — show projects; `--closest` ranks the ones nearest
  done (complete stage + recent + has a bounce) so I know what to finish next.
- `releases plan --cadence "single/3w" --target "EP by Aug 31"` — generate a release calendar
  from the closest-to-done projects, slotting singles toward the EP.
- `releases status <project> <stage>` — move a project along the pipeline; log the change.
- `releases dashboard` — counts by stage, what's scheduled, what's overdue.
- Optional (AI, flagged): given a project's stem/bounce, a Claude call estimating "what this
  needs to ship" (arrangement, mix, vocal) as a short checklist.

## Tool 6 — `scheduler` (content scheduler + repurposer)
The clipper makes clips; this decides what posts when, and multiplies one set into weeks.
- A content queue in SQLite: each item = a clip (from the clipper's `out/`) + caption +
  target platform(s) + scheduled date + status (queued / posted).
- `scheduler ingest <clipper-out-dir>` — pull new clips + their `captions.md` into the queue.
- `scheduler plan --per-week 4 --platforms tiktok,ig,youtube` — space posts across the week,
  spreading one event's clips over days so a single night becomes ~2 weeks of content.
- `scheduler repurpose <clip>` — generate caption + hashtag variations (Claude, flagged) so
  the same clip posts differently across platforms without looking duplicated.
- `scheduler due` — what to post today, with the file path and caption ready to copy.
- It **prepares** posts; I post manually (no auto-posting — platform ToS + my voice stays mine).

## Tool 7 — `finder` (sample/loop tagger + search)
Makes the organised `ProducerLibrary` instantly queryable mid-session.
- Scan `~/ProducerLibrary/Beats` (read-only), extract BPM/key/duration + audio features
  (spectral, MFCC) → SQLite index. Cache so re-scans are incremental.
- `finder search --bpm 110-115 --key "C#m" --type loop --vibe dark` — find matching samples.
- `finder fit <project>` — given a project's BPM/key, surface samples that fit it.
- `finder tag` — auto genre/mood/energy tags from features for the whole library.
- Keep it read-only on the library; it indexes and reports, never moves files (that's the
  librarian's job).

## Tool 8 — `gigs` (set / gig analytics)
Turns DJ sets into data that informs both sets and production.
- SQLite: gigs (date, venue, event), setlists (tracks played, order), notes (what landed).
- `gigs log` — record a set; `gigs add-track` — what was played and the crowd response.
- `gigs report` — most-played, what works by venue/crowd, energy patterns over a set.
- Optional: cross-reference with `releases` — which of MY tracks I'm actually playing out.

---

## Tool 9 — `web` (public website) — separate Vercel deploy
A real public site, not just the EPK — promo for music AND the broader brand (events, DJ,
the multi-disciplinary story). Keep it clearly the **brand/music property**, separate from
the quant/tech portfolio.
- **Stack:** Astro or Next.js (static export), Tailwind, deployed on Vercel. Fast,
  content-driven, dark editorial aesthetic consistent with the brand.
- **Sections:**
  - Hero / the story (DJ + producer + runs 1,500-cap events — the rare combination).
  - Music: embedded releases (Spotify/SoundCloud), the monthly mix, latest drops.
  - Live: upcoming + past events, with photos/clips from the clipper.
  - Press kit: downloadable EPK (bio, photos, logos, rider) — folds in the `site` EPK work.
  - Booking / contact: form or mailto, clearly surfaced.
  - Optional links out: events brands (BPM / Throwbacks) for the wider operation.
- **Content in markdown/JSON** so I update it without touching components.
- **Optional (AI, flagged):** a small endpoint that drafts release blurbs / event copy from
  a few details, in my voice — Claude via the Anthropic API.
- Don't sell on the tech portfolio; this is where the music and brand live and convert.

---

## Build order for these
1. `releases` — the shipping pipeline (attacks the real bottleneck: finishing music)
2. `web` — public presence so released music has somewhere to point
3. `scheduler` — once the clipper is producing clips to queue
4. `finder` — production-speed booster
5. `gigs` — nice-to-have, lowest urgency

Each is self-contained and well-specified — good for a long autonomous Fable session, then
the `@reviewer` / `@test-runner` loop before merge. But none of them before the clipper ships
clips and a couple of tracks are actually finished.

---

## Candidate — `crate` (fresh-crate discovery / anti-stale-set) — CAPTURED, not yet scheduled
<!-- Added 2026-07-06 from a painkiller-vs-vitamin ideation pass. This is a GAP: every existing
tool (librarian, the set-prep memo) operates on music you ALREADY OWN. Nothing goes outward to
find NEW external tracks. remix-scout finds *remixable* candidates, not a fresh DJ-play crate.
Painkiller-vs-vitamin verdict: borderline — becomes a real painkiller only if tied to
edits/acapellas nobody else has; otherwise it's a vitamin ("prep some DJs enjoy"). Build only
after the money-painkillers (booking deposits, PRS/PPL royalty recovery) if at all. -->

A weekly **fresh-crate** feed: surface brand-new external releases that fit my sound
(amapiano / afrobeats / UKG / rap) and aren't already overplayed, so sets stay current without
hours of digging. Distinct from everything built so far:
- **vs `librarian` / set-prep memo** — those organise and sequence the library I *already own*;
  `crate` sources tracks I *don't* have yet.
- **vs `finder`** — that indexes samples/loops in `ProducerLibrary` for production; `crate` is
  DJ-play tracks for the floor.
- **vs `remix-scout` (skill)** — that finds tracks worth *remixing*; `crate` finds tracks worth
  *playing*.

Rough shape: pull new-release + trending signals (per-genre), filter to my genres, down-rank
the already-saturated, prefer tracks with available edits/acapellas, write candidates to the
vault / a reviewable list (never auto-download). Reuses `remix-scout`'s signal plumbing.
**Real product only if the edits/acapellas angle makes it a painkiller — otherwise it's a
vitamin and stays parked.**
