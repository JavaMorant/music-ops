# music-ops — board

A lightweight kanban of what's left. Tags: `[clipper]` `[librarian]` `[outreach]`
`[releases]` `[site]` `[repo]`. Ambition-tier = the AI/“nice-to-have” layer that
comes only after each tool's boring core runs (see `CLAUDE.md`).

_Last synced: 2026-06-29._

---

## 🔨 In progress

- _(nothing mid-flight — `feat/organise-engine` is merged to `main`.)_

---

## 📋 Next up

- `[librarian]` **Trust existing rekordbox BPM/key** — wire pyrekordbox so analysed
  BPM/key are read from the collection (librosa only as fallback); flag
  low-confidence key detections rather than guessing. Listed as a safety invariant
  in `CLAUDE.md` but still not implemented.
- `[librarian]` **Harmonic set planning (camelot / setplan)** — the build-order tail.
  `setcard.py` / `setlog.py` / `pulse_usb.py` exist, but there's still no `camelot` /
  `setplan` CLI command surfacing harmonic-key set building.
- `[librarian]` **Filename normalization pass** — genre tags are written but filenames
  are kept as-ingested (`tagclean` never renames). A consistent `Artist - Title`
  rename across `~/DJ/library` is still pending (`naming.target_stem` exists, used
  only by the cleanup/inbox path).
- `[librarian]` **Apply the organise plans on `~/DJ`** — held by policy (plans-only).
  Dedup / reorg / quarantine-recovery plans are generated but nothing is applied.
  Guest-USB identity on junk filenames needs an AcoustID key (`~/DJ/.acoustid-key`
  or `ACOUSTID_API_KEY`); the master library classifies fine without one.
- `[librarian]` **Ingest the ~1.1k staged files** — `~/DJ/inbox` holds ~1,146 files
  (incl. `_from-usb` / `_from-pioneerdj` recoveries) not yet folded into the library.
- `[site]` **Deploy to Vercel** — `vercel.json` is in place but there's no `.vercel/`
  or live URL yet; this is the one step waiting on you. Before going live: replace
  placeholder content (bio, stats, a stray Rickroll in `highlights.json`, sample
  embeds), drop in a real 1200×630 `og.png` (currently `og.svg`, which iMessage /
  Twitter won't render), and add real press-kit assets.
- `[repo]` **Add `releases/` to the CLAUDE.md build order** — it's in the documented
  layout but still missing from the numbered build-order section.

---

## 🧊 Backlog (ambition tier — after cores are solid)

- `[releases]` **AI “what this needs to ship” estimator** — arrangement / mix / vocal
  checklist inferred from a bounce. (`[ai]` extra reserved, not built.)
- `[outreach]` **Research enrichment step** — enrich a draft with real, specific
  detail before writing. Still drafts only, still never sends.
- `[clipper]` **Claude clip ranking** — let the model re-rank candidate moments on top
  of the energy heuristic.
- `[clipper]` **Validate the crowd detector** — `--crowd-weight` is an experimental
  spike; the flatness heuristic reads backwards on real material. Default stays 0
  until it's proven.
- `[librarian]` **Web app auth token** — `serve` is localhost-only and unauthenticated;
  add a token if it ever needs to be reachable.

---

## 🟢 Recently done

- `[librarian]` **Organisation overhaul** (merged `feat/organise-engine`) — four-stage
  engine: identity (fpcalc→AcoustID, cached, degrades) → dedup v2 (recording-level,
  version-preserving, never deletes) → classify v2 (23-bucket, tiered, concurrent) →
  output. `librarian organise` CLI (dry-run default, `--apply` gated) + web Organise
  button. Guest-USB `.m3u8` per-genre + `Low/Unrated` mode. Version detection and
  quarantine recovery. Floor-fillers now exclude 0-star tracks.
- `[librarian]` **Dedup cue-redirect fix** — a quarantined duplicate's rekordbox cues
  now follow the kept copy, not the `_quarantine` reject (cue-safety invariant; the
  web `/api/organise` path was bugged the same way).
- `[librarian]` **Fuzzy / fingerprint dedupe** — delivered via `identity.py` +
  `dedup_v2.py` (recording-level near-dup detection); the old TODO is closed.
- `[librarian]` **Deterministic tag cleanup** (`tagclean`) and the **USB recency-window
  chooser** + per-window pulse.
- `[releases]` **Reel deck** — reel/mp4 mode, Web-Audio playback (no media bar), bokeh,
  deck Size S/M/L/XS, OBS recording recipe. **Demucs stem separation** (`stems.py`)
  committed + wired into the CLI and the web remix player (opt-in behind `has_demucs()`).
- `[outreach]` **Local CRM web app** — drag-stage kanban, add / log / draft, drafts
  viewer; never-sends invariant holds. PR #1 **merged**.
- `[librarian]` **Duplicates browse + merge**, **ingest pipeline + pulse web GUI**.
- `[releases]` **Track List web app** — beat-reactive vinyl turntable, packs, reel.
