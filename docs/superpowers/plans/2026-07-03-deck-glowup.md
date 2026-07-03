# Deck Glow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the vinyl and cassette decks look premium in 9:16 phone video (TikTok/IG Reels): skin fidelity (spooling tape, real cassette shell, vinyl light/groove/label detail) + cinematic FX (VHS, vignette, dust, hue override, title/end cards).

**Architecture:** All skin + FX visuals live in the shared deck module (`releases/src/releases/web/deck/`) consumed by both the web app (server-side include + `/static`) and the exported pack (inlined via `__DECK_CSS__`/`__DECK_HTML__`/`__DECK_JS__`/`__VIZ_JS__` placeholders). CSS FX gate off host classes (`#ov` in the app, `<body>` in packs) which already carry `cassette-mode`, `playing`, and `off-<fx>` classes. Canvas FX extend `turntable.js`. Title/end cards are app-only (recording is app-only).

**Tech Stack:** Vanilla JS + CSS (no frameworks), FastAPI static/serving unchanged, pytest string-level assertions.

Spec: `docs/superpowers/specs/2026-07-03-deck-glowup-design.md`.

## Global Constraints

- Branch: `feat/deck-glowup` off `main` (created by the controller before Task 1).
- The repo working tree carries a parallel session's uncommitted `librarian/` + root `CLAUDE.md`/`TODO.md` changes — NEVER stage, commit, stash, or checkout-over them. Every `git add` must name explicit `releases/` or `docs/` paths. Never `git add -A` / `git add .`.
- Packs stay fully self-contained: built pack `index.html` must contain none of `src="http`, `href="http`, `url(http`, `.woff2` (pinned by `test_pack_is_themed_and_self_contained`). Inline `data:` URIs and SVG `xmlns='http://www.w3.org/2000/svg'` namespace tokens are fine.
- `pack.py`'s `_PLAYER_TEMPLATE` must contain ONLY ASCII straight quotes — no curly quotes U+2018/U+2019/U+201C/U+201D (pinned by the curly-quote guard in `test_pack.py`). Edit tools sometimes smart-quote; verify after editing.
- `deck.css` must NOT declare `:root{--deck-size}` (app sets 320px, pack 330px; deck.css only consumes `var(--deck-size,320px)`).
- Skins are theme-independent: no `[data-theme=…]` selectors in deck module files.
- Existing behaviours preserved: spin/pause (`.playing`), scrub, tap-to-play, tonearm `--prog` sweep, `--heat` glow, reel-mode scene scale, label cover-art mode (JS sets inline `background-image` + `.cover` class on `.label`/`.clabel` — skin CSS must keep paper/accent art as `background-image` layers so the inline override replaces them cleanly).
- Baseline: 204 tests green before Task 1. Run the full suite with `cd /Users/awandedibidi/dev/music-ops/releases && .venv/bin/python -m pytest -q` — there is NO system python.
- `deckSmokeAt` (deck.js) reads `#cassette .reel` centres and `#armtip` — keep `.reel` as the positioned 32%-wide wrapper and keep `#armtip`.

---

### Task 1: Cassette skin fidelity (spooling tape, cream hubs, shell detail, sticker label)

**Files:**
- Modify: `releases/src/releases/web/deck/deck.html`, `releases/src/releases/web/deck/deck.css`
- Test: `releases/tests/test_pack.py`, `releases/tests/test_webapp.py`

**Interfaces:**
- Consumes: `--prog` (0..1, set per-frame by turntable.js on the scene), `--heat` (0..1), `.playing` on host, `.cassette-mode` on host, `__CLABEL_HTML__` substitution.
- Produces: `.reel.l/.reel.r > .pack + .hub` structure; `--pk` custom prop. Task 3+ must not rename these.

- [ ] **Step 1: Write the failing tests.**

Append to `releases/tests/test_pack.py` (uses the existing `_track`/`_meta` helpers and `packmod` import):

```python
def test_pack_cassette_has_spooling_and_shell_detail(tmp_path):
    out = tmp_path / "out" / "p"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Beat", bpm=140)], out, _meta())
    html = (out / "index.html").read_text(encoding="utf-8")
    # tape packs spool with progress: left empties, right fills
    assert 'class="reel l"' in html and 'class="reel r"' in html
    assert 'class="pack"' in html and 'class="hub"' in html
    assert "--pk:calc(1 - var(--prog,0))" in html and "--pk:var(--prog,0)" in html
    # shell detail present
    assert 'class="screw s1"' in html and 'class="chole c1"' in html
```

Append to `releases/tests/test_webapp.py` inside the existing webapp test class (uses the `client` fixture returning `(TestClient, root)`):

```python
    def test_deck_css_serves_cassette_fidelity(self, client):
        c, _ = client
        css = c.get("/static/deck/deck.css").text
        assert "--pk:" in css and ".cassette .reel .hub" in css and ".cassette .screw" in css
```

- [ ] **Step 2: Run to verify failure.**

