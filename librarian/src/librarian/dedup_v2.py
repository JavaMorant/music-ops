"""Stage 2 — recording-level dedup that preserves distinct versions.

Per the design spec: collapse exact copies and alternate-source rips of the SAME
recording (audio / music-video / lyrics), keeping the best-quality audio copy;
NEVER collapse a distinct version (intro / extended / radio / remix / instrumental
/ acapella / live / VIP / flip).

Grouping key:
  * AcoustID gave an MBID  → key on (mbid, version) — same recording collapses,
    but a version token still splits an edit off (an intro edit can fingerprint to
    the original recording, so the version token is the safety net).
  * no MBID (degraded)     → key on (artist, base-title, version).

Keep selection within a group: rating (keep the copy you use/cued) → lossless →
bitrate → size → not-in-a-raw-download-folder. Drops are NEVER deleted — the
engine routes them to quarantine through the reversible plan/apply/undo path.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import versions as V

LOSSLESS_EXTS = {".wav", ".aiff", ".aif", ".flac", ".alac"}
_JUNK = ("todownload", "download", "collab mock", "found.0", "discjockey",
         "pamiredo", "pamito", "minisound", "fightnight", "combini", "_from-",
         "sample-library")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _is_junk(path: Path) -> bool:
    p = str(path).lower()
    return any(j in p for j in _JUNK)


@dataclass(frozen=True)
class FileInfo:
    path: Path
    artist: str
    title: str
    mbid: str | None = None
    bitrate: int = 0
    size: int = 0
    rating: int = 0

    @property
    def lossless(self) -> bool:
        return self.path.suffix.lower() in LOSSLESS_EXTS


@dataclass
class DupGroup:
    key: tuple
    keep: FileInfo
    drops: list[FileInfo] = field(default_factory=list)
    reason: str = ""


def dedup_key(fi: FileInfo) -> tuple:
    ver = V.version_label(fi.title) or ""
    if fi.mbid:
        return ("mbid", fi.mbid, ver)
    return ("vt", _norm(fi.artist), V.base_title(fi.title), ver)


def quality_score(fi: FileInfo) -> tuple:
    """Higher is better. Rating first so a rated/cued copy wins; then audio
    quality; then prefer copies outside raw-download folders."""
    return (fi.rating, fi.lossless, fi.bitrate, fi.size, 0 if _is_junk(fi.path) else 1)


def plan_groups(infos: list[FileInfo]) -> list[DupGroup]:
    """Group files into dedup groups; each group keeps the best and lists drops.
    Groups of one (unique) are returned with no drops."""
    buckets: dict[tuple, list[FileInfo]] = collections.defaultdict(list)
    for fi in infos:
        buckets[dedup_key(fi)].append(fi)
    groups: list[DupGroup] = []
    for key, members in buckets.items():
        ranked = sorted(members, key=quality_score, reverse=True)
        keep, drops = ranked[0], ranked[1:]
        reason = ""
        if drops:
            ver = key[-1]
            reason = (f"recording-level dup of '{keep.title}'"
                      + (f" (version: {ver})" if ver else ""))
        groups.append(DupGroup(key, keep, drops, reason))
    return groups


def summarise(groups: list[DupGroup]) -> dict:
    keepers = [g.keep for g in groups]
    drops = [d for g in groups for d in g.drops]
    versions_kept = sum(1 for g in groups if g.key and g.key[-1])
    reclaim = sum(d.size for d in drops)
    return {
        "total": len(keepers) + len(drops),
        "unique_keepers": len(keepers),
        "drops": len(drops),
        "distinct_versions_kept": versions_kept,
        "reclaim_bytes": reclaim,
    }
