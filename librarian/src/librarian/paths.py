"""Small path helpers shared by the planner and engine."""

from __future__ import annotations

from pathlib import Path

QUARANTINE_DIRNAME = "_quarantine"

# Audio file types the librarian organises. Lower-case, with the dot.
AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg"}


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def collision_free(dest: Path, reserved: set[Path]) -> Path:
    """A destination that clashes with neither an existing file nor an already
    chosen destination, by appending `` (2)``, `` (3)`` … before the extension."""
    if not dest.exists() and dest not in reserved:
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while True:
        candidate = dest.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists() and candidate not in reserved:
            return candidate
        n += 1


def quarantine_dest(library_root: Path, name: str, reserved: set[Path]) -> Path:
    """A collision-free path for ``name`` inside the library's quarantine dir."""
    return collision_free(library_root / QUARANTINE_DIRNAME / name, reserved)
