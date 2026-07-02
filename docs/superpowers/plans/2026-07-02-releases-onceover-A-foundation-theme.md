# Releases Once-Over — Plan A: Foundation + Theme

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modularize the releases web app (single shared deck module, dead code deleted) and ship the theme layer — `classic` preserved, `editorial` as the new default.

**Architecture:** Extract the deck (engine JS, CSS, markup, wiring) out of `web/index.html` and `pack.py`'s inline strings into `src/releases/web/deck/*` — the app serves them via a `/static` mount + server-side include; the pack export inlines the same files at build time (stays self-contained). Then all colors/type become CSS custom properties under `[data-theme]`, with `classic` = current palette and `editorial` = the approved brass/record-red/violet look.

**Tech Stack:** FastAPI (existing `[web]` extra), vanilla JS/CSS, pytest + TestClient. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-02-releases-onceover-design.md` (sections 1–2). Plans B (hub), C (record studio), D (reel FX + pack parity) follow after this lands.

## Global Constraints

- Branch: `feat/releases-onceover` (already created; spec committed `81e7492`).
- Run tests with `cd releases && .venv/bin/python -m pytest -q` — **baseline 193 passed** must never drop.
- Built packs must remain fully self-contained (no `http(s)://` references) — this is the monetize seam.
- The exported pack's current look must not change in this plan (its Editorial default arrives with `?theme=` in Task 6).
- Never touch `~/ProducerLibrary` (tests use tmp_path fixtures only).
- Commit after every task; message prefix `releases:`.
- **Extraction steps** reference exact source line ranges instead of pasting hundreds of existing lines; all *new* code is given in full. Line numbers are as of `81e7492` — re-locate by the quoted anchors if drifted.

---

### Task 1: Extract the turntable engine to `web/deck/turntable.js` (+ delete the dead export path)

**Files:**
- Create: `releases/src/releases/web/deck/turntable.js`
- Modify: `releases/src/releases/pack.py:100-429` (the `TURNTABLE_JS = r"""…"""` constant)
- Test: `releases/tests/test_pack.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `packmod.TURNTABLE_JS` (unchanged name, now file-loaded) — `webapp/app.py:161-164` and `render_index_html` keep working untouched. File exposes globals `ttRun(opts)`, `ttScrub(el, audio)` exactly as today.

- [ ] **Step 1: Write the failing tests** — append to `releases/tests/test_pack.py`:

```python
from pathlib import Path
from releases import pack as packmod

DECK_DIR = Path(packmod.__file__).parent / "web" / "deck"

def test_turntable_engine_is_a_real_file():
    src = (DECK_DIR / "turntable.js").read_text(encoding="utf-8")
    assert "function ttRun" in src and "function ttScrub" in src
    assert packmod.TURNTABLE_JS == src  # constant now loads from the file

def test_dead_canvas_export_engine_removed():
    assert "ttExportFrame" not in packmod.TURNTABLE_JS
    assert "ttVinylScene" not in packmod.TURNTABLE_JS
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_pack.py -q` → FAIL (`FileNotFoundError` on turntable.js; `ttExportFrame` present).

- [ ] **Step 3: Implement**
  1. Create `web/deck/turntable.js` containing the body of the `TURNTABLE_JS` raw string (`pack.py:103-363` — from `// shared turntable…` through the end of `ttScrub`), **excluding** the dead block `pack.py:364-428` (`ttExportFrame`/`ttVinylScene`/`ttCassetteScene`/`pauseDraw`/`bottomFx`).
  2. In `pack.py`, replace the whole constant with:

```python
_WEB_DIR = Path(__file__).parent / "web"
TURNTABLE_JS = (_WEB_DIR / "deck" / "turntable.js").read_text(encoding="utf-8")
```

  3. Add `"web/deck/*"` (and `"web/*"` patterns as needed) to the package-data globs in `releases/pyproject.toml` so the files ship with the package.

- [ ] **Step 4: Run full suite** — `.venv/bin/python -m pytest -q` → 195 passed (193 + 2 new).

- [ ] **Step 5: Commit** — `git add -A releases && git commit -m "releases: turntable engine to shared file; drop dead canvas-export path"`

---

### Task 2: Shared deck CSS + markup (`deck.css`, `deck.html`) consumed by app and pack

**Files:**
- Create: `releases/src/releases/web/deck/deck.css`, `releases/src/releases/web/deck/deck.html`
- Modify: `releases/src/releases/web/index.html:106-159` (deck CSS) and the `#ov` deck markup block (`index.html:283-309`, the `.deck` element and children)
- Modify: `releases/src/releases/webapp/app.py:135` (index route) + add `/static` mount
- Modify: `releases/src/releases/pack.py` `_PLAYER_TEMPLATE:453-506` (deck CSS copy) + deck markup + `render_index_html`
- Test: `releases/tests/test_webapp.py`, `releases/tests/test_pack.py`