Run: `cd /Users/awandedibidi/dev/music-ops/releases && .venv/bin/python -m pytest tests/test_pack.py::test_pack_cassette_has_spooling_and_shell_detail tests/test_webapp.py -q`
Expected: the two new tests FAIL (assertions on missing markup); all pre-existing pass.

- [ ] **Step 3: Implement deck.html.** Replace the cassette block (keep the rest of the file byte-identical):

```html
  <div class="cassette" id="cassette">
    <i class="screw s1"></i><i class="screw s2"></i><i class="screw s3"></i><i class="screw s4"></i>
    __CLABEL_HTML__
    <div class="win">
      <div class="reel l"><div class="pack"></div><div class="hub"></div></div>
      <div class="tape"></div>
      <div class="reel r"><div class="pack"></div><div class="hub"></div></div>
    </div>
    <div class="chole c1"></div><div class="chole c2"></div><div class="chole c3"></div><div class="chole c4"></div>
  </div>
```

- [ ] **Step 4: Implement deck.css.** Replace the whole cassette section (everything from the `/* cassette skin */` comment through the `.playing .cassette .reel` rule) with:

```css
/* cassette skin (toggled with .cassette-mode) */
.cassette{position:absolute;left:6%;top:24%;width:88%;height:52%;border-radius:14px;display:none;z-index:1;
  background:
    repeating-linear-gradient(180deg,rgba(255,255,255,.028) 0 1.2%,transparent 1.2% 4%),
    linear-gradient(165deg,#3a3a45,#15151b);
  border:1px solid #000;
  box-shadow:0 16px 46px rgba(0,0,0,.6),inset 0 1px 0 rgba(255,255,255,.09),inset 0 0 0 2px rgba(255,255,255,.04);}
.cassette-mode .cassette{display:block;}
.cassette-mode .vinyl,.cassette-mode .arm,.cassette-mode .gloss{display:none;}
/* head-access opening along the bottom edge */
.cassette:after{content:"";position:absolute;left:29%;right:29%;bottom:0;height:13%;
  border:1px solid rgba(0,0,0,.6);border-bottom:none;border-radius:8px 8px 0 0;
  background:linear-gradient(180deg,rgba(0,0,0,.12),rgba(0,0,0,.3));}
.cassette .screw{position:absolute;width:4.2%;aspect-ratio:1;border-radius:50%;
  background:
    linear-gradient(45deg,transparent 44%,rgba(10,10,12,.9) 46% 54%,transparent 56%),
    radial-gradient(circle at 38% 32%,#5c5c68,#22222a);
  box-shadow:inset 0 0 0 1px #000,0 1px 1px rgba(255,255,255,.06);}
.cassette .screw.s1{left:1.8%;top:3.4%;}.cassette .screw.s2{right:1.8%;top:3.4%;}
.cassette .screw.s3{left:1.8%;bottom:3.4%;}.cassette .screw.s4{right:1.8%;bottom:3.4%;}
/* capstan + pinch-roller holes flanking the head opening */
.cassette .chole{position:absolute;bottom:4.5%;width:3%;aspect-ratio:1;border-radius:50%;
  background:radial-gradient(circle at 42% 36%,#101014,#000 70%);box-shadow:inset 0 0 0 1px rgba(255,255,255,.05);}
.cassette .chole.c1{left:20%;}.cassette .chole.c2{left:26.5%;}
.cassette .chole.c3{right:26.5%;}.cassette .chole.c4{right:20%;}
/* printed-sticker label: paper + accent band + ruled title lines + side marker */
.cassette .clabel{position:absolute;left:9%;right:9%;top:8%;height:26%;border-radius:5px;overflow:hidden;
  background-color:#ece5d2;
  background-image:linear-gradient(180deg,transparent 0 64%,rgba(70,50,20,.3) 64% 66%,transparent 66% 88%,rgba(70,50,20,.3) 88% 90%,transparent 90%);
  background-size:cover;background-position:center;
  display:flex;align-items:center;justify-content:center;padding-top:9%;
  color:#241c0c;font-weight:800;text-transform:uppercase;letter-spacing:.04em;
  font-family:var(--font-display,sans-serif);font-size:clamp(10px,3vmin,15px);
  box-shadow:inset 0 0 0 1px rgba(0,0,0,.25);}
.cassette .clabel:before{content:"";position:absolute;left:0;right:0;top:0;height:30%;
  background:linear-gradient(180deg,var(--accent),var(--accent));box-shadow:0 1px 0 rgba(0,0,0,.35);}
.cassette .clabel:after{content:"A";position:absolute;left:3.5%;top:5%;height:20%;aspect-ratio:1;
  background:rgba(0,0,0,.28);color:#f4efdf;border-radius:3px;display:flex;align-items:center;justify-content:center;
  font-size:55%;font-weight:800;}
.cassette .clabel.cover{color:transparent;box-shadow:inset 0 0 0 3px #ece5d2;}
.cassette .clabel.cover:before,.cassette .clabel.cover:after{display:none;}
.cassette .win{position:absolute;left:11%;right:11%;bottom:17%;height:44%;border-radius:10px;background:#0b0b0f;
  box-shadow:inset 0 0 0 2px #000,inset 0 5px 14px rgba(0,0,0,.7),0 0 0 1.5px rgba(255,255,255,.045);
  display:flex;align-items:center;justify-content:space-between;padding:0 8%;}
.cassette .reel{width:32%;aspect-ratio:1;border-radius:50%;position:relative;}
/* tape pack: wound tape around the hub; --pk 0..1 = how full this side is.
   The left pack empties (1 - prog) while the right fills (prog) — the whole
   track's arc is visible at a glance. */
.cassette .reel .pack{position:absolute;border-radius:50%;inset:calc(25% - var(--pk,.5)*25%);
  background:
    repeating-radial-gradient(circle at 50% 50%,rgba(255,255,255,.045) 0 1.5%,transparent 1.5% 3.5%),
    radial-gradient(circle at 42% 36%,#241a10 0 58%,#150e08 78%,#2c2114 96%,#0a0705 100%);
  box-shadow:inset 0 0 8px rgba(0,0,0,.85),0 0 4px rgba(0,0,0,.6);}
.cassette .reel.l .pack{--pk:calc(1 - var(--prog,0));}
.cassette .reel.r .pack{--pk:var(--prog,0);}
/* cream 6-tooth hub (spins; carries the --heat brake-disc glow) */
.cassette .reel .hub{position:absolute;inset:26%;border-radius:50%;
  background:repeating-conic-gradient(#ede6d3 0 34deg,#c7bfaa 34deg 60deg);
  box-shadow:inset 0 0 0 2px #8f8770,0 0 calc(var(--heat,0)*22px) rgba(255,45,0,calc(var(--heat,0)*0.8));
  animation:spin 1.7s linear infinite;animation-play-state:paused;}
.cassette .reel .hub:before{content:"";position:absolute;inset:0;border-radius:50%;opacity:calc(var(--heat,0)*0.7);
  background:radial-gradient(circle at 50% 50%,#ff5a1e 0 55%,#a01400 78%,transparent 80%);}
.cassette .reel .hub:after{content:"";position:absolute;left:50%;top:50%;width:16%;height:16%;margin:-8% 0 0 -8%;border-radius:50%;background:#0b0b0f;}
.cassette .tape{position:absolute;left:24%;right:24%;top:50%;height:3px;margin-top:-1.5px;background:#1c130b;box-shadow:0 1px 0 rgba(255,255,255,.05);}
.playing .cassette .hub{animation-play-state:running;}
```

