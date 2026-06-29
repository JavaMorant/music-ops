# Organisation Overhaul — Design Spec

**Date:** 2026-06-29
**Status:** Approved (decisions locked via brainstorming; user set an autonomous goal "keep working until completion")
**Branch:** `feat/organise-engine`

## Problem

The library's organisational side is unsatisfactory in three ways the user named:

1. **Misclassification** — the 24 genre buckets are *good*, but too many tracks land in
   the wrong one. Root cause: the classifier only saw filenames/tags, and many are junk
   (`UnknownArtist`, label-as-artist, missing genre), so uncertain tracks got forced into
   catch-alls (`Other edits`, `General House`).
2. **Janky workflow** — the dedupe→classify→playlist→export flow was one-off scratchpad
   scripts, not a repeatable, first-class capability.
3. **No single source of truth** — the real library + cues live on someone else's Windows
   rekordbox; the user DJs off an export stick. Nothing is canonical.

Plus four concrete asks from the user:
- Remove **real** dupes including alternate-source versions (audio + music-video + lyrics).
- Reorganise by genre (accurately).
- Recover "missing" intro/edit versions (e.g. Promiscuous, Wannabe) — found: they are in
  `~/DJ/_quarantine`, quarantined by an earlier run, not lost.
- Fix **floor-fillers** (a 0-star track must never be a floor-filler) and improve recs.

## Decisions (locked)

| Decision | Choice |
|---|---|
| Canonical library | **`~/DJ` on the Mac** is master; guest USBs get importable `.m3u8` |
| Taxonomy | Keep the existing **24 genre buckets**; fix accuracy, not categories |
| Accuracy method | **Identify the real track first** (acoustic fingerprint → AcoustID/MusicBrainz), then classify with a **higher-effort model**; minimise manual review |
| Dedup rule | **Recording-level**: REMOVE exact copies + same *recording* from different sources (audio / music-video / lyrics-video, keep best-quality audio). KEEP distinct versions: intro edits, extended, radio, remixes, instrumentals, acapellas, live |
| Missing versions | Recover from `~/DJ/_quarantine`; flag genuinely-absent ones into a re-acquire list |
| Floor-fillers | Exclude 0-star; rating + play-history aware; better recommendations |
| Surface | **Shared engine**, exposed as a **CLI command AND a web-app button** |
| Budget | **Modest (~$10)** — tiered: cheap model on easy tracks, higher-effort only on hard/uncertain |
| Safety on `~/DJ` | **Plans only** — build + run, but anything that moves/removes files in `~/DJ` is a reviewable plan held for the user; non-destructive outputs (m3u8/XML/reports) produced fully |
| First-run scope | Build the engine; run it on `~/DJ` as reviewable plans/reports |

## Architecture — the Organise engine

A single pipeline, `librarian/src/librarian/organise_engine.py` (name TBD), composed of four
isolated stages with well-defined interfaces. Each stage reads the previous stage's output
and is independently testable. The engine is pure/dry-run by default; all disk mutation goes
through the existing reversible `engine.apply_plan` / `journal` / `undo` machinery.

```
source (USB or ~/DJ)
   │
   ▼  Stage 1: IDENTIFY ───────────────────────────────────────────────
   │   fpcalc → AcoustID lookup → MusicBrainz → real {artist,title,(mbid)}
   │   cached in an identity store keyed by fingerprint (re-runs are free;
   │   a track identified once is never looked up again).
   │   Degrades gracefully with no AcoustID key (uses cleaned tags/filename).
   ▼
   │  Stage 2: DEDUP v2 ────────────────────────────────────────────────
   │   group by acoustic fingerprint (same recording) AND by identity.
   │   Recording-level rule: collapse exact + cross-source copies, keep best
   │   audio; PRESERVE distinct versions via title/version-token detection
   │   (intro/extended/radio/remix/instrumental/acapella/live/VIP/flip…).
   │   Never deletes — drops go to quarantine via the reversible plan.
   ▼
   │  Stage 3: CLASSIFY ────────────────────────────────────────────────
   │   tiered: deterministic/cheap pass for confident cases (clean identity
   │   + known artist), higher-effort model only for the hard/uncertain ones.
   │   Into the 24 buckets. Cached by identity so re-runs don't re-pay.
   ▼
   │  Stage 4: OUTPUT ──────────────────────────────────────────────────
   │   master (~/DJ): reviewable on-disk reorg plan (plan/apply/undo) +
   │     rekordbox XML; HELD for review (plans only).
   │   guest USB: importable .m3u8 per bucket + Low/Unrated; never touches
   │     the stick's export.pdb. Optional cue-preserving rekordbox XML.
```

### Stage interfaces (isolation)
- `identify(files) -> {path: Identity}` — Identity = {artist, title, mbid?, source, confidence}
- `dedup_plan(files, identities, fingerprints) -> DedupResult` — {keepers, drops(→quarantine), version_groups}
- `classify(keepers) -> {path: Genre}` — one of the 24 buckets
- `build_outputs(keepers, genres, ratings, target) -> Outputs` — plans + m3u8 + xml + reports

