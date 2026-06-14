"""The canonical filename a track should have — one source of truth shared by
cleanup and inbox, so a track is named identically however it arrives."""

from __future__ import annotations

from pathlib import Path

from .metadata import TrackMeta, quality_rank
from .paths import sanitize_component
from .planner import _DUP_MARKER, normalize_stem


def target_stem(meta: TrackMeta) -> str:
    """`Artist - Title` from tags when both are present, else a cleaned version
    of the existing filename (never guessed)."""
    if meta.has_artist_title:
        return sanitize_component(f"{meta.artist} - {meta.title}")
    return sanitize_component(normalize_stem(meta.path.stem))


def pick_keeper(group: list[Path], metas: dict[Path, TrackMeta]) -> Path:
    """Choose which copy of an exact-duplicate group to keep: highest quality,
    and on a tie prefer a *clean* name over a copy-marker one (``Track`` over
    ``Track (1)``). Without the name tiebreak the marker copy could win and then
    be quarantined by the copy-marker rule, dropping the whole group."""

    def key(p: Path):
        # quality_rank is "bigger is better"; append 1 for clean / 0 for marker.
        return (*quality_rank(metas[p]), 0 if _DUP_MARKER.search(p.stem) else 1)

    return max(group, key=key)
