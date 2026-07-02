# Recording the player with OBS — best quality, vertical for TikTok / Reels / Shorts

The in-app **● Record reel** button (tab capture → mp4) is the quick path, but
browser tab-capture is lossy. For posting-quality clips, record the player with
**OBS** — it captures the real on-screen render at full bitrate and 60 fps.

The trick that makes this painless: the player is responsive, so a **portrait
browser window + Clean mode** auto-frames the deck for 9:16 (deck centred, song +
play/pause bar pinned at the bottom, dark background filling the rest). OBS then
window-captures it straight into a 1080×1920 canvas with **no cropping**.

---

## TL;DR (once set up)

1. Resize Chrome to a tall, narrow window (roughly phone-shaped).
2. Open the player vertical: `http://127.0.0.1:8765/?deck` → hit **⛶ Clean**
   (or load `?deck&clean`). For the remix crew, use **⤢ Reel** + **👥 Show crew**.
3. OBS → **Start Recording** → in the player press play (or remix) → **Stop** at the end.
4. The `.mov`/`.mp4` lands in your OBS recording folder, ready to post.

---

## One-time OBS setup

**Video** (Settings → Video)
- Base (Canvas) Resolution: **1080×1920**
- Output (Scaled) Resolution: **1080×1920**
- FPS: **60** (matches the visualizer's animation; smooth motion is the whole point)

**Recording output** (Settings → Output → set *Output Mode: Advanced* → *Recording* tab)
- Recording Format: **MOV** (or MKV) — crash-safe; remux to MP4 afterward via
  *File → Remux Recordings*. (Plain MP4 is fine too if OBS stops cleanly.)
- Audio Encoder: **AAC**, 320 kbps
- Video Encoder: **Apple VT H.264 Hardware Encoder** (Apple Silicon — fast, clean,
  low CPU). HEVC makes smaller files but H.264 MP4 is the most TikTok-proof.
- Rate Control: **CQP**, CQ level **18–20** (lower = better; 18 is near-transparent),
  or **CBR 30–40 Mbps** if you prefer a fixed size.
- Keyframe Interval: **2s** · Profile: **high**

**Audio sample rate** (Settings → Audio): **48 kHz**, Channels **Stereo**.

---

## Capturing the player

**Video — the window**
- Sources → **+** → **macOS Screen Capture** → Method: **Window** → pick the Chrome
  window with the player.
- Uncheck **Show cursor**.
- Window capture keeps rendering even when Chrome isn't the focused app (so you can
  click into OBS without the deck freezing — unlike browser tab-capture).
- Right-click the source → **Transform → Fit to screen**. If Chrome's tab/title bar
  shows at the top, **Alt-drag** (Option-drag) the top edge of the source down to
  crop it off. The player's dark background hides any small gaps.
  - Cleaner still: put Chrome in **fullscreen** first (View → Enter Full Screen) so
    there's no browser chrome at all — then use **macOS Screen Capture → Display**
    and frame the deck. (On a landscape monitor this crops the sides; Clean mode
    keeps the deck centred so that's fine.)

**Audio — only the beat, no system sounds**
- Sources → **+** → **macOS Application Audio Capture** → choose **Google Chrome**.
  This grabs just the player's audio — no notifications, no virtual cable needed.
  (Requires a recent macOS, which you have.)
- *Fallback on older macOS:* install **BlackHole 2ch**, make a **Multi-Output
  Device** (BlackHole + your speakers) in Audio MIDI Setup, send Chrome's output
  there, and add an **Audio Input Capture (BlackHole 2ch)** source in OBS.
- Mute OBS's **Desktop Audio** so you don't double-capture / pick up other apps.

---

## Per-recording flow

1. **Shape the window.** Drag Chrome to a tall, narrow size (phone-ish). The deck
   reframes to `min(84vw, 56vh)` and the song bar pins to the bottom — that's your
   9:16 frame.
2. **Pick the look:**
   - **Clean** (⛶) — just the deck + song/play-pause bar. Best for a single beat.
   - **Reel** (⤢) — the zoomed deck; add **👥 Show crew** to record the stem-remix
     dancers around the turntable.
   - **Cassette** vs vinyl — the **Cassette** button switches skins.
   - The fx chips (Smoke / Particles / Shake / Heat) toggle effects on/off.
3. **Cue the beat** but don't start it yet (or let it loop on the deck).
4. OBS → **Start Recording**.
5. In the player, hit **play** (or **🎛 Remix** then play the layers in/out).
6. **Stop Recording** when the beat ends.
7. If you recorded MOV/MKV: **File → Remux Recordings** → MP4. Post.

---

## Quality notes

- **60 fps** end-to-end — OBS at 60, and the player already animates at 60. A 30 fps
  capture is the #1 thing that makes these look cheap.
- **Don't page-zoom.** Keep Chrome at 100% zoom so the canvas renders at native
  device pixels (crisp spectrum/bokeh). The visualizer already renders the canvas at
  2× for sharpness.
- **Feed TikTok clean source.** TikTok re-encodes hard, so a high-bitrate
  (CQP ≤ 20) master survives the upload far better than the in-browser capture.
- **Cover art on the label:** launch with `releases web --cover art.jpg` (or
  `releases pack … --cover art.jpg`) so the vinyl/cassette label shows the artwork
  instead of your name.
- The same player ships inside an **exported pack** (`Build pack`), so anything you
  tune here also looks right in the shareable link.