Note the spin moved from `.reel` to `.hub` (`.playing .cassette .hub` replaces the old `.playing .cassette .reel` rule); the `.reel` wrapper stays 32%-wide and positioned so `deckSmokeAt` keeps working.

- [ ] **Step 5: Run the new tests, then the full suite.**

Run: `cd /Users/awandedibidi/dev/music-ops/releases && .venv/bin/python -m pytest -q`
Expected: all pass (baseline + 2 new).

- [ ] **Step 6: Manual sanity (report only, no commit gate):** `releases web`, open deck, toggle Cassette: packs visibly asymmetric at track start (left full), hubs cream + spinning when playing, label reads as a sticker. Note findings in the report.

- [ ] **Step 7: Commit.**

```bash
cd /Users/awandedibidi/dev/music-ops
git add releases/src/releases/web/deck/deck.html releases/src/releases/web/deck/deck.css releases/tests/test_pack.py releases/tests/test_webapp.py
git commit -m "releases: cassette skin fidelity — spooling tape packs, cream hubs, shell detail, sticker label"
```

---

### Task 2: Vinyl skin fidelity (fixed specular, groove bands, rim, label ring text)

**Files:**
- Modify: `releases/src/releases/web/deck/deck.html`, `releases/src/releases/web/deck/deck.css`
- Test: `releases/tests/test_pack.py`, `releases/tests/test_webapp.py`

**Interfaces:**
- Consumes: `.playing`, `__LABEL_HTML__`, `var(--accent)`, `var(--font-display)`.
- Produces: `.vinyl .lring` (decorative SVG ring text). No JS contract changes.

- [ ] **Step 1: Write the failing tests.**

Append to `releases/tests/test_pack.py`:

```python
def test_pack_vinyl_has_ring_text_and_groove_detail(tmp_path):
    out = tmp_path / "out" / "p"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Beat", bpm=140)], out, _meta())
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'class="lring"' in html and "45 RPM" in html      # pressed ring text
    assert "closest-side" in html                             # calibrated groove/rim rings
```

