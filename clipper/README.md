# clipper

Turn long set recordings into ready-to-post 9:16 short clips, as a batch job.
Point it at a set recording; it finds the highest-energy moments, cuts vertical
clips, normalizes loudness, and writes a manifest plus a captions stub you fill
in before posting.

## Requirements

- **Python 3.12+**
- **ffmpeg and ffprobe on your PATH** — the hard dependency. clipper shells out
  to both for probing, audio extraction, and cutting.
  ```
  brew install ffmpeg
  ```

## Install

```
cd clipper
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Optional ambition-tier extras (Whisper transcription + Claude captions):

```
pip install -e '.[ai]'
```

Optional local clip-studio web UI:

```
pip install -e '.[web]'
```

Every command has `--help`.

## Commands

### `analyze` — preview the high-energy moments (read-only)

Ranks candidate moments and prints a timeline. Writes nothing.

```
clipper analyze "<set recording>"
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--len` | 30 | Candidate clip length in seconds (15–60) |
| `--spacing` | 60 | Minimum seconds between candidates |
| `--top` | 10 | How many candidates to show |
| `--drop-weight` | 0.5 | Selection blend: 0 = sustained loudness, 1 = sharp drops |
| `--lead-in` | 5 | Start each candidate this many seconds before the detected drop |
| `--build-window` | 8 | Seconds compared before/after a moment to measure the energy step up |
| `--crowd-weight` | 0 (off) | Reward crowd-roar moments; try `0.4` (extra audio analysis) |
| `--beat-align / --no-beat-align` | on | Snap starts to the nearest beat |
| `--visual / --no-visual` | off | Blend on-camera motion/flash energy into scoring (extra video pass) |

### `cut` — produce the clips

Cuts the top-N moments to 9:16, normalizes loudness, and writes the manifest +
captions.

```
clipper cut "<set recording>" --clips 5 --len 30
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--clips` | 5 | Number of clips to cut |
| `--len` | 30 | Clip length in seconds (15–60) |
| `--spacing` | 60 | Minimum seconds between clips |
| `--drop-weight` | 0.5 | Selection blend: 0 = sustained loudness, 1 = sharp drops |
| `--lead-in` | 5 | Start each clip this many seconds before the detected drop |
| `--build-window` | 8 | Seconds compared before/after a moment to measure the energy step up |
| `--crowd-weight` | 0 (off) | Reward crowd-roar moments; try `0.4` (extra audio analysis) |
| `--x-offset` | centre | Manual crop x-offset in px |
| `--out` | `out/<date>/` | Output directory |
| `--beat-align / --no-beat-align` | on | Snap starts to the nearest beat |
| `--visual / --no-visual` | off | Blend on-camera motion/flash energy into scoring |
| `--transcribe` | off | Whisper transcription for per-clip captions (needs `[ai]`) |
| `--ai` | off | Draft captions/hashtags with Claude (needs `[ai]` + `ANTHROPIC_API_KEY`) |

### `overlay` — burn event/date/artist text into a clip

Burns a text overlay via ffmpeg `drawtext`, using an editable TOML template
(`templates/overlay.toml`). Re-encodes video; copies audio.

```
clipper overlay out/2026-06-14/clip_01.mp4 --event "Lightbox" --date "2026" --artist "dibs"
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--event` / `--date` / `--artist` | "" | Text filled into the template slots |
| `--template` | built-in defaults | Path to a TOML overlay template |
| `--out` | `<clip>_overlay.mp4` | Output file |
| `--force` | off | Overwrite an existing output file |

### `artwork` — batch-resize one image into every platform spec

Centre-crops to aspect and resizes into square (3000²), story (1080×1920),
banner (1500×500), and profile (1000²). Flags upscaled outputs.

```
clipper artwork cover.jpg
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--out` | `out/artwork/<name>/` | Output directory |

### `web` — local clip-studio UI (opt-in)

Runs a small FastAPI app (needs `pip install -e '.[web]'`) to drive analyze/cut
from the browser. Localhost only.

```
clipper web            # serves http://127.0.0.1:8765
```

## Output layout

`cut` writes to `out/<date>/` (or `--out`):

```
out/2026-06-14/
  clip_01.mp4      # 1080×1920, 9:16, loudness-normalized to -14 LUFS
  clip_02.mp4
  manifest.csv     # clip → source timestamp, duration, score
  captions.md      # stub to fill before posting (enriched when --ai is used)
```

## How clip selection works

1. Extract a downsampled mono WAV and analyze **only the audio** — the video is
   never decoded until cutting. This keeps memory flat (~1 GB peak) regardless of
   file size, so multi-GB / multi-hour sets work fine.
2. Score every second by **RMS energy + onset strength**, percentile-normalized
   so a single clipping spike can't skew the curve.
3. Pick non-overlapping windows by a blend of **drop strength** (a sharp energy
   rise) and sustained level, **anchored** so the drop lands a few seconds into
   the clip — not at the cut, not buried. `--spacing` keeps clips apart.
4. `--beat-align` snaps each start to the nearest beat (local tempo, handles a
   DJ set's BPM drift); `--visual` blends in on-camera motion/flash energy so
   selection isn't deaf to the footage.

### Tuning the selection

The blend in step 3 is adjustable, so you can aim it at the kind of moment you
want to pull:

- **`--drop-weight`** (0–1, default 0.5) trades off *what makes a clip win*.
  Toward **1.0** it chases sharp build→drop transitions; toward **0.0** it
  rewards sustained loudness, which is better for anthem/sing-along sections that
  never really "drop". Start here if selection keeps grabbing the wrong kind of
  moment.
- **`--crowd-weight`** (0–1, default 0 = off) rewards moments the audience
  reacts to. It detects a crowd roar by its sound — broadband, noise-like energy
  up in the 2–8 kHz cheer/whistle band, unlike the tonal music underneath — and
  mixes that into the ranking. Try **`0.4`**. It needs a recording with audible
  crowd (real room/crowd mics), and adds an extra spectral pass over the audio
  (still a single decode, no second file read).
- **`--lead-in`** (default 5s) is how far *before* the detected drop each clip
  starts, so the drop lands just inside the clip. **`--build-window`** (default
  8s) is how many seconds before vs after a moment are compared to call it a
  drop — widen it for slow builds, narrow it for snappy ones.

With `--crowd-weight 0` (the default) selection is exactly the energy-only
behaviour above; the crowd signal is opt-in and never changes results when off.

## Ambition-tier AI (optional, off by default)

```
pip install -e '.[ai]'
export ANTHROPIC_API_KEY=...
clipper cut "<set recording>" --transcribe --ai
```

- `--transcribe` — faster-whisper transcribes the set; each clip's transcript is
  added to `captions.md`.
- `--ai` — Claude (`claude-sonnet-4-6`, JSON output) drafts a caption, hashtags,
  and a one-line rationale per clip.

Both degrade gracefully: a missing dependency, missing `ANTHROPIC_API_KEY`, or
any API/parse error just warns and falls back to the plain captions stub — the
core cut never fails because of them.

## Safety

Source footage is never modified or re-encoded in place. All output goes to
`out/`.

## Tests

```
python -m pytest
```
