from __future__ import annotations

from dataclasses import dataclass, field

GenreBlock = tuple[str, float]

# Energy arcs: anchor points (position t in [0,1], target BPM *percentile* of the pool).
_ARCS: dict[str, list[tuple[float, float]]] = {
    "warmup":  [(0.0, 0.25), (1.0, 0.55)],
    "build":   [(0.0, 0.35), (1.0, 0.90)],
    "peak":    [(0.0, 0.55), (0.15, 0.85), (0.85, 0.92), (1.0, 0.80)],
    "closing": [(0.0, 0.85), (1.0, 0.45)],
    "journey": [(0.0, 0.30), (0.3, 0.80), (0.5, 0.55), (0.8, 0.90), (1.0, 0.70)],
}

ARC_NAMES = tuple(_ARCS)  # public: valid --arc values (CLI validates against this)


@dataclass
class GigSpec:
    minutes: int
    tracks_per_hour: int = 20
    journey: list[GenreBlock] = field(default_factory=list)
    arc: str = "peak"
    bpm_range: tuple[int, int] | None = None
    end_bpm: int | None = None
    harmonic: str = "loose"          # strict | loose | off
    freshness: float = 0.3
    must_play: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    venue: str | None = None
    opener: str | None = None
    allow_low_bitrate: bool = False
    seed: int | None = None
    catalogue_only: bool = True      # off (future): also suggest tracks to acquire, never downloaded

    def n_slots(self) -> int:
        return max(1, round(self.minutes / 60 * self.tracks_per_hour))


def target_percentile(arc: str, t: float) -> float:
    anchors = _ARCS.get(arc, _ARCS["peak"])
    for (t0, p0), (t1, p1) in zip(anchors, anchors[1:]):
        if t0 <= t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return p0 + f * (p1 - p0)
    return anchors[-1][1]


def parse_journey(s: str) -> list[tuple[str, float]]:
    """'amapiano:60,afrobeats:40' -> [('amapiano',0.6),('afrobeats',0.4)]; 'house' -> [('house',1.0)]."""
    s = (s or "").strip()
    if not s:
        return []
    parts = []
    for chunk in s.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            name, pct = chunk.rsplit(":", 1)
            parts.append((name.strip(), float(pct) / 100.0))
        else:
            parts.append((chunk, 1.0))
    return parts


def genre_block_at(spec: GigSpec, t: float) -> str | None:
    if not spec.journey:
        return None
    acc = 0.0
    for genre, frac in spec.journey:
        acc += frac
        if t <= acc + 1e-9:
            return genre
    return spec.journey[-1][0]