Append to `releases/tests/test_webapp.py` (same class as Task 1's test):

```python
    def test_deck_css_serves_vinyl_fidelity(self, client):
        c, _ = client
        css = c.get("/static/deck/deck.css").text
        assert ".vinyl .lring" in css and "closest-side" in css
```

- [ ] **Step 2: Run to verify failure.** Same commands as Task 1 Step 2 pattern; the two new tests FAIL.

- [ ] **Step 3: Implement deck.html.** Replace the vinyl line with (one line, SVG inline; `href="#lrp"` is a fragment reference, not a fetch):

```html
  <div class="vinyl" id="vinyl">__LABEL_HTML__<svg class="lring" viewBox="0 0 100 100" aria-hidden="true"><defs><path id="lrp" d="M50,50 m-23.5,0 a23.5,23.5 0 1,1 47,0 a23.5,23.5 0 1,1 -47,0"/></defs><text><textPath href="#lrp">45 RPM · STEREO · LONG PLAY · 45 RPM · STEREO ·</textPath></text></svg><div class="hole"></div></div>
```

- [ ] **Step 4: Implement deck.css.** Replace the `.vinyl{…}` rule, the `.vinyl .label{…}` rule, and the `.gloss{…}` + `@keyframes sheen` rules with:

```css
.vinyl{position:absolute;left:14%;top:14%;width:72%;height:72%;border-radius:50%;
  background:
    conic-gradient(from 0deg,rgba(255,255,255,0) 0deg,rgba(255,255,255,.05) 30deg,rgba(255,255,255,0) 72deg,rgba(255,255,255,0) 205deg,rgba(255,255,255,.035) 235deg,rgba(255,255,255,0) 280deg),
    radial-gradient(circle closest-side at 50% 50%,
      transparent 0 40.5%,#101014 40.5% 46%,
      transparent 46% 57.5%,rgba(0,0,0,.42) 57.5% 58.6%,
      transparent 58.6% 69.5%,rgba(0,0,0,.42) 69.5% 70.6%,
      transparent 70.6% 80.5%,rgba(0,0,0,.42) 80.5% 81.6%,
      transparent 81.6% 97.2%,rgba(255,255,255,.13) 97.2% 97.9%,
      #000 97.9% 100%),
    repeating-radial-gradient(circle at 50% 50%,#0c0c0e 0 0.85%,#191920 0.85% 1.7%),
    radial-gradient(circle at 38% 32%,#2a2a31,#000 72%);
  box-shadow:0 16px 46px rgba(0,0,0,.6),inset 0 0 0 2px #000,inset 0 0 16px 3px rgba(255,255,255,.05);
  animation:spin 3.4s linear infinite;animation-play-state:paused;cursor:pointer;}
```

(The faint in-disc wedges stay as the motion cue; the run-out band 40.5–46%, three track-gap rings, and the rim highlight/edge are new. `closest-side` calibrates percentages to the disc radius.)

```css
.vinyl .label{position:absolute;left:30%;top:30%;width:40%;height:40%;border-radius:50%;
  background-color:#8a6f17;
  background-image:
    repeating-radial-gradient(circle closest-side at 50% 50%,rgba(0,0,0,.06) 0 7%,transparent 7% 14%),
    radial-gradient(circle at 50% 34%,var(--accent),#8a6f17);
  color:#1a1405;display:flex;
  align-items:center;justify-content:center;font-weight:800;letter-spacing:.05em;text-transform:uppercase;
  font-size:clamp(11px,3.5vmin,18px);padding:6%;overflow:hidden;text-align:center;
  box-shadow:inset 0 0 0 2px rgba(0,0,0,.25),0 0 0 3px rgba(255,255,255,.1);background-size:cover;background-position:center;}
```

```css
/* pressed ring text just outside the label — spins with the record */
.vinyl .lring{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;}
.vinyl .lring text{fill:rgba(255,255,255,.3);font-size:3.4px;letter-spacing:.62px;font-weight:600;
  font-family:var(--font-display,serif);text-transform:uppercase;}
```

```css
/* fixed room-light: two soft specular bands the disc spins under */
.gloss{position:absolute;left:14%;top:14%;width:72%;height:72%;border-radius:50%;pointer-events:none;z-index:2;
  background:
    conic-gradient(from 0deg at 50% 50%,
      transparent 0deg 18deg,rgba(255,255,255,.13) 38deg,transparent 58deg,
      transparent 196deg,rgba(255,255,255,.09) 218deg,transparent 240deg),
    linear-gradient(115deg,transparent 44%,rgba(255,255,255,.05) 50%,transparent 56%);
  animation:sheen 5.2s ease-in-out infinite;}
@keyframes sheen{0%,100%{opacity:.55;}50%{opacity:1;}}
```

- [ ] **Step 5: Full suite.** Expected: all pass. If the ring text overlaps itself or gaps at the seam in the browser, tune ONLY `font-size`/`letter-spacing` in `.vinyl .lring text` (path length ≈ 147.6 units).

- [ ] **Step 6: Commit.**

```bash
cd /Users/awandedibidi/dev/music-ops
git add releases/src/releases/web/deck/deck.html releases/src/releases/web/deck/deck.css releases/tests/test_pack.py releases/tests/test_webapp.py
git commit -m "releases: vinyl skin fidelity — fixed specular, groove bands, rim bevel, label ring text"
```

---

### Task 3: Cinematic CSS FX — VHS + vignette overlays, chips, pack parity

**Files:**
- Modify: `releases/src/releases/web/deck/deck.css`, `releases/src/releases/web/index.html`, `releases/src/releases/web/css/app.css`, `releases/src/releases/pack.py`
- Test: `releases/tests/test_pack.py`, `releases/tests/test_webapp.py`

**Interfaces:**
- Consumes: host classes `cassette-mode` / `off-<fx>` (app host `#ov`, pack host `<body>`); existing fx-chip wiring (`data-fx` chips toggle `off-<name>` on the host; `#reelopts [data-rofx]` chips mirror it).
- Produces: `.fx-vig` / `.fx-vhs` overlay elements + CSS; chips `data-fx="vhs"`, `data-fx="vignette"`, `data-fx="dust"` (dust chip is markup-only until Task 4). Defaults: VHS shows only in cassette mode; vignette shows in both skins; all default ON (no `off-` class initially).

- [ ] **Step 1: Write the failing tests.**

Append to `releases/tests/test_pack.py`:

```python
def test_pack_has_cinematic_fx_layer(tmp_path):
    out = tmp_path / "out" / "p"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Beat", bpm=140)], out, _meta())
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'class="fx-vhs"' in html and 'class="fx-vig"' in html
    assert 'data-fx="vhs"' in html and 'data-fx="vignette"' in html and 'data-fx="dust"' in html
    assert "vhstrack" in html            # tracking-line animation inlined via deck.css
    # still self-contained
    assert 'src="http' not in html and 'href="http' not in html and "url(http" not in html
```

Append to `releases/tests/test_webapp.py` (same class):

```python
    def test_app_has_cinematic_fx_layer(self, client):
        c, _ = client
        page = c.get("/").text
        assert 'class="fx-vhs"' in page and 'class="fx-vig"' in page
        assert 'data-fx="vhs"' in page and 'data-fx="vignette"' in page
        css = c.get("/static/deck/deck.css").text
        assert ".off-vhs .fx-vhs" in css and ".off-vignette .fx-vig" in css
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement deck.css.** Append at the end of the file:

```css
/* ---- cinematic FX overlays. Consumers place <div class="fx-vig"> + <div class="fx-vhs">
   in their scene; the host (#ov in the app, <body> in packs) carries the skin and
   off-<fx> classes, so gating works identically in both. ---- */
.fx-vig,.fx-vhs{position:absolute;inset:0;pointer-events:none;}
/* lens vignette — both skins, default on */
.fx-vig{background:radial-gradient(120% 88% at 50% 44%,transparent 54%,rgba(0,0,0,.42) 100%);}
.off-vignette .fx-vig{display:none;}
/* VHS: animated grain + scanlines (cassette only, default on) */
.fx-vhs{display:none;opacity:.16;mix-blend-mode:overlay;animation:vhsjit .45s steps(3) infinite;
  background:
    repeating-linear-gradient(180deg,rgba(0,0,0,.14) 0 1px,transparent 1px 3px),
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='vn'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23vn)'/%3E%3C/svg%3E");}
.cassette-mode .fx-vhs{display:block;}
.off-vhs .fx-vhs{display:none;}
@keyframes vhsjit{0%{background-position:0 0,0 0;}50%{background-position:0 1px,-40px 55px;}100%{background-position:0 0,70px -35px;}}
/* occasional tracking line drifting down the frame */
.fx-vhs:after{content:"";position:absolute;left:0;right:0;top:-6%;height:2.5px;opacity:0;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.5) 18% 82%,transparent);
  box-shadow:0 0 8px 2px rgba(255,255,255,.25),0 4px 14px 4px rgba(120,220,255,.12);
  animation:vhstrack 6.5s linear infinite;}
