"""Protected previews for sharable packs.

Trim a beat to a short hook and mix a quiet periodic tag tone over it (ffmpeg),
so the full beat can't be ripped from the player/zip. Read-only on the source.

Fails LOUDLY: if ffmpeg is missing or errors, we raise — we never silently ship
the full beat as a "preview" (that would defeat the point and leak the beat).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class PreviewError(RuntimeError):
    pass


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def make_preview(
    src: Path,
    dest: Path,
    *,
    seconds: int = 40,
    tone_hz: int = 1100,
    tone_gain: float = 0.12,
    every: int = 15,
    blip: float = 0.18,
) -> None:
    """Write a preview of ``src`` to ``dest`` (mp3): trimmed to ``seconds`` with a
    ``blip``-second tag tone every ``every`` seconds at ``tone_gain`` amplitude.

    Read-only on ``src``. Raises :class:`PreviewError` if ffmpeg is missing or
    fails (so a caller can refuse to ship an unprotected pack)."""
    if not has_ffmpeg():
        raise PreviewError("ffmpeg not found — can't build protected previews")
    # A quiet sine blip that fires for the first ``blip`` seconds of every ``every``
    # second window. Commas inside the filtergraph expression are escaped so the
    # graph parser doesn't read them as argument separators.
    expr = f"{tone_gain}*sin(2*PI*{tone_hz}*t)*lt(mod(t\\,{every})\\,{blip})"
    fc = (
        f"aevalsrc=exprs='{expr}':d={seconds}:s=44100[tone];"
        f"[0:a][tone]amix=inputs=2:duration=shortest:normalize=0[out]"
    )
    args = [
        "ffmpeg", "-v", "error",
        "-t", str(seconds), "-i", str(src),
        "-filter_complex", fc, "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100",
        "-y", str(dest),
    ]
    try:
        subprocess.run(args, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:  # ffmpeg vanished between the check and the run
        raise PreviewError("ffmpeg not found — can't build protected previews") from exc
    except subprocess.CalledProcessError as exc:
        raise PreviewError(
            f"ffmpeg failed building a preview of {src.name}:\n{exc.stderr.strip()[-1500:]}"
        ) from exc
