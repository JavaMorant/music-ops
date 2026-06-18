"""Minimal, reversible ID3 tag read/write for MP3s (via mutagen).

Used by the Track-List organizer to stamp a rekordbox-readable **genre** and a
**comment** carrying mix/master state. Deliberately surgical:

  * Only ``genre`` (TCON) and ``comment`` are writable.
  * The comment is ONE specific frame — ``COMM`` with desc='' lang='eng''. Other
    COMM frames (iTunNORM, ReplayGain, other-language comments) are never touched,
    so they can't be wiped or mis-restored.
  * The OLD value (full text list) is read before writing, so undo restores it
    exactly (``None`` means the frame was absent — undo deletes it).
  * Writes are atomic (temp copy → save → os.replace), so a crash leaves the
    file fully-old or fully-new, never half-written.

mutagen is imported lazily so the rest of the CLI runs without it installed.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

WRITABLE_TAGS = ("genre", "comment")
_TAGGABLE_SUFFIXES = {".mp3"}
_COMM_KEY = "COMM::eng"  # the single comment frame this tool manages (desc='', lang='eng')


def is_taggable(path: Path) -> bool:
    """Only MP3s carry ID3 here. WAV/AIFF bounces are skipped (no reliable tags)."""
    return path.suffix.lower() in _TAGGABLE_SUFFIXES


def _require_mutagen():
    try:
        import mutagen.id3 as id3  # noqa: F401
        return id3
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "writing tags needs mutagen — install with: pip install -e '.[tags]'"
        ) from e


def _aslist(v):
    return list(v) if isinstance(v, (list, tuple)) else [v]


def read_tags(path: Path, keys) -> dict[str, list | None]:
    """Read current values for ``keys`` (subset of WRITABLE_TAGS) as the FULL
    text list (so multi-value frames round-trip). A missing frame reads as None,
    so undo can faithfully delete a frame that wasn't there."""
    id3 = _require_mutagen()
    out: dict[str, list | None] = {k: None for k in keys}
    try:
        tag = id3.ID3(path)
    except id3.ID3NoHeaderError:
        return out
    if "genre" in out:
        fr = tag.get("TCON")
        out["genre"] = list(fr.text) if fr and fr.text else None
    if "comment" in out:
        fr = tag.get(_COMM_KEY)  # the exact frame we manage, never getall()[0]
        out["comment"] = list(fr.text) if fr and fr.text else None
    return out


def write_tags(path: Path, fields: dict) -> None:
    """Set/clear ``fields`` (genre/comment). A None value deletes that frame.
    Values may be a string or a list (a journaled old value). Only the genre
    frame and the one managed COMM frame are touched — all other frames survive.
    Atomic: writes a temp copy and os.replaces it over the original."""
    id3 = _require_mutagen()
    bad = [k for k in fields if k not in WRITABLE_TAGS]
    if bad:
        raise ValueError(f"not writable tag field(s): {bad}")

    tmp = path.with_name(path.name + ".releases-tag-tmp")
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(path, tmp)
    try:
        try:
            tag = id3.ID3(tmp)
        except id3.ID3NoHeaderError:
            tag = id3.ID3()

        if "genre" in fields:
            v = fields["genre"]
            tag.delall("TCON")
            if v is not None:
                tag.add(id3.TCON(encoding=3, text=_aslist(v)))
        if "comment" in fields:
            v = fields["comment"]
            if _COMM_KEY in tag:
                del tag[_COMM_KEY]  # remove ONLY our frame; siblings untouched
            if v is not None:
                tag.add(id3.COMM(encoding=3, lang="eng", desc="", text=_aslist(v)))

        tag.save(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