@keyframes vhstrack{0%{top:-6%;opacity:0;}1.5%{opacity:.5;}10%{top:104%;opacity:.35;}10.1%,100%{opacity:0;}}
/* chroma fringe on the deck while VHS is on */
.cassette-mode:not(.off-vhs) .deck{filter:drop-shadow(1.4px 0 0 rgba(255,0,70,.16)) drop-shadow(-1.4px 0 0 rgba(0,255,220,.14));}
```

- [ ] **Step 4: Implement app side.** In `releases/src/releases/web/index.html`:
  - After `<div class="fx"></div>` (inside `#ov`) add: `<div class="fx-vig"></div><div class="fx-vhs"></div>`
  - In BOTH the `#fxbar` chips row and the `#reelopts` `.rofx` chips row, after the Heat chip add:
    `<button class="fxchip" data-fx="vhs">VHS</button><button class="fxchip" data-fx="vignette">Vignette</button><button class="fxchip" data-fx="dust">Dust</button>` (in `#reelopts` use `data-rofx` instead of `data-fx` — the existing mirroring wiring picks them up automatically).

  In `releases/src/releases/web/css/app.css`, next to the `#ov .fx` rule add:

```css
  #ov .fx-vig,#ov .fx-vhs{position:absolute;inset:0;z-index:6;}
```

