# Deck glow-up: skin fidelity + cinematic FX — design

Expands §5 of `2026-07-02-releases-onceover-design.md` (reel visual upgrades) into a
standalone effort, pulled forward at the user's request. Goal: the vinyl and cassette
decks must look premium in 9:16 phone video (TikTok / Instagram Reels) — both as
objects (skin fidelity) and as footage (cinematic FX). User approved both layers
2026-07-03; colours/detail at implementer-of-record's (controller's) discretion.

## Constraints (binding)

- All skin work lives in the **shared deck module** (`releases/src/releases/web/deck/`:
  `deck.html`, `deck.css`, `turntable.js`, `deck.js`) so the web app AND exported packs
  inherit it. Pack inlining placeholders (`__DECK_CSS__`, `__DECK_HTML__`, `__DECK_JS__`,
  `__VIZ_JS__`) keep working; **packs stay fully self-contained** (no http(s) fetches,
  no `.woff2`) — the monetize seam. Any new imagery is inline data-URI SVG/CSS only.
- `--deck-size` cascade unchanged (app 320px, pack 330px; deck.css declares no `:root`).
- Skins are theme-independent (identical under classic and editorial).
- Existing behaviours preserved: spin/pause, scrub-to-seek, tap-to-play, tonearm `--prog`
  sweep, reel-mode scene scale, label cover-art mode, `--heat` reel glow.
- Baseline 204 tests stay green. New pytest coverage where string-assertable
  (pack inlining, placeholder leaks, self-containment, chip markup); visual quality is
  verified by manual checklist.
- Repo hygiene: parallel session owns uncommitted `librarian/` + root `CLAUDE.md`/`TODO.md`
  — never staged. Every `git add` names `releases/` or `docs/` paths explicitly.
  Branch `feat/deck-glowup` off `main`.

## Layer 1 — Skin fidelity

### Cassette (biggest wins)

- **Tape spooling** — each reel gains a tape *pack* (dark tape ring around the hub)
  whose diameter is driven by the existing `--prog` var: left pack starts full and
  shrinks, right starts empty and grows. Reels restructure to `.reel > .pack + .hub`.
- **Hubs** — cream/ivory 6-tooth hubs (real cassette hubs), replacing the abstract
  grey spoke wheels. `--heat` glow retained on the hub.
- **Shell detail** — four corner screws; capstan + pinch-roller holes along the bottom
  edge; a faint trapezoid head-access outline bottom-centre; subtle top-edge ridge lines.
- **Sticker label** — off-white paper label with an accent-coloured top band, track title
  in `var(--font-display)` (Fraunces under editorial), thin ruled lines beneath (blank
  cassette-label look), small "A" side marker. Cover-art mode keeps the art with a
  sticker border.

### Vinyl

- **Light** — stronger *fixed* dual-band specular in `.gloss` (the room light the disc
  spins under), layered with a fainter rotating in-disc wedge so spin stays visible.
- **Groove detail** — 2–3 darker track-separator rings; a smooth run-out band between
  label and grooves; beveled outer rim (light ring + edge shadow).
- **Label** — pressed concentric ring texture; generic curved pressing text around the
  label edge ("45 RPM · STEREO", inline SVG `textPath`, no dynamic data — avoids new
  placeholders).

## Layer 2 — Cinematic FX (chips, per-skin defaults)

All toggleable; defaults applied when the skin is switched, manual overrides after.
CSS-driven FX wire through the existing-but-unused `deckFx(root, name, on)` helper
(`off-<name>` classes) — closing that deferred Minor. Canvas FX use the existing
`opts.fxOn(name)` callback.

| Chip | What | Default |
|---|---|---|
| `vhs` | animated grain (inline feTurbulence data-URI), occasional drifting tracking line, subtle chroma fringe (drop-shadow pair) | cassette ON, vinyl off |
| `vignette` | soft radial darkening pulling the eye centre-frame | vinyl ON, cassette off |
| `dust` | tiny slow white specks drifting near the disc (turntable.js particle type) | vinyl ON, cassette off |
| hue override | slider/chip forcing the spectrum hue (cover art still seeds it by default) | off (auto) |

- **Title card** — first ~1.5s of a recording: track / producer / BPM (line omitted if
  unknown) in `var(--font-display)`, fades out. Hooks the existing tab-capture record
  start. **End card** — optional 0.8s fade to producer name for a clean loop point.
  Both app-only (pack recipients don't record).
- **Pack parity** — packs inherit skins automatically via the shared module; `vhs` /
  `vignette` CSS ships in the inlined deck.css with the same per-skin defaults; `dust`
  rides `__VIZ_JS__`. No title/end cards in packs.

## Error handling

- Missing BPM/producer metadata → title card omits the line, never shows "undefined".
- FX are presentational: a failed/absent chip never blocks playback or recording.
- Unknown chip names in `deckFx` are inert (class toggle only).

## Testing

- pytest (string-level): pack build inlines new skin markup/CSS with zero placeholder
  leaks; self-contained invariant still pinned (no `src="http`/`href="http`/`url(http`,
  no `.woff2`); grain data-URI present; chip markup present in app index and pack HTML;
  per-skin default classes correct in initial markup.
- Existing 204 tests stay green throughout.
- Manual checklist (recorded in the wrap-up task): both skins at phone width, spooling
  arc over a full track, VHS look on cassette, dust+vignette on vinyl, title/end cards
  in a real recording, pack `index.html` from `file://` with skins + FX, classic theme
  unchanged.

## Out of scope

- OBS record studio, count-in, safe-area guide, recordings drawer (once-over spec §4 — Plan C).
- Management hub tabs (§3 — Plan B).
- New deck skins beyond vinyl/cassette; 3D/tilt effects.
