# music-ops — board

A lightweight kanban of what's left. Tags: `[clipper]` `[librarian]` `[outreach]`
`[releases]` `[site]` `[repo]`. Ambition-tier = the AI/“nice-to-have” layer that
comes only after each tool's boring core runs (see `CLAUDE.md`).

_Last synced: 2026-06-23._

---

## 🔨 In progress

- `[releases]` **Demucs stem separation** — `src/releases/stems.py` is written
  (untracked) but not committed or wired into a command/UI yet. Caches stems
  outside the library; opt-in behind `has_demucs()`. → finish wiring + tests, commit.
- `[repo]` **Pull local `main`** — it's 2 commits behind `origin/main` (the merged
  outreach-web PR #1 + the librarian Duplicates work). `git pull` to sync.

---

## 📋 Next up

- `[librarian]` **Trust existing rekordbox BPM/key** — wire pyrekordbox so analysed
  BPM/key are read from the collection (librosa only as fallback); flag
  low-confidence key detections rather than guessing. (Safety invariant in CLAUDE.md.)
- `[librarian]` **Fuzzy / fingerprint dedupe** — current dedupe is exact-match only;
  add chromaprint-style near-duplicate detection (same song, different file).
- `[librarian]` **Harmonic set planning (camelot / setplan / usb)** — the build-order
  tail. `setcard.py` / `setlog.py` / `pulse_usb.py` exist as modules but there's no
  `camelot` / `setplan` CLI command surfacing harmonic-key set building yet.
- `[site]` **Deploy to Vercel** — `vercel.json` is in place but the site isn't live
  yet (no `.vercel/`); this is the one step waiting on you.
- `[repo]` **Add `releases/` to CLAUDE.md layout** — it's a full tool now but still
  missing from the documented monorepo layout / build order.

---

## 🧊 Backlog (ambition tier — after cores are solid)

- `[releases]` **AI “what this needs to ship” estimator** — arrangement / mix / vocal
  checklist inferred from a bounce. (`[ai]` extra reserved, not built.)
- `[outreach]` **Research enrichment step** — enrich a draft with real, specific
  detail before writing. Still drafts only, still never sends.
- `[clipper]` **Claude clip ranking** — let the model re-rank candidate moments on top
  of the energy heuristic.
- `[clipper]` **Validate the crowd detector** — `--crowd-weight` is an experimental
  spike; the flatness heuristic reads backwards on real material. Keep default 0
  until it's proven.
- `[librarian]` **Web app auth token** — `serve` is localhost-only and unauthenticated;
  add a token if it ever needs to be reachable.
- `[site]` **Real OG image** — replace the `public/og.svg` placeholder with a
  1200×630 `og.png`.

---

## 🟢 Recently done

- `[outreach]` **Local CRM web app** — drag-stage kanban board, add / log / draft in the
  browser, drafts viewer. Never-sends invariant held (no send route). PR #1 **merged**.
- `[librarian]` **Duplicates browse + merge** — same-song-different-file dedupe in the
  pulse GUI.
- `[librarian]` **Ingest pipeline + pulse web GUI** — USB-first dashboard, folder ingest,
  PioneerDJ recovery, crates + genre playlists under one `_Playlists` folder.
- `[releases]` **Track List web app** — beat-reactive vinyl turntable, packs, reel mode.
