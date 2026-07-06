# Setplan — set-prep engine design spec

**Status:** DRAFT for review · 2026-07-06
**Scope:** the LOCAL set-prep feature for the existing `librarian` tool (a new `setplan`
CLI command + engine, later a web tab). The cloud/SaaS lift is **out of scope here** —
that lives in `setprep-saas-proposal.md` and is gated on founder decisions. This spec
is the build-ready Phase 0/1 piece and depends on none of those decisions.

**One-line goal:** given a gig description, build an ordered, ready-to-play set from the
DJ's own library — sequenced by harmonic + BPM + energy-arc fit and, uniquely, by the
DJ's own play-history transition graph.

**Success criterion (the kill test):** the generated sets must beat the founder's manual
prep on ≥3 real gigs. If they don't, the feature (and the product thesis) stops here.

---

## 1. Where it fits — reuse map

Setplan is a **read-only** feature: it reads library metadata + play history and writes
plan artifacts/exports. It never moves or retags files, so it sits *outside* the
plan/apply/undo engine — but it persists every plan to the runs area in the same
reviewable-artifact spirit.

| Existing primitive | Module | Setplan use |
|---|---|---|
| `TrackMeta` incl. `bpm`, `key` | `metadata.py` (`read_meta`) | Candidate pool: BPM + key per file, no audio analysis |
| Play sessions in order (desktop) | `pulse.py` | Proven-track + position priors |
| Play sessions in order (USB DL/DL+) | `pulse_usb.py` (`read_stick`) | Same, for Opus-Quad history |
| **Transition graph** `follows()` | `pulse_usb.py:354`, `/api/pulse/next` | Personal "what works live for me" edge weights |
| Play-count / floor-filler stats | `pulse_usb.usb_insights` | Proven-vs-fresh scoring |
| Label normalisation `_norm` | `pulse_usb.py` | Join file metadata ↔ play history |
| rekordbox XML playlist emit | `rekordbox.add_tracks_and_playlist` | Cue-safe export |
| m3u8 write | `pulse._write_m3u` | Export |
| Venue/set sidecar | `setlog.py` | Venue-filtered history |

**New code:** `camelot.py` and a `setplan/` package (`pool.py`, `score.py`, `search.py`,
`export.py`), a `setplan` Typer command, and (Phase 2) a web tab + 4 API endpoints. Each
module has one job and a narrow interface so it's testable in isolation:

- `camelot.py` — key string → `(1–12, A|B)` + a `harmonic(a, b) -> float` compatibility score. Pure, no I/O.
- `setplan/pool.py` — build `list[Candidate]` from the library + play history (the only I/O-heavy module).
- `setplan/score.py` — pure `score(candidate, prev, slot, spec) -> float` + reason string. No I/O.
- `setplan/search.py` — turn a scored pool + `GigSpec` into an ordered `SetPlan` (greedy v1, beam v2).
- `setplan/export.py` — `SetPlan` → rekordbox XML / m3u8 / markdown.

---

## 2. Input — the `GigSpec`

One JSON-serialisable dataclass; the shared contract for CLI flags, the web form, and a
future API. Everything optional except `minutes`.

```python
@dataclass
class GigSpec:
    minutes: int                          # required
    tracks_per_hour: int = 20
    journey: list[tuple[str, float]] = [] # ordered genre blocks, fractions: [("amapiano",.6),("afrobeats",.4)]
    arc: str = "peak"                     # warmup | build | peak | closing | journey | custom
    bpm_range: tuple[int,int] | None = None
    end_bpm: int | None = None            # closing anchor ("hand over at 124")
    harmonic: str = "loose"               # strict | loose | off
    freshness: float = 0.3                # 0 = proven bangers … 1 = surface the unplayed
    must_play: list[str] = []             # fuzzy queries → anchors
    avoid: list[str] = []                 # tracks / artists / genres to exclude
    venue: str | None = None              # filters follows() to matching setlog nights
    opener: str | None = None
    allow_low_bitrate: bool = False
    seed: int | None = None               # reproducible rerolls
```

