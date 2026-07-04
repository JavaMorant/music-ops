"""Small path helpers shared by the planner and engine."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

QUARANTINE_DIRNAME = "_quarantine"

# Audio file types the librarian organises. Lower-case, with the dot.
AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg"}


def default_runs_dir(library_root: Path | None = None) -> Path:
    """Where undo journals + pre-apply backups live.

    Two invariants drive this: backups must live OUTSIDE the library (so they are
    never scanned, counted, or de-duplicated as library tracks), and every run
    must land in ONE place (so ``librarian runs``/``undo`` can find them all).

    Resolution order:
      1. ``$LIBRARIAN_RUNS_DIR`` if set (explicit override),
      2. a sibling of the library root — ``<root>/../.librarian-runs`` — when a
         root is known (for ``~/DJ/library`` this is ``~/DJ/.librarian-runs``,
         which is where the tool's runs already accumulate),
      3. the personal default ``~/DJ/.librarian-runs`` for the rootless
         ``undo``/``runs`` commands.
    """
    env = os.environ.get("LIBRARIAN_RUNS_DIR")
    if env:
        return Path(env).expanduser().absolute()
    if library_root is not None:
        return (Path(library_root).absolute().parent / ".librarian-runs")
    return (Path.home() / "DJ" / ".librarian-runs")

# Characters that can't (or shouldn't) appear in a path component.
_ILLEGAL = re.compile(r"[/\\:\x00-\x1f]")


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def audio_files(root: Path, *, extra_skip_dirs: frozenset[str] = frozenset()) -> list[Path]:
    """Every audio file under ``root``, sorted. Skips the quarantine dir, any
    ``extra_skip_dirs`` by name, and any hidden (dot-prefixed) path component such
    as ``.librarian`` — so the run backups and journals the tool stores under
    ``.librarian`` are never scanned, counted, or de-duplicated as library tracks
    (and macOS ``._*`` AppleDouble sidecars are ignored for free)."""
    skip = {QUARANTINE_DIRNAME, *extra_skip_dirs}

    def visible(rel_parts: tuple[str, ...]) -> bool:
        if skip & set(rel_parts):
            return False
        return not any(part.startswith(".") for part in rel_parts)

    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and is_audio(p) and visible(p.relative_to(root).parts)
    )


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


def norm_key(path: Path) -> str:
    """Case- and Unicode-normalised absolute path key. Must stay identical to
    ``engine._norm`` so a destination reserved by a planner is recognised as a
    collision by the engine's preflight (macOS stores NFD; tags are often NFC)."""
    return unicodedata.normalize("NFC", os.path.abspath(path)).casefold()


# Internal alias for the collision helpers below.
_norm = norm_key


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
