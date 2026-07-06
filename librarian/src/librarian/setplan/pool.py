# src/librarian/setplan/pool.py
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..metadata import read_meta
from ..paths import audio_files
from .. import camelot

# Genre clusters for the reference user; adjacent genres are allowed with a lower fit.
DEFAULT_ADJACENCY: dict[str, set[str]] = {
    "amapiano": {"afrobeats", "afro house", "afrohouse"},
    "afrobeats": {"amapiano", "afro house", "afrohouse", "afroswing"},
    "uk garage": {"house", "general house"},
    "house": {"uk garage", "general house", "amapiano"},
    "general house": {"uk garage", "house"},
    "uk rap": {"us rap", "rnb"},
    "us rap": {"uk rap", "rnb"},
}


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


@dataclass
class Candidate:
    path: Path
    artist: str
    title: str
    genre: str
    bpm: float | None
    camelot: tuple[int, str] | None
    length_s: float | None
    norm_key: str
    low_bitrate: bool
    plays: int = 0
    positions: list[float] = field(default_factory=list)

    @property
    def position_prior(self) -> float | None:
        return sum(self.positions) / len(self.positions) if self.positions else None


def _to_bpm(s: str | None) -> float | None:
    try:
        return float(s) if s else None
    except ValueError:
        return None


def history_stats(sessions: list[list[str]]) -> tuple[dict, dict, dict]:
    """From sessions (each an ordered tracklist of raw labels): per-track play count,
    normalised positions (0=opener, 1=closer), and the transition graph prev->next."""
    plays: dict[str, int] = collections.Counter()
    positions: dict[str, list[float]] = collections.defaultdict(list)
    follows: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for tracks in sessions:
        ks = [norm(t) for t in tracks]
        n = len(ks)
        for i, k in enumerate(ks):
            if not k:
                continue
            plays[k] += 1
            positions[k].append(i / max(1, n - 1))
            if i + 1 < n and ks[i + 1] and ks[i + 1] != k:
                follows[k][ks[i + 1]] += 1
    return plays, positions, follows


def build_pool(root: Path, sessions: list[list[str]] | None = None) -> tuple[list[Candidate], dict]:
    plays, positions, follows = history_stats(sessions or [])
    cands: list[Candidate] = []
    for p in audio_files(root):
        m = read_meta(p)
        title = m.title or p.stem
        nk = norm(f"{m.artist or ''} {title}")
        cands.append(Candidate(
            path=p, artist=m.artist or "", title=title, genre=(m.genre or "").strip(),
            bpm=_to_bpm(m.bpm), camelot=camelot.parse_key(m.key), length_s=m.length_s,
            norm_key=nk, low_bitrate=m.low_bitrate,
            plays=plays.get(nk, 0), positions=positions.get(nk, []),
        ))
    return cands, follows