**Energy arcs** are piecewise anchor curves over normalised position `t∈[0,1]`, each anchor
`(t, bpm_percentile, energy)`. Presets: `warmup` (low, gentle rise, bangers held back),
`build` (monotonic climb), `peak` (fast ramp → 80–95th-pct plateau → late spike),
`closing` (descent + known-track bias), `journey` (two humps, mid breather), plus custom
anchor lists. **BPM targets are percentiles within the pool for the active genre block**,
so "peak" means ~115 in amapiano but ~140 in UKG; `bpm_range`/`end_bpm` clamp the curve.

---

## 3. Scoring & ordering

### 3.1 Candidate pool (`pool.py`)
Walk the library (same scan as cleanup) → `read_meta` → one `Candidate` per track with:
`path, artist, title, genre, bpm, camelot, length_s, norm_key`, and joined-from-history
`plays_all, plays_recent, last_played, sets_count, position_prior, follows_out`. Cached to
`runs/setplan-pool.json`, rebuilt on demand. Hard filters up front: `avoid`; genre ∉
journey ∪ adjacency map; BPM outside `bpm_range` (unknown BPM survives); low-bitrate unless
allowed. **Genre adjacency** is a small editable map (defaults: amapiano ↔ afrobeats ↔ afro
house; uk garage ↔ house; uk rap ↔ us rap ↔ rnb) — exact 1.0, adjacent 0.6, else pruned.

### 3.2 `camelot.py` — harmonic score
Parse Camelot (`8A`), Open Key (`1m/1d`), and classical (`Am`, `F#m`, `Ebmaj`, `C`) → `(n, A|B)`.
```
same key 1.00 · ±1 same letter 0.90 · relative (same n, A↔B) 0.85 ·
+2 same letter 0.55 (rising arc only) · +1 letter-swap 0.40 (loose only) ·
else 0.10 · either key UNKNOWN → 0.50 (neutral — never punish, never guess)
```
`strict` prunes < 0.85; `off` fixes the component at neutral.

### 3.3 BPM — transition + arc fit
`bpm_dist(a,b) = min over m∈{.5,1,2} of |a·m − b|/b` (half/double-time aware, so 70↔140 is one
move). `bpm_transition` = 1 within 2%, linear to 0 at 8%. `arc_fit` = gaussian(bpm − target, σ≈4).
Unknown BPM → both neutral (0.5) and flagged in the reason.

### 3.4 Slot score
```
score(c | prev, slot) =
  0.20·harmonic + 0.10·bpm_transition + 0.10·arc_fit + 0.15·genre_fit
  + 0.20·proven_mix + 0.25·follows_boost − penalties
```
- **`follows_boost`** = `min(1, log2(1+count)/2)` from the DJ's own transitions — the
  differentiator. It is an **additive bonus, never averaged in**, so no-history never drags
  down a harmonically perfect pick (essential for new tracks *and* cold-start users; with zero
  history the engine gracefully degrades to a harmonic/arc planner). A 3-gram "signature run"
  match adds +0.05 and is named in the reason.
- **`proven_mix`** = `(1−freshness)·proven + freshness·novelty`; `proven=log(1+plays)/log(1+max)`,
  `novelty = 1 − recency` (never-played = 1.0).
- **Penalties**: same artist within 6 slots (−0.3); same key >3 consecutive (−0.15); played at
  this venue in the last 2 tagged sets (−0.2); longer than remaining time (pruned).
- **Arc modulation**: warm-up halves the proven weight; `position_prior` adds ±0.05 (a track he
  historically opens with scores higher in slots 1–3).

Weights ship as editable presets (`default`, `harmonic-purist`, `crowd-safe`, `explore`). No ML;
every component maps 1:1 to a reason string.

### 3.5 Ordering — anchors + beam search (`search.py`)
1. **Slots** `n = round(minutes/60 · tracks_per_hour)`; running time from `length_s`×0.55.
2. **Anchors**: opener + each must-play placed by phase (must-plays → peak plateau; pinnable to a
   time). Anchors split the set into **segments**.
3. **Segment beam search** (how DJs think — "route me from here to Mnike by 10:45"): width B=8,
   expand top M=20 by slot score, terminal bonus for landing harmonically/BPM-near the segment's
   closing anchor so it *steers into* the must-play cleanly.
4. **v1 ships greedy** (B=1, same code path); beam is a constant change. ~2k filtered pool ×
   20×8×~40 slots → sub-second on a laptop.