- [ ] **Step 5: Implement pack side.** In `releases/src/releases/pack.py` `_PLAYER_TEMPLATE`:
  - In the fxbar, after the Heat chip add the same three `data-fx` chips (VHS / Vignette / Dust).
  - Before `<div class="fx"></div>` (body children) add: `<div class="fx-vig"></div><div class="fx-vhs"></div>`
  - In the template CSS, next to the `.fx{…}` rule add: `.fx-vig,.fx-vhs{position:fixed;inset:0;z-index:6;}`
  - ASCII quotes only; the deck.css additions arrive automatically via `__DECK_CSS__`.

- [ ] **Step 6: Full suite.** Expected: all pass (self-contained + curly-quote guards included).

- [ ] **Step 7: Commit.**

```bash
cd /Users/awandedibidi/dev/music-ops
git add releases/src/releases/web/deck/deck.css releases/src/releases/web/index.html releases/src/releases/web/css/app.css releases/src/releases/pack.py releases/tests/test_pack.py releases/tests/test_webapp.py
git commit -m "releases: cinematic FX layer — VHS grain/tracking/chroma (cassette), vignette; chips in app + pack"
```

---

### Task 4: Canvas FX — dust motes + hue override

**Files:**
- Modify: `releases/src/releases/web/deck/turntable.js`, `releases/src/releases/web/js/app.js`, `releases/src/releases/web/index.html`, `releases/src/releases/web/css/app.css`
- Test: `releases/tests/test_pack.py`, `releases/tests/test_webapp.py`

**Interfaces:**
- Consumes: `on(name)` fx gate and `drawFX()` in turntable.js; `#reelopts` panel; `window` for shared state.
- Produces: `opts.hueOverride` (optional callback returning `null` for auto or a 0–360 number) — packs don't pass it and keep auto; `dustSpawn` gated by `on('dust')` + vinyl skin + playing.

- [ ] **Step 1: Write the failing tests.**

Append to `releases/tests/test_pack.py`:

```python
def test_pack_viz_has_dust_and_hue_override(tmp_path):
    out = tmp_path / "out" / "p"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Beat", bpm=140)], out, _meta())
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "dustSpawn" in html and "hueOverride" in html   # shared viz inlined via __VIZ_JS__
```

Append to `releases/tests/test_webapp.py` (same class):

```python
    def test_app_has_dust_and_hue_controls(self, client):
        c, _ = client
        assert "dustSpawn" in c.get("/api/turntable.js").text
        page = c.get("/").text
        assert 'id="ro-hue"' in page and 'id="ro-hueauto"' in page
        assert "hueOverride" in c.get("/static/js/app.js").text
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement turntable.js.**

3a. State (line ~5, extend the particle arrays declaration): add `dust=[]` to the `var sparks=[], embers=[], …` list.

3b. Hue override — replace line `hue=(hue+0.04+energy*0.45)%360;  // a touch slower …` with:

```js
    var hov=opts.hueOverride?opts.hueOverride():null;  // reel hue chip: pin the palette
    if(hov!=null&&isFinite(hov))hue=Number(hov); else hue=(hue+0.04+energy*0.45)%360;  // a touch slower so the colour drift reads as calm, not strobing
```

3c. Spawn — in `frame()`, right after the line computing `var cassette=opts.getSkin&&opts.getSkin()==='cassette';` add:

```js
    if(playing&&!cassette&&fxParticles&&on('dust'))dustSpawn();  // vinyl only: dust catching the light
```

3d. Add the spawn function next to `ambient()`:

```js
  // slow-drifting dust motes catching the light — vinyl only
  function dustSpawn(){ if(dust.length>=36||Math.random()>0.3)return;
    dust.push({x:Math.random()*fxw,y:Math.random()*fxh,vx:(Math.random()*2-1)*fxw*0.00008,
      vy:-(fxh*0.00012)*(0.4+Math.random()),t:0,dt:0.0016+Math.random()*0.002,
      sz:ref*(0.0016+Math.random()*0.0034),ph:Math.random()*6.2832,tw:2+Math.random()*3});}
```

3e. Render — in `drawFX()`, immediately before the `if(bokeh.length){…}` block (dust sits behind the bokeh):

```js
    if(dust.length){c.save();
      for(var di=dust.length-1;di>=0;di--){var d=dust[di];d.t+=d.dt;
        if(d.t>=1){dust.splice(di,1);continue;}
        d.x+=d.vx+Math.sin(d.t*6.2832*d.tw+d.ph)*fxw*0.00018;d.y+=d.vy;
        var da=Math.sin(d.t*3.14159)*(0.1+0.16*Math.sin(d.t*6.2832*d.tw*1.7+d.ph));
        if(da<=0)continue;
        c.globalAlpha=Math.min(0.3,da);c.fillStyle='rgba(255,248,235,1)';
        c.beginPath();c.arc(d.x,d.y,d.sz,0,6.2832);c.fill();}
      c.restore();c.globalAlpha=1;}
```