**Interfaces:**
- Consumes: Task 1's `_WEB_DIR`.
- Produces: `deck.css` sizing driven by `--deck-size` (app sets `320px`, pack sets `330px` — current looks preserved exactly); `deck.html` fragment starting `<!-- deck-module -->` with the `.deck` subtree (`.vinyl`, `.arm`, `.label`, `.hole`, `.gloss`, `.cassette` with two `.reel`s + `.tape` + `.clabel`, canvases). Server-side include marker `<!--DECK-->` in `index.html`; template placeholders `__DECK_CSS__` / `__DECK_HTML__` in `pack.py`.

- [ ] **Step 1: Write the failing tests** — append to `test_webapp.py`:

```python
def test_index_includes_shared_deck(client):
    html = client.get("/").text
    assert "deck-module" in html          # server-side include ran
    assert "/static/deck/deck.css" in html

def test_static_deck_assets_served(client):
    assert client.get("/static/deck/deck.css").status_code == 200
    assert "--deck-size" in client.get("/static/deck/deck.css").text
```

and to `test_pack.py`:

```python
def test_pack_inlines_shared_deck(tmp_path, sample_pack):  # reuse existing pack fixture
    html = (sample_pack / "index.html").read_text(encoding="utf-8")
    assert "deck-module" in html and "__DECK_HTML__" not in html and "__DECK_CSS__" not in html
```

- [ ] **Step 2: Run to verify failure** — targeted pytest → FAIL (no marker, 404 on /static).

- [ ] **Step 3: Implement**
  1. `deck.css`: move the app's deck rules (`index.html:106-159`) verbatim, replacing the hardcoded `320px` with `var(--deck-size, 320px)`; prefix a `/* deck-module */` header comment. Keep selectors rooted at `.deck` (they already are).
  2. `deck.html`: move the `.deck` subtree markup out of `index.html`, prefixed with `<!-- deck-module -->`; leave `<!--DECK-->` in its place in `index.html`.
  3. `app.py`: add once, after app creation:

```python
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
```

  and change the index route to substitute the include:

```python
@app.get("/")
def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("<!--DECK-->", (WEB_DIR / "deck" / "deck.html").read_text(encoding="utf-8"))
    return HTMLResponse(html)
```

  4. `index.html`: add `<link rel="stylesheet" href="/static/deck/deck.css">` and `:root{--deck-size:320px}` (its current size).
  5. `pack.py`: delete the duplicated deck CSS (`453-506`) and deck markup from `_PLAYER_TEMPLATE`, insert `__DECK_CSS__` inside its `<style>` and `__DECK_HTML__` at the deck position; keep pack-specific `--deck-size:330px` and its reel-size rule in the template's own CSS. In `render_index_html` add:

```python
html = html.replace("__DECK_CSS__", (_WEB_DIR / "deck" / "deck.css").read_text(encoding="utf-8"))
html = html.replace("__DECK_HTML__", (_WEB_DIR / "deck" / "deck.html").read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run full suite** → all green (196+). Manual check: `releases web` — deck renders identically (vinyl + cassette + arm), skins toggle.

- [ ] **Step 5: Commit** — `releases: single shared deck module (css+markup) for app and pack`

---

### Task 3: Split app CSS/JS out of `index.html`; deck wiring to `deck/deck.js`

**Files:**
- Create: `releases/src/releases/web/css/app.css`, `releases/src/releases/web/js/app.js`, `releases/src/releases/web/deck/deck.js`
- Modify: `releases/src/releases/web/index.html` (drop inline `<style>`/`<script>`, add links); `releases/src/releases/pack.py` (player wiring `604-663` delegates to deck.js via `__DECK_JS__`)
- Test: `releases/tests/test_webapp.py`

**Interfaces:**
- Consumes: Tasks 1–2 modules.
- Produces: `deck.js` globals (pure DOM helpers, no fetch/app state):
  - `deckSetSkin(root, skin)` — `skin: "vinyl"|"cassette"`, toggles `cassette-mode`, persists nothing.
  - `deckSetPlaying(root, on)` — toggles `playing` class.
  - `deckSmokeAt(root)` → `[{x,y}]` (stylus tip on vinyl / hot reels on cassette — unifies the current `ovSmokeAt`/`smokeAt` duplicates).
  - `deckFx(root, name, on)` — toggles `off-<name>` for smoke/particles/shake/heat.
  App keeps app-only logic (tables, API calls, remix crew, reel modal, recording) in `js/app.js`. Pack template inlines `deck.js` then its small glue (`select/setPlaying` handlers).

- [ ] **Step 1: Write the failing test** — append to `test_webapp.py`:

```python
def test_app_assets_split_out(client):
    html = client.get("/").text
    assert '/static/css/app.css' in html and '/static/js/app.js' in html
    assert client.get("/static/deck/deck.js").text.count("function deckSetSkin") == 1
    assert "<style>" not in html.split("deck-module")[0]  # no inline app stylesheet left
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — move `index.html`'s `<style>` body (minus already-moved deck rules) to `css/app.css`; move the `<script>` body to `js/app.js` except the deck helpers listed above, which become `deck/deck.js` (rename call sites: `ovSmokeAt()` → `deckSmokeAt(ov)`, skin toggle → `deckSetSkin`). In `pack.py`, replace its duplicated helper functions (`604-663` portion that matches) with `__DECK_JS__` inlining `deck.js` + the pack's残 glue calling `deckSetSkin`/`deckSetPlaying`/`deckSmokeAt`.

