"""Reading track metadata (mutagen) and content hashing (for exact dedupe).

This module only *reads*. It never writes tags — tag normalisation that mutates
file bytes is a separate, carefully-reversible slice. Everything the cleanup
planner needs to decide moves comes from here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile

# Formats we treat as lossless for quality ranking / low-bitrate flagging.
LOSSLESS_EXTS = {".wav", ".aiff", ".aif", ".flac", ".alac"}
# DJ-quality floor for lossy files (brief: < 320kbps mp3 is flagged).
MIN_LOSSY_KBPS = 320


@dataclass
class TrackMeta:
    """What we know about one audio file. Any field may be None/unknown — the
    planner degrades gracefully rather than guessing."""

    path: Path
    artist: str | None = None
    title: str | None = None
    genre: str | None = None
    bpm: str | None = None
    key: str | None = None
    bitrate_kbps: int | None = None
    lossless: bool = False
    length_s: float | None = None

    @property
    def low_bitrate(self) -> bool:
        """True only when we actually read a lossy bitrate below the floor.
        Unknown bitrate is *not* flagged — we never guess quality."""
        if self.lossless:
            return False
        return self.bitrate_kbps is not None and 0 < self.bitrate_kbps < MIN_LOSSY_KBPS

    @property
    def has_artist_title(self) -> bool:
        return bool(self.artist) and bool(self.title)


def _first(value) -> str | None:
    """mutagen easy tags are lists; take the first non-empty entry."""
    if not value:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    text = str(value).strip() if value is not None else None
    return text or None


def _read_key(raw) -> str | None:
    """Musical key lives in TKEY (ID3) or an 'initialkey'/'key' tag elsewhere."""
    if raw is None or raw.tags is None:
        return None
    for frame in ("TKEY", "key", "initialkey", "INITIALKEY"):
        if frame in raw.tags:
            val = raw.tags[frame]
            text = _first(getattr(val, "text", val))
            if text:
                return text
    return None


def read_meta(path: Path) -> TrackMeta:
    """Best-effort metadata for ``path``. Unreadable files yield an all-unknown
    TrackMeta rather than raising — a weird file should never crash a scan."""
    meta = TrackMeta(path=path)
    meta.lossless = path.suffix.lower() in LOSSLESS_EXTS
    try:
        easy = MutagenFile(path, easy=True)
        raw = MutagenFile(path)
    except Exception:
        return meta
    if easy is not None:
        meta.artist = _first(easy.get("artist"))
        meta.title = _first(easy.get("title"))
        meta.genre = _first(easy.get("genre"))
        meta.bpm = _first(easy.get("bpm"))
    if raw is not None:
        meta.key = _read_key(raw)
        info = getattr(raw, "info", None)
        if info is not None:
            br = getattr(info, "bitrate", None)
            meta.bitrate_kbps = int(br) // 1000 if br else None
            meta.length_s = getattr(info, "length", None)
            # FLAC/ALAC report bits-per-sample; treat their presence as lossless.
            if getattr(info, "bits_per_sample", 0):
                meta.lossless = True
    return meta


def content_hash(path: Path, _chunk: int = 1 << 20) -> str:
    """sha256 of the whole file — the basis for exact (byte-identical) dedupe."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(_chunk), b""):
            h.update(block)
    return h.hexdigest()


def quality_rank(meta: TrackMeta) -> tuple:
    """Sort key for 'highest quality wins' — bigger is better. Lossless first,
    then bitrate, then file size as a last resort."""
    try:
        size = meta.path.stat().st_size
    except OSError:
        size = 0
    return (1 if meta.lossless else 0, meta.bitrate_kbps or 0, size)
