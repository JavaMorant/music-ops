# releases once-over — design

**Date:** 2026-07-02 · **Branch:** `feat/releases-onceover` · **Scope:** the `releases` tool only (other tools handled elsewhere).

## Goals

Two user purposes drive everything:
1. **Organise & manage music/beatpacks** — the web app becomes a full management hub.
2. **Record tracks on the vinyl/cassette deck and post them** — OBS-automated, count-in, organized output.

Plus a **Refined Editorial** restyle, delivered revertibly. Future monetization is a design constraint (portable deck engine, static hostable packs, thin API over domain logic), **not** a rebuild trigger.

## Decisions log (from brainstorm)

| Decision | Choice |
|---|---|
| Scope | releases only |
| Approach | Modularize-then-build, monetize-aware (no framework rebuild) |
| Visual direction | A — Refined Editorial, everywhere; color latitude given |
| Record path | OBS automation is the golden path; tab-capture fallback |
| Web hub scope | Full hub (releases/scheduling/dashboard into web UI) |
| Revertibility | Feature branch + theme layer with `classic` = today's exact look |

## 1 — Foundation & architecture

```
src/releases/web/
  index.html          # slims to structure + view mounting
  css/app.css         # app chrome
  css/themes.css      # [data-theme] custom-property sets: editorial (default), classic
  js/app.js           # boot, routing between views, theme toggle
  js/views/*.js       # tracks.js, packs.js, releases.js, dashboard.js
  js/studio.js        # deck overlay + record flow (opened via ● Record / ▶ Play — not a tab)
  deck/               # ★ single source of truth for the deck
    deck.css          # vinyl + cassette skins, reel/clean modes, FX
    deck.html         # deck markup fragment
    deck.js           # skin/mode/scrub/FX wiring
    turntable.js      # the particle/spectrum engine (moved out of pack.py)
```

- The app serves `deck/*` static; `pack.py` **inlines the same files at build time** — exported packs stay fully self-contained (monetize seam). Kills the current app↔pack drift (320px vs 330px deck, mismatched reel sizing). One deck, two consumers.
- **Delete** the dead canvas-export engine (`ttExportFrame`/`ttVinylScene`/`ttCassetteScene`/`pauseDraw`/`bottomFx`, pack.py:364–428) — shipped in every pack, never called.
- FastAPI serves the new static dirs; webapp routes stay thin — all domain logic lives in modules (`db.py`, `plan.py`, `score.py`, `pack.py`, new `obsctl.py`).

## 2 — Theme: Refined Editorial

All colors/type/spacing become CSS custom properties under `[data-theme]`.

| Token | editorial | notes |
|---|---|---|
| bg / panel | `#0b0b0e` / `#14141a` | warm off-black, soft panel gradient |
| accent | `#d4aa5e` champagne brass | actions + active states only |
| record | `#b3352c` | strictly for record affordances |
| remix | `#b98ede` violet | remix badges (pairs with Beats/Remixes split) |
| ink / dim | `#ecebe6` / `#96959e` | |
| display type | **Fraunces** (self-hosted, as on the EPK site) | track names + headers |
| ui type | system sans, small-caps letterspaced labels | |
| numbers | mono (BPM / key / time) | |

- `classic` theme = today's values extracted verbatim. Header toggle, `localStorage` persisted; pack export takes `?theme=` (editorial default).
- Track rows become soft cards (12px radius, hairline border, inset top highlight) instead of flat table rows.
- Approved via mockup in the brainstorm session (user: "happy with that").

## 3 — Management hub (four tabs)

New endpoints are thin, origin-guarded, localhost-only routes over the same functions the CLI uses.

**① Tracks** — current view restyled to cards. Fixes: explicit `unknown` genre chip (kills empty-vs-unknown ambiguity); violet `remix` badge via `Project.is_remix`; send badges; send-logging via modal (browser `prompt()`/`confirm()` removed).

**② Packs** — real pack builder: explicit track selection (start from filter or pick), drag ordering, name, cover, full/protected-preview choice, live player preview, Build → zip. Send logging (contact/date/note) + Sent history live here (per-pack + per-track). New `/api/packs/*`; reuses `/api/pack`, `/api/sent`, `/api/sends` machinery.

**③ Releases** — surfacing the CLI-only power features: create/rename/ship/delete releases; add/remove/reorder tracks (drag); schedule via cadence + optional "EP by date" target onto Fridays; simple calendar strip. New `/api/releases/*`, `/api/plan` wrapping the `release` sub-app + `plan` logic.

**④ Dashboard** — Beats vs Remixes split (mirrors CLI dashboard), closest-to-done, scheduled/overdue, send activity. New `/api/dashboard`.

**Stays CLI-only:** `scan`, organize `by-stage`/`file`/`rename`/`relink` (rare, power-user, better reviewed in terminal).

## 4 — Record studio (reel → post)

