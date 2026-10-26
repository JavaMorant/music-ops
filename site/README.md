# site — Dibsss EPK

The music-side property: a fast, static EPK skinned to match the Dibsss press kit
(black, Anton + Oswald, all-caps, full-bleed photos). Hero, about, sets, photos,
numbers, booking, downloadable press kit. Built with **Astro**, all content in
markdown/JSON so you edit copy without touching components. Every section below
the hero hides itself while its content file is empty, so nothing unfinished
reaches the live page.

## Edit your content (no code)

Everything you'll change lives in **`content/`**:

| File | What it controls |
|---|---|
| `content/site.json` | Name, tagline, location, **booking email**, social links |
| `content/bio.md` | Your bio (markdown — bold/italic/links work) |
| `content/sets.json` | Recorded sets (SoundCloud / Mixcloud / YouTube embeds) — section appears once an item has an `embedUrl` |
| `content/photos.json` | The photo gallery (files in `public/photos/`) |
| `content/stats.json` | The numbers tiles |
| `content/productions.json` | Releases / remixes — empty hides the section |
| `content/highlights.json` | Highlight-reel videos (YouTube embed URLs) — empty hides the section |
| `content/dates.json` | Upcoming & past dates — empty hides the section |
| `content/collab.json` | Collaborate section — empty `heading` hides it |
| `content/press-kit/` | Files bundled into the downloadable `press-kit.zip` |

Change the booking email in one place: `content/site.json` → `bookingEmail`.

### Getting embed URLs
- **SoundCloud:** open a track/set → Share → Embed → copy the `src` that starts
  with `https://w.soundcloud.com/player/`.
- **Mixcloud:** Share → Embed → copy the `https://player-widget.mixcloud.com/widget/iframe/?feed=...` URL.
- **Spotify:** Share → Embed → copy the `https://open.spotify.com/embed/...` URL.
- **YouTube:** use `https://www.youtube.com/embed/VIDEO_ID`.

### Press kit
`content/press-kit/` holds `bio.md`, the 8 press photos and (once exported from
the design canvas) `Dibsss-press-kit.pdf`. They're zipped into
`public/press-kit.zip` automatically on every build. Photos shown on the site
live in `public/photos/` — keep the two folders in step.

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
- **Link preview image:** `public/og.jpg` is a 1200×630 crop of the cover photo.
  Swap the file to change it.
- Fonts are self-hosted (Anton + Oswald via `@fontsource`) — no third-party
  request, works offline.

CLI alternative: `npm i -g vercel && vercel login && vercel --cwd site`.
