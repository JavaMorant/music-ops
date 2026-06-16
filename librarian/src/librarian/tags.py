"""Writing (and reading-back) the identity tags the librarian is allowed to
repair — artist, title, genre. This is the ONLY module that mutates tags;
``metadata.py`` stays read-only.

The engine reads the current values here, journals them, then writes the new
ones, so ``undo`` can restore exactly what was there (``None`` means the tag was
absent, and restoring it deletes the tag again). Musical key/BPM are not in the
writable set and can never be touched by a repair.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError

from .model import WRITABLE_TAGS


class TagError(RuntimeError):
    """A tag could not be read or written for a file."""


def _open(path: Path, *, create: bool = False):
    """Open ``path`` for easy (artist/title/genre) tag access.

    Falls back to EasyID3 for MP3s mutagen can't sniff as audio (e.g. a tag-only
    file), creating an ID3 header when ``create`` is set so a first write can
    land. Returns None when the file simply can't carry these tags.
    """
    easy = MutagenFile(path, easy=True)
    if easy is not None:
        return easy
    if path.suffix.lower() == ".mp3":
        try:
            return EasyID3(path)
        except ID3NoHeaderError:
            if not create:
                return EasyID3()  # empty, readable as "all absent"
            tag = EasyID3()
            tag.save(path)
            return EasyID3(path)
    return None


def is_taggable(path: Path) -> bool:
    """True if mutagen can open ``path`` to write identity tags."""
    try:
        return _open(path) is not None
    except Exception:
        return False


def read_tags(path: Path, fields: Iterable[str]) -> dict[str, str | None]:
    """Current value of each named field (None if absent or unreadable)."""
    try:
        audio = _open(path)
    except Exception as exc:
        raise TagError(f"cannot read tags from {path}: {exc}") from exc
    out: dict[str, str | None] = {}
    for field in fields:
        val = None
        if audio is not None:
            try:
                got = audio.get(field)
                if got:
                    val = str(got[0]) if isinstance(got, list) else str(got)
            except Exception:
                val = None
        out[field] = val
    return out


def write_tags(path: Path, fields: dict[str, str | None]) -> None:
    """Set each field to its value; a value of ``None`` deletes the tag (used by
    undo to restore a previously-absent tag). Only WRITABLE_TAGS are accepted."""
    bad = [k for k in fields if k not in WRITABLE_TAGS]
    if bad:
        raise TagError(f"refusing to write non-writable tag(s): {bad}")
    try:
        audio = _open(path, create=True)
    except Exception as exc:
        raise TagError(f"cannot open {path} for tag write: {exc}") from exc
    if audio is None:
        raise TagError(f"file cannot carry tags: {path}")
    for field, value in fields.items():
        if value is None:
            audio.pop(field, None)
        else:
            audio[field] = value
    try:
        audio.save()
    except Exception as exc:
        raise TagError(f"cannot save tags to {path}: {exc}") from exc