- [ ] **Step 4: Full suite + manual check** (app: play, skin toggle, FX chips, remix crew, reel modal; pack build: play, skin, reel toggle).

- [ ] **Step 5: Commit** — `releases: split app css/js; deck wiring shared via deck.js`

---

### Task 4: Theme layer with `classic` extracted (zero visual change)

**Files:**
- Create: `releases/src/releases/web/css/themes.css`
- Modify: `releases/src/releases/web/css/app.css` (literals → `var(--*)`), `releases/src/releases/web/index.html` (`<html data-theme="classic">`, link themes.css), `releases/src/releases/web/js/app.js` (applyTheme)
- Test: `releases/tests/test_webapp.py`

**Interfaces:**
- Produces: token set (both themes MUST define all): `--bg --panel --line --txt --dim --accent --accent-ink --record --remix --good --warn --bad --font-display --radius-card`. JS: `applyTheme(name)` — sets `document.documentElement.dataset.theme`, saves `localStorage.theme`; boot reads `?theme=` → `localStorage` → default.

- [ ] **Step 1: Write the failing test:**

```python
import re

def test_theme_token_parity(client):
    css = client.get("/static/css/themes.css").text
    def tokens(theme):
        block = re.search(r'\[data-theme="%s"\]\s*{([^}]*)}' % theme, css).group(1)
        return set(re.findall(r"--[\w-]+", block))
    assert tokens("classic") == tokens("editorial") != set()

def test_app_css_uses_tokens_not_literals(client):
    assert "#c9a227" not in client.get("/static/css/app.css").text  # gold lives only in themes.css
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — `themes.css`:

```css
[data-theme="classic"]{
  --bg:#0d0d10; --panel:#16161c; --line:#26262f; --txt:#e7e7ea; --dim:#8a8a96;
  --accent:#c9a227; --accent-ink:#141414; --record:#f85149; --remix:#c9a227;
  --good:#3fb950; --warn:#d29922; --bad:#f85149;
  --font-display:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; --radius-card:0px;
}
[data-theme="editorial"]{ /* placeholder values = classic for now; real values in Task 5 */ … }
```

  (editorial block duplicates classic's values in this task so parity passes; Task 5 replaces them). Sweep `app.css` + `deck.css` replacing color literals with the vars. `applyTheme` in `app.js`:

```js
function applyTheme(name){document.documentElement.dataset.theme=name;try{localStorage.theme=name}catch(e){}}
applyTheme(new URLSearchParams(location.search).get("theme")||localStorage.theme||"classic");
```

- [ ] **Step 4: Full suite + manual check** — app must look pixel-identical to before.

- [ ] **Step 5: Commit** — `releases: theme layer; classic extracted verbatim as default`

---

### Task 5: Editorial theme (tokens, Fraunces, card restyle, header toggle, default flip)

**Files:**
- Create: `releases/src/releases/web/fonts/fraunces.woff2` (copy from `site/dist/_astro/fraunces-latin-wght-normal.ukD16Tqj.woff2` — OFL-licensed, already self-hosted on the EPK site)
- Modify: `themes.css` (real editorial tokens + `@font-face`), `app.css` (card rows, pills, header tabs shell, player bar — all via tokens), `index.html`/`app.js` (theme `<select>` in header), default flips to `editorial`
- Test: `releases/tests/test_webapp.py`

**Interfaces:**
- Consumes: Task 4 tokens + `applyTheme`.
- Produces: editorial token values (from the spec §2): `--bg:#0b0b0e --panel:#14141a --accent:#d4aa5e --record:#b3352c --remix:#b98ede --txt:#ecebe6 --dim:#96959e --font-display:"Fraunces",Georgia,serif --radius-card:12px`. Classic keeps flat table look via `--radius-card:0px` + its palette; structure (card DOM) is shared.