- [ ] **Step 4: Implement app side.**

4a. `releases/src/releases/web/index.html` — in `#reelopts`, after the Dancers row add:

```html
      <div class="reelrow"><span>Hue</span>
        <div class="rofx"><button class="fxchip" id="ro-hueauto" onclick="reelHueAuto()">Auto</button>
        <input type="range" id="ro-hue" min="0" max="360" value="42" oninput="reelHueSet(this.value)"></div></div>
```

4b. `releases/src/releases/web/js/app.js`:
  - Near the `let PRODUCER…` declarations add: `window.__fxHue = null;  // null = auto (cover-art seeded, drifting)`
  - In the `ttRun({…})` options object add one line after `getCover:()=>COVER,`:
    `hueOverride:()=>window.__fxHue,`
  - Add near the other reel panel functions:

```js
function reelHueAuto(){ window.__fxHue=null; document.getElementById('ro-hueauto').classList.remove('off'); }
function reelHueSet(v){ window.__fxHue=Number(v); document.getElementById('ro-hueauto').classList.add('off'); }
```

  - In the reel-panel open/sync code (where `#reelopts [data-rofx]` chip states are synced) add:
    `document.getElementById('ro-hueauto').classList.toggle('off', window.__fxHue!=null);`

4c. `releases/src/releases/web/css/app.css` — next to the `.rofx` / reel-panel styles add:

```css
  #ro-hue{accent-color:var(--accent);width:130px;}
```

- [ ] **Step 5: Full suite.** Expected: all pass.

- [ ] **Step 6: Commit.**

```bash
cd /Users/awandedibidi/dev/music-ops
git add releases/src/releases/web/deck/turntable.js releases/src/releases/web/js/app.js releases/src/releases/web/index.html releases/src/releases/web/css/app.css releases/tests/test_pack.py releases/tests/test_webapp.py
git commit -m "releases: canvas FX — vinyl dust motes + reel hue override"
```

---

### Task 5: Title card + end card (recorded intro/outro, app-only)

**Files:**
- Modify: `releases/src/releases/web/index.html`, `releases/src/releases/web/css/app.css`, `releases/src/releases/web/js/app.js`
- Test: `releases/tests/test_webapp.py`

**Interfaces:**
- Consumes: `DECK[deckCur]` (`.name`, `.bpm`, `.genre`), `PRODUCER`, `#ov` off-class chip system, `exportReel()` recorder, `reelGo()`/`reelSrc.onended` (reel-mode Web Audio path).
- Produces: `showTitleCard()`, `showEndCard(done)`; chips `titlecard`/`endcard` (reel panel only, default ON).

- [ ] **Step 1: Write the failing test.** Append to `releases/tests/test_webapp.py` (same class):

```python
    def test_app_has_title_and_end_cards(self, client):
        c, _ = client
        page = c.get("/").text
        assert 'id="tcard"' in page and 'id="ecard"' in page
        assert 'data-rofx="titlecard"' in page and 'data-rofx="endcard"' in page
        js = c.get("/static/js/app.js").text
        assert "showTitleCard" in js and "showEndCard" in js
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement index.html.**
  - Inside `#ov`, after `<div class="flash"></div>` add:

```html
  <div class="tcard" id="tcard"><div class="tc-title" id="tc-title"></div><div class="tc-meta" id="tc-meta"></div></div>
  <div class="ecard" id="ecard"><div class="ec-name" id="ec-name"></div></div>
```

  - In the `#reelopts` `.rofx` chips row (after the Dust chip) add:
    `<button class="fxchip" data-rofx="titlecard">Title card</button><button class="fxchip" data-rofx="endcard">End card</button>`

- [ ] **Step 4: Implement app.css.** Next to the other `#ov` layers add:

```css
  #ov .tcard,#ov .ecard{position:fixed;inset:0;z-index:9;display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:10px;background:rgba(5,5,8,.88);opacity:0;pointer-events:none;transition:opacity .35s ease;}
  #ov .tcard.show,#ov .ecard.show{opacity:1;}
  #ov .tc-title,#ov .ec-name{font-family:var(--font-display);font-size:clamp(28px,6vmin,54px);font-weight:700;color:var(--txt);letter-spacing:.01em;text-align:center;padding:0 6vw;}
  #ov .tc-meta{font-size:clamp(13px,2.2vmin,18px);color:var(--accent);letter-spacing:.14em;text-transform:uppercase;}
```

- [ ] **Step 5: Implement app.js.** Add above `exportReel()`:

