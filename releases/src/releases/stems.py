"""Auto stem separation (Demucs) for the interactive stem player.

Splits a beat's mixdown into stems — drums / bass / other (melody/instruments) /
vocals — by running Demucs locally. Stems are cached OUTSIDE the music library
(keyed by the source path + size + mtime, so a re-run is instant and a changed
file re-separates), keeping the read-only-library invariant intact.

Heavy: pulls in PyTorch and is slow on CPU (~a minute+ per track). Strictly
opt-in, behind ``has_demucs()`` + a clear message — never required to run the
rest of the tool.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

STEMS = ("drums", "bass", "other", "vocals")  # demucs htdemucs 4-stem output
MODEL = "htdemucs"


class StemError(RuntimeError):
    pass


def has_demucs() -> bool:
    return importlib.util.find_spec("demucs") is not None


def cache_key(src: Path) -> str:
    """Stable per-file key (path + size + mtime) so edits re-separate."""
    st = src.stat()
    raw = f"{src.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cached_stems(src: Path, cache_dir: Path) -> dict[str, Path] | None:
    """Return the cached {stem: path} for ``src`` if all stems are present, else None."""
    dest = cache_dir / cache_key(src)
    out = {s: dest / f"{s}.mp3" for s in STEMS}
    return out if all(p.is_file() for p in out.values()) else None


def separate(src: Path, cache_dir: Path, *, model: str = MODEL, bitrate: int = 256) -> dict[str, Path]:
    """Separate ``src`` into stems under ``cache_dir`` and return {stem: mp3 path}.
    Cached: a second call for an unchanged file returns instantly. Read-only on
    ``src`` (Demucs only reads it). Raises :class:`StemError` if Demucs is missing
    or fails."""
    if not has_demucs():
        raise StemError("Demucs not installed — run `pip install demucs` to separate stems")
    hit = cached_stems(src, cache_dir)
    if hit is not None:
        return hit
    dest = cache_dir / cache_key(src)
    out = {s: dest / f"{s}.mp3" for s in STEMS}
    work = dest / "_work"
    shutil.rmtree(work, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable, "-m", "demucs", "-n", model,
        "--mp3", "--mp3-bitrate", str(bitrate),
        "-o", str(work), str(src),
    ]
    try:
        subprocess.run(args, capture_output=True, text=True, check=True)
    except FileNotFoundError as e:
        raise StemError("Demucs not installed — run `pip install demucs`") from e
    except subprocess.CalledProcessError as e:
        shutil.rmtree(work, ignore_errors=True)
        raise StemError(f"Demucs failed on {src.name}:\n{e.stderr.strip()[-1500:]}") from e
    # Demucs writes work/<model>/<trackname>/{stem}.mp3 — move them to dest/{stem}.mp3
    model_dir = work / model
    track_dir = next((d for d in model_dir.iterdir() if d.is_dir()), None) if model_dir.is_dir() else None
    if track_dir is None:
        shutil.rmtree(work, ignore_errors=True)
        raise StemError("Demucs produced no output")
    for s in STEMS:
        produced = track_dir / f"{s}.mp3"
        if produced.is_file():
            shutil.move(str(produced), str(out[s]))
    shutil.rmtree(work, ignore_errors=True)
    missing = [s for s in STEMS if not out[s].is_file()]
    if missing:
        raise StemError(f"Demucs did not produce stems: {missing}")
    return out
