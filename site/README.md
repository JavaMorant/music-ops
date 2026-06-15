# site — Dibsss EPK

The music-side property: a fast, static, dark-editorial EPK (bio, mixes, highlight
reel, stats, dates, booking, downloadable press kit). Built with **Astro**, all
content in markdown/JSON so you edit copy without touching components.

## Edit your content (no code)

Everything you'll change lives in **`content/`**:

| File | What it controls |
|---|---|
| `content/site.json` | Name, tagline, location, **booking email**, social links |
| `content/bio.md` | Your bio (markdown — bold/italic/links work) |
| `content/mixes.json` | Embedded mixes (SoundCloud / Spotify embed URLs) |
| `content/highlights.json` | Highlight-reel videos (YouTube embed URLs) — empty hides the section |
| `content/dates.json` | Upcoming & past dates |
| `content/stats.json` | The selected-stats numbers |
| `content/press-kit/` | Files bundled into the downloadable `press-kit.zip` |

Change the booking email in one place: `content/site.json` → `bookingEmail`.

### Getting embed URLs
- **SoundCloud:** open a track/set → Share → Embed → copy the `src` that starts
  with `https://w.soundcloud.com/player/`.
- **Spotify:** Share → Embed → copy the `https://open.spotify.com/embed/...` URL.
- **YouTube** (highlights): use `https://www.youtube.com/embed/VIDEO_ID`.

### Press kit
Drop real files into `content/press-kit/` (bio, `photos/`, `logos/`, tech rider).
They're zipped into `public/press-kit.zip` automatically on every build.

## Run it

```bash
cd site
npm install
npm run dev        # http://localhost:4321 — live preview while editing
npm run build      # builds press-kit.zip + the static site into dist/
npm run preview    # serve the production build locally
```

## Deploy (Vercel, from this subfolder)

The repo is a monorepo, so point Vercel at this subdirectory:

1. https://vercel.com → **Add New… → Project** → import `JavaMorant/music-ops`.
2. Set **Root Directory** to `site`.
3. Framework preset auto-detects **Astro**; `vercel.json` here pins the build
   command (`npm run build`, so the press-kit zip is generated) and output
   (`dist`). Click **Deploy**.
4. Add your custom domain in the project's **Domains** settings, then update
   `site` in `astro.config.mjs` to that domain (used for absolute/OG/canonical
   URLs + the sitemap).

## Polish later
- **Link preview image:** `public/og.svg` is a placeholder. For best previews on
  iMessage/Twitter/WhatsApp (which often don't render SVG), drop a real
  1200×630 `public/og.png` (a press photo works) and change `/og.svg` →
  `/og.png` in `src/layouts/Base.astro`.
- Fonts are self-hosted (Fraunces + Inter via `@fontsource`) — no third-party
  request, works offline.

CLI alternative: `npm i -g vercel && vercel login && vercel --cwd site`.