```js
// ---- recorded intro/outro cards (reel-panel chips: titlecard / endcard, default on) ----
function cardOn(n){ return !document.getElementById('ov').classList.contains('off-'+n); }
function showTitleCard(){
  if(!cardOn('titlecard')) return;
  const t = DECK[deckCur]; if(!t) return;
  document.getElementById('tc-title').textContent = t.name || '';
  document.getElementById('tc-meta').textContent =
    [PRODUCER, t.bpm ? t.bpm + ' BPM' : '', (t.genre && t.genre !== 'unknown') ? t.genre : ''].filter(Boolean).join(' · ');
  const el = document.getElementById('tcard');
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 1500);
}
function showEndCard(done){
  if(!cardOn('endcard')){ if(done) done(); return; }
  document.getElementById('ec-name').textContent = PRODUCER;
  const el = document.getElementById('ecard');
  el.classList.add('show');
  setTimeout(() => { el.classList.remove('show'); if(done) done(); }, 1100);
}
```

Hooks (exact edits):
  - In `exportReel()`, after the `rec.start();` line add: `showTitleCard();`
  - In `exportReel()`, change the guard timeout from `dur*1000 + 1000` to `dur*1000 + 2600` (room for the end card).
  - In `exportReel()`, replace the ended listener line with:

```js
  audioEl.addEventListener('ended', () => { clearTimeout(guard); showEndCard(() => { if(rec.state !== 'inactive') rec.stop(); }); }, {once:true});
```

  - In `reelGo()`, after `reelSrc.start(0); window.__reelPlaying=true; ovSetPlaying(true);` add: `showTitleCard();`
  - In `reelGo()`, replace `reelSrc.onended = ()=>{ window.__reelPlaying=false; ovSetPlaying(false); };` with:

```js
    reelSrc.onended = ()=>{ window.__reelPlaying=false; ovSetPlaying(false); showEndCard(); };
```

  Note: the natural track-end autoplay (`audioEl` `ended` → `deckSelect(deckCur+1)`) only runs when NOT recording — the exportReel `{once:true}` listener stops the recorder; do not touch the autoplay listener.

- [ ] **Step 6: Full suite.** Expected: all pass.

- [ ] **Step 7: Commit.**

```bash
cd /Users/awandedibidi/dev/music-ops
git add releases/src/releases/web/index.html releases/src/releases/web/css/app.css releases/src/releases/web/js/app.js releases/tests/test_webapp.py
git commit -m "releases: recorded title + end cards for reels (chips, default on)"
```

---

### Task 6: Docs + full verification

**Files:**
- Modify: `releases/README.md`
- Test: full suite

- [ ] **Step 1:** Full suite: `cd /Users/awandedibidi/dev/music-ops/releases && .venv/bin/python -m pytest -q` → all pass, no new warnings.
- [ ] **Step 2:** Update `releases/README.md`: deck section gains the new skin detail (spooling tape packs, sticker label, vinyl ring text), the FX chip list (Smoke/Particles/Shake/Heat/VHS/Vignette/Dust + reel-panel Hue/Title card/End card), and a note that packs inherit skins + VHS/vignette/dust (no cards/hue UI in packs). Keep claims honest — describe only what shipped.
- [ ] **Step 3:** Write the manual browser checklist into the task report (controller relays it to the user): both skins at phone width; cassette left pack full at 0:00 / right full at end; VHS grain+tracking+chroma visible on cassette and killable via chip; vignette+dust on vinyl; hue slider pins the palette, Auto restores drift; record a reel → title card at start, end card at end, both absent when chips off; build a pack → open `index.html` from `file://` → skins + FX work, no console errors; classic theme unchanged.
- [ ] **Step 4:** Commit — `releases: deck glow-up docs`

```bash
cd /Users/awandedibidi/dev/music-ops
git add releases/README.md
git commit -m "releases: deck glow-up docs"
```

---

## Self-review notes

- Spec coverage: cassette spooling/hubs/shell/sticker ✓ (T1), vinyl light/grooves/rim/label ✓ (T2), VHS+vignette+chips+pack parity ✓ (T3), dust+hue ✓ (T4), title/end cards ✓ (T5), docs+checklist ✓ (T6). Spec deviation (deliberate): vignette defaults ON for both skins (spec table said vinyl-only default) — it flatters the cassette too and one rule is simpler; VHS is cassette-only by CSS gating rather than a per-skin default toggle.
- Type consistency: `.reel.l/.r > .pack/.hub` (T1) referenced nowhere else by JS except `deckSmokeAt('#cassette .reel')` which keeps working; `fx-vig`/`fx-vhs` class names match between deck.css (T3), index.html (T3), pack.py (T3); `hueOverride` name matches turntable.js (T3d) ↔ app.js (T4b); `showTitleCard`/`showEndCard` defined and hooked in the same task (T5).
- No placeholders: every code step carries the actual code.
- The `--pk:calc(1 - var(--prog,0))` pattern: custom properties may hold calc() and resolve at use inside `inset:calc(25% - var(--pk,.5)*25%)`; `--prog` is set as a unitless number string by turntable.js (`setProperty('--prog',pr.toFixed(4))`) which multiplies fine in calc.