### OBS automation (golden path)
- New `obsctl.py`: minimal obs-websocket **v5** client (sha256 challenge auth, request/response, event subscribe) over the `websockets` lib. Optional dependency behind a `[obs]` extra with `has_obs()` guard — mirrors the `has_demucs()` pattern. Config: `OBS_WS_URL` (default `ws://127.0.0.1:4455`) + `OBS_WS_PASSWORD` via env or a small settings panel persisted beside the db (never in the repo).
- New endpoints: `/api/obs/status` (connected? recording?), `/api/obs/record/start`, `/api/obs/record/stop` (returns output path from `GetRecordStatus`).
- **Recording flow** (from the reel setup panel):
  1. User hits Start → app shows the **on-screen count-in** (ring countdown, user-selectable 3/5/10s; replaces the current silent `setTimeout` — fixes the "count in your head" problem).
  2. At T−0.5s the backend sends `StartRecord`.
  3. The beat drops only when OBS confirms `RecordStateChanged: started` (max wait 2s, then drop anyway with a warning toast). Countdown never appears in the file; audio starts ≈ at frame 1.
  4. Track ends (+2s tail) → auto `StopRecord` → backend post-processes: if the OBS output isn't already `.mp4` and ffmpeg is present, remux to mp4 (`ffmpeg -c copy +faststart`; otherwise keep the original container), rename `<track>-<skin>-<date>.mp4`, move to `~/releases-reels/<track>/`, write `caption.txt` stub (title, BPM, key, genre, producer tag, hashtag suggestions).
- **Scene setup stays manual** (one-time, per a simplified RECORDING.md); v1 does not create OBS scenes/sources — too OS-fragile. "Scene setup assist" logged as future work.

### Fallback + unification
- No OBS connected → same count-in UI, existing tab-capture recorder (kept, restyled). Its output now POSTs to a new `/api/reel-save` instead of a bare browser download, so **both paths** land in `~/releases-reels/<track>/` with the same naming + caption stub.
- A **Recordings drawer** in the studio view lists results (play, reveal in Finder, open caption).
- Status chip in the setup panel: `OBS: connected` / `not connected — using tab capture` (never blocks).

### Reel setup panel v2
Skin (vinyl/cassette) · Size (XS/S/M/L) · FX chips · Dancers (remix only) · **Count-in (3/5/10s, default 5)** · **Title card on/off** · **Safe-area guide** (TikTok/Reels UI zones overlay — visible during setup/framing only, hidden the moment recording starts).

## 5 — Reel visual upgrades (all optional FX chips, per-skin defaults)

- **Cassette:** VHS grain + occasional tracking line + slight chroma shift (canvas/CSS overlay).
- **Vinyl:** dust motes + soft vignette (extends existing bokeh/embers).
- **Title card:** first ~1.5s — track name / producer / BPM in Editorial type, fades out (intentional intro, recorded).
- **End card:** optional 0.8s fade to producer name — clean loop point.
- Hue override chip (cover art already seeds spectrum hue; make it adjustable).

## 6 — Pack player parity + monetize seams

- Pack player consumes the shared deck module → gains reel sizes, themes (`?theme=`, editorial default), and the new FX. Recording stays app-only (recipients don't record).
- Inquire stays `mailto:` locally; hosted-build seam (tracked POST) documented, not built.
- `zip_pack` unchanged; self-contained invariant pinned by test.

## Error handling

- OBS unreachable/auth-fail → status chip + tab-capture fallback; never blocks playback.
- OBS start-confirm timeout (2s) → beat drops anyway + warning toast (recording may clip the first instant rather than desync).
- ffmpeg missing → keep original container with a notice (same behavior as today's `/api/tomp4`).
- New endpoints: 4xx + detail on bad input; UI surfaces toasts. Multi-GB library scans unaffected (no new scan paths).

## Testing

- `obsctl`: fake in-process websocket server — auth handshake, start/stop round-trip, event-gated beat start, timeout path.
- Endpoints: packs/releases/plan/dashboard/reel-save/obs-status via TestClient (domain logic already covered by CLI tests).
- Themes: both themes define the identical custom-property set (parity test).
- Pack build: still self-contained (no external URLs), deck module correctly inlined, dead engine gone.
- Existing 193 tests keep passing throughout; UI flows manually verified via `releases web`.

## Build order (each slice: tests green → commit)

1. **Foundation** — file split, shared deck module, dead-code deletion, theme scaffolding (`classic` extracted first so visuals are provably unchanged).
2. **Editorial theme** — tokens + component restyle + toggle.
3. **Hub** — Tracks fixes → Packs builder → Releases/scheduling → Dashboard.
4. **Record studio** — `obsctl` → count-in + flow rewrite → reel-save/caption/Recordings drawer.
5. **Reel FX + pack parity** — VHS/dust/title/end cards → pack player gains sizes/theme/FX; RECORDING.md rewritten around the new flow.

## Out of scope (logged, not built)

Hosted pack pages with payments/tracking · OBS scene auto-setup · direct social posting · multi-user/auth.