- [ ] **Step 1: Write the failing test:**

```python
def test_editorial_is_default_and_distinct(client):
    assert 'data-theme' in client.get("/").text  # boot attr present
    css = client.get("/static/css/themes.css").text
    assert "#d4aa5e" in css and "#b3352c" in css and "Fraunces" in css
    assert client.get("/static/fonts/fraunces.woff2").status_code == 200

def test_default_theme_is_editorial(client):
    # boot fallback in app.js flips to editorial
    assert '||"editorial"' in client.get("/static/js/app.js").text
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — copy the font; `@font-face{font-family:Fraunces;src:url(/static/fonts/fraunces.woff2) format("woff2");font-weight:100 900;font-display:swap}`; fill the editorial block with spec tokens; restyle rows to cards in `app.css` **using only tokens** (radius `var(--radius-card)`, hairline `var(--line)`, inset top highlight); small-caps letterspaced labels; mono for BPM/key/time; header `<select id="themepick">` wired to `applyTheme`; boot default `"editorial"`.

- [ ] **Step 4: Full suite + manual check** — editorial by default; picking Classic in the header instantly restores the familiar look; deck labels/accents follow theme.

- [ ] **Step 5: Commit** — `releases: editorial theme (brass/record-red/violet, Fraunces) with classic toggle`

---

### Task 6: Pack export theming + self-contained invariant

**Files:**
- Modify: `releases/src/releases/pack.py` (`render_index_html(theme="editorial")`, inline theme tokens, runtime `?theme=`), `releases/src/releases/cli.py:pack` (add `--theme` option, default `editorial`)
- Test: `releases/tests/test_pack.py`

**Interfaces:**
- Consumes: `themes.css` tokens (inlined at build — no font file embedded; `--font-display` falls back to Georgia in packs to keep zips light).
- Produces: `render_index_html(..., theme: str = "editorial")`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_pack_is_themed_and_self_contained(sample_pack):
    html = (sample_pack / "index.html").read_text(encoding="utf-8")
    assert 'data-theme="editorial"' in html and "--accent:#d4aa5e" in html
    assert "http://" not in html and "https://" not in html  # monetize seam: fully static
    assert ".woff2" not in html  # no font payload in packs

def test_pack_theme_flag(tmp_path, ...):  # build with theme="classic" → data-theme="classic"
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — inline both theme token blocks into the pack `<style>`; set `data-theme` from the arg; tiny inline script honors `?theme=`; CLI `--theme editorial|classic`.

- [ ] **Step 4: Full suite green.**

- [ ] **Step 5: Commit** — `releases: pack export themed (editorial default, ?theme= toggle), self-contained pinned`

---

### Task 7: Wrap-up — docs + full verification

**Files:**
- Modify: `releases/README.md` (asset layout, theme toggle, `--theme`), `releases/RECORDING.md` (unchanged flow, still valid)
- Test: full suite

- [ ] **Step 1:** Full suite: `.venv/bin/python -m pytest -q` → ~200 passed, 0 failed.
- [ ] **Step 2:** Manual verification checklist via `releases web`: classic theme matches memory of old UI; editorial default; deck vinyl+cassette skins; remix crew; reel modal + record button; build a pack (both themes) and open its index.html from disk (file://) — plays, skins toggle, no console errors.
- [ ] **Step 3:** Update README sections; note Plans B–D pending.
- [ ] **Step 4:** Commit — `releases: once-over plan A done (foundation + themes); docs updated`

---

## Self-review notes

- Spec §1 coverage: file split ✓ (T2–T3), shared deck ✓ (T1–T3), dead code ✓ (T1), theme scaffolding ✓ (T4). §2 coverage: tokens/type ✓ (T5), classic revert ✓ (T4–T5), pack `?theme=` ✓ (T6). §§3–6 are Plans B–D by design.
- "Classic = today's exact look": palette/type verbatim; row structure becomes cards in both themes with classic styled flat (radius 0, old palette). Full pixel-revert remains the branch. (Consistent with spec's revert model.)
- Type consistency: `deckSetSkin/deckSetPlaying/deckSmokeAt/deckFx` (T3) are the only cross-task JS names; `applyTheme` (T4) reused in T5; `render_index_html(theme=)` (T6) matches T2's substitution site.