### Identity store / caching
- Local JSON/SQLite keyed by chromaprint fingerprint → identity + classification.
- Makes the engine cheap to re-run and idempotent. Survives across USBs (a track seen on
  one stick is identified for all).

## Separate workstream — Floor-fillers & recs

Independent of the pipeline. Current floor-filler logic (in `ai.py` crate defs +
`rekordbox_sync.py` + `run_floorfillers.py`) can mark 0-star tracks as floor-fillers — wrong.
Fix:
- A floor-filler MUST have rating ≥ threshold (≥3★) AND/OR real play-history support.
- Exclude 0-star everywhere floor-fillers/recs are computed.
- Recommendations become rating + play-history aware (lean on `pulse_usb` play data).

## Quarantine recovery (missing versions)

- Scan `~/DJ/_quarantine` (and `.quarantine`) for tracks matching wanted titles/identities.
- Produce a **recovery report** (what's recoverable, which version each is) — HELD for review
  per the plans-only rule. Confirmed examples: Promiscuous (incl. music-video + "Loose"),
  Wannabe.
- Genuinely-absent wanted versions → a `re-acquire.txt` list.

## Surfaces
- **CLI:** `librarian organise <path>` (USB or library root); flags for `--apply` (gated),
  `--out`, `--rekordbox-xml`. Mirrors existing `organize`/`ingest`/`dedupe` conventions.
- **Web:** a button in the existing USB/Pulse panel: plug a stick → Organise → progress →
  outputs. Shares the engine; long job runs server-side like `ingest`.

## Safety invariants (non-negotiable, per CLAUDE.md)
- Dry-run by default; only a reviewed plan is applied. On `~/DJ`, **hold all applies**.
- Never delete — drops go to quarantine; highest-quality / preferred-version kept.
- Never write a CDJ stick's `export.pdb`; guest output is import-only m3u8 (+ optional XML).
- rekordbox cue safety: emit updated paths/XML; never point at dead paths.
- Undo journal covers every applied change.

## Testing (pytest, per CLAUDE.md)
- Dedup v2: recording-level collapse, version-preservation (intro/extended NOT collapsed),
  never-delete invariant.
- Identity: cache hit/miss, graceful degrade with no key.
- Classify tiering: easy→cheap path, hard→high-effort path; cache reuse.
- Floor-fillers: 0-star excluded.
- Quarantine recovery: matching + report, no auto-move under plans-only.

## First autonomous run — definition of done
1. Engine implemented (4 stages) with tests green.
2. Floor-fillers fix + tests.
3. Quarantine-recovery report (incl. Promiscuous/Wannabe) — held.
4. Run engine on `~/DJ` (identity where key available, else degraded) → reviewable dedup
   plan + classification + on-disk reorg plan + reports. **No applies.**
5. CLI command + web button wired.
6. Everything committed on `feat/organise-engine`; a summary of plans/reports for review.

## Build status (2026-06-29, branch `feat/organise-engine`)

Done + tested (212 pytest passing), committed:
- ✅ Floor-fillers / recs rating-gated (0-star excluded) — `rekordbox_sync._rated_cids`.
- ✅ Version detection (`versions.py`) + quarantine recovery (`quarantine.py`). Ran on
  `~/DJ/_quarantine`: **1,642 distinct versions** flagged recoverable; Promiscuous/Wannabe
  located. Reports under `~/DJ/quarantine-recovery/` (restore held).
- ✅ Identity stage (`identity.py`) — fpcalc→AcoustID, cached, degrades without key.
- ✅ Dedup v2 (`dedup_v2.py`) — recording-level, version-preserving.
- ✅ Classify v2 (`classify_v2.py`) — tiered, identity-aware, cached.
- ✅ Organise engine (`organise.py`) + `librarian organise` CLI + `/api/organise` web button.
- ✅ Ran on `~/DJ/library` (7,949): **7,506 keepers, 443 recording-level dupes, 1,764 versions
  preserved, 6,628 relocations**, 637 needing the AI pass. Plans held under `~/DJ/organise-run/`
  (both preflight-OK). **Nothing applied.**

Blocked on user action:
- ⏳ The accurate identity-first AI classify run needs the AcoustID key. Once it's at
  `~/DJ/.acoustid-key` (or `ACOUSTID_API_KEY`), re-running `librarian organise ~/DJ/library`
  fills in real identities + genres; cached, so cheap thereafter.

Next increment (not in first-run scope):
- Guest-USB output mode (genre `.m3u8` + Low/Unrated from pdb ratings) in the engine — the
  D_MI scratchpad playlists already demonstrate the shape.

## Open / external
- **AcoustID API key** not yet on the machine. Identity stage built to read
  `ACOUSTID_API_KEY` (env) or `~/DJ/.acoustid-key`; runs full-accuracy once present,
  degrades cleanly until then. Classification can re-run for free (cached) when the key
  arrives and identities improve.