5. Seeded RNG tie-breaks → "reroll" varies but is reproducible.

---

## 4. Output — the `SetPlan` artifact
Persisted as `runs/setplan-<id>.json`; every view renders from it. Per slot: track, BPM,
Camelot badge, clock time, a one-line **reason** from the top-two scoring components, and **3
alternates** scored *in that slot's context*, each with its own reason. Reasons cite only real
signals (Camelot move name, BPM delta, `follows` counts "in N sets", plays, freshness, arc phase)
and honestly flag gaps ("no key tag — neutral match").

**Exports** (all from existing code): rekordbox XML playlist (primary, cue-safe), m3u8, markdown
crib sheet. Optional dry-run-gated direct rekordbox playlist insert later.

**Closed loop (Phase 3):** `setplan review <id>` diffs plan vs what pulse says was actually played
(adherence %, alternates used, arc bailouts) → weight-tuning evidence. Plan → play → learn.

---

## 5. Missing key/BPM — degradation ladder
Per track, first hit wins: (1) file tags; (2) rekordbox master.db (pyrekordbox); (3) USB export
meta; (4) still unknown → **neutral 0.5, never zero, never guessed**, competes on
genre/proven/follows, reason flags the gap (`strict` mode excludes key-unknown, with a count).
`setplan doctor` reports pool key/BPM coverage and the identity-join miss rate (pool uses file
tags; history uses `_norm(artist - title)` — exact join then a conservative contains-fallback,
same trick `follows()` uses; unmatched labels surfaced). librosa key-guessing stays out until it
can be flagged low-confidence (safety invariant: never guess key/BPM).

---

## 6. Error handling & safety
- **Read-only.** Setplan never mutates library files; no plan/apply/undo needed. Exports write only
  to explicit output paths / the runs dir.
- **Never guess** key/BPM (neutral instead) — matches the tool's existing safety invariants.
- Empty pool / no candidates for a slot → clear message, partial plan with the gap flagged, never a crash.
- Missing play history → engine still works (follows_boost just contributes 0), explicitly noted.
- rekordbox export reuses the existing cue-safe path; no new file-move risk.

---

## 7. Testing plan (pytest)
- `camelot.py`: parsing across Camelot/Open-Key/classical spellings; the compatibility table;
  unknown → neutral.
- `score.py`: BPM half/double-time distance; follows_boost is additive (a no-history perfect
  harmonic pick still wins vs a low-follows mediocre one); penalties fire; unknown key/BPM → neutral.
- `search.py`: no-artist-repeat-within-6 invariant; must-play appears as an anchor at its phase;
  plan length ≈ requested minutes; determinism under a fixed seed; greedy and beam produce valid sets.
- `pool.py`: metadata↔history join (exact + contains-fallback); low-bitrate/avoid/genre filters.
- `export.py`: rekordbox XML + m3u8 round-trip; markdown renders.
- Golden test: a small fixture library + fake history → a stable, asserted setlist.

---

## 8. Build phases
| Phase | Ships | Effort |
|---|---|---|
| **1 — core** | `camelot.py` + pool + greedy ordering + CLI flags + m3u8/markdown export + the pytest suite above | days |
| **2 — product** | beam + anchor segments, alternates + reasons, rekordbox XML export, web tab (lock/reroll/crate-order mode) | ~1–2 wks |
| **3 — moat** | `setplan review` plan-vs-played diff, venue-filtered follows, weight presets tuned on real gigs | ongoing |
| *(4 — SaaS)* | *out of scope here — see the strategy memo; same engine behind 4 endpoints + metadata-only companion* | later |

---

## 9. Open design decisions (for founder review)
1. **CLI surface first, or CLI+web together for Phase 1?** (Recommend CLI first — fastest path to
   dogfooding at a real gig, which is the kill test.)
2. **Default `tracks_per_hour` / play-fraction** (20 / 0.55) — matches your style, or adjust?
3. **Genre adjacency map defaults** — confirm the amapiano/afrobeats/UKG/rap clusters, or hand me your mental map.
4. **Weight preset as default** (`default` vs a more `crowd-safe` bias) for your own first runs.
5. **Reason-string verbosity** — one line per slot (spec'd), or a terse badge-only mode too?

None of these block starting Phase 1; they're tuning knobs I'll default sensibly and you can override.
