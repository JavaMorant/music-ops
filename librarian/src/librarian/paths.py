"""Small path helpers shared by the planner and engine."""

from __future__ import annotations

import os
import re
from pathlib import Path

QUARANTINE_DIRNAME = "_quarantine"

# Audio file types the librarian organises. Lower-case, with the dot.
AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg"}

# Characters that can't (or shouldn't) appear in a path component.
_ILLEGAL = re.compile(r"[/\\:\x00-\x1f]")


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


# Keep a margin under the common 255-byte per-component filesystem limit so a
# garbage/very-long tag can't make a move fail mid-apply.
_MAX_COMPONENT = 200


def sanitize_component(name: str) -> str:
    """Make ``name`` safe as a single path component (filename or folder)."""
    cleaned = _ILLEGAL.sub("-", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(". ")
    if len(cleaned) > _MAX_COMPONENT:
        cleaned = cleaned[:_MAX_COMPONENT].strip()
    return cleaned or "Unknown"


def _norm(path: Path) -> str:
    return os.path.abspath(path).casefold()


def _samefile(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def collision_free(dest: Path, reserved: set[str], ignore: Path | None = None) -> Path:
    """A destination free of any existing file *and* any already-chosen dest.

    ``reserved`` holds case-folded absolute paths already claimed in this plan.
    ``ignore`` is the file being moved — it may legitimately "occupy" the
    destination (a case-only rename of itself), so it doesn't count as a clash.
    Clashes get `` (2)``, `` (3)`` … appended before the extension.
    """

    def taken(p: Path) -> bool:
        if _norm(p) in reserved:
            return True
        if p.exists():
            return not (ignore is not None and _samefile(p, ignore))
        return False

    if not taken(dest):
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while True:
        candidate = dest.with_name(f"{stem} ({n}){suffix}")
        if not taken(candidate):
            return candidate
        n += 1


def quarantine_dest(library_root: Path, name: str, reserved: set[str]) -> Path:
    """A collision-free path for ``name`` inside the library's quarantine dir."""
    return collision_free(library_root / QUARANTINE_DIRNAME / name, reserved)
