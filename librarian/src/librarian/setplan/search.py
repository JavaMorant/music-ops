# src/librarian/setplan/search.py
from __future__ import annotations

from dataclasses import dataclass, field

from .. import camelot
from .pool import DEFAULT_ADJACENCY, norm
from .score import score_slot
from .spec import GigSpec, genre_block_at, target_percentile


@dataclass
class Slot:
    index: int
    candidate: object
    reason: str
    alternates: list = field(default_factory=list)
    clock_min: float = 0.0


@dataclass
class SetPlan:
    spec: GigSpec
    slots: list


def _in_block(genre: str, block: str | None, adjacency: dict) -> bool:
    if not block:
        return True
    g = (genre or "").lower()
    b = block.lower()
    return g == b or g in adjacency.get(b, set())


def _percentile_to_bpm(pool, block, pct, adjacency) -> float | None:
    bpms = sorted(c.bpm for c in pool if c.bpm and _in_block(c.genre, block, adjacency))
    if not bpms:
        bpms = sorted(c.bpm for c in pool if c.bpm)
    if not bpms:
        return None
    return bpms[min(len(bpms) - 1, int(pct * (len(bpms) - 1)))]


def _match(query: str, pool) -> object | None:
    q = norm(query)
    return next((c for c in pool if q and q in c.norm_key), None)


def build_set(pool, spec: GigSpec, follows: dict | None = None, adjacency: dict | None = None) -> SetPlan:
    adjacency = adjacency or DEFAULT_ADJACENCY
    follows = follows or {}
    avoid_keys = {norm(a) for a in spec.avoid}
    avoid_names = {a.lower() for a in spec.avoid}
    pool = [c for c in pool
            if c.norm_key not in avoid_keys and c.artist.lower() not in avoid_names
            and (c.genre or "").lower() not in avoid_names
            and (spec.allow_low_bitrate or not c.low_bitrate)]
    if spec.bpm_range:
        lo, hi = spec.bpm_range
        pool = [c for c in pool if c.bpm is None or lo <= c.bpm <= hi]

    n = spec.n_slots()
    # Anchors: opener at slot 0, must-plays spread across the peak region.
    anchors: dict[int, object] = {}
    if spec.opener:
        m = _match(spec.opener, pool)
        if m:
            anchors[0] = m
    musts = [m for q in spec.must_play if (m := _match(q, pool))]
    for j, m in enumerate(musts):
        preferred = min(n - 1, int(n * 0.5) + j)     # aim for the peak plateau
        # Nearest free slot: forward from the preferred slot, then backward.
        # Never overwrite a slot already held by another anchor (that would
        # silently drop a requested must-play).
        free = ([s for s in range(preferred, n) if s not in anchors]
                or [s for s in range(preferred - 1, -1, -1) if s not in anchors])
        if not free:
            break                                    # every slot is already an anchor
        anchors[free[0]] = m

    max_plays = max((c.plays for c in pool), default=1)
    used: set = set()
    recent_artists: list[str] = []
    slots: list[Slot] = []
    prev = None
    clock = 0.0
    for i in range(n):
        t = i / max(1, n - 1)
        block = genre_block_at(spec, t)
        target = _percentile_to_bpm(pool, block, target_percentile(spec.arc, t), adjacency)
        prev_t = (i - 1) / max(1, n - 1)
        rising = i == 0 or target_percentile(spec.arc, t) >= target_percentile(spec.arc, prev_t)
        ctx = dict(rising=rising, target_bpm=target, block=block, adjacency=adjacency,
                   max_plays=max_plays, freshness=spec.freshness, follows=follows,
                   recent_artists=[a.lower() for a in recent_artists[-6:]], harmonic_mode=spec.harmonic)

        if i in anchors and anchors[i].path not in used:
            best = anchors[i]
            _, why = score_slot(best, prev, ctx)
            reason = f"⭐ anchor ({'opener' if i == 0 else 'must-play'}) — {why}"
            alts = []
        else:
            avail = [c for c in pool if c.path not in used and _in_block(c.genre, block, adjacency)
                     and c not in anchors.values()]
            # Avoid repeating an artist within the last 6 slots — a hard filter,
            # but fall back to the full set rather than leave a slot unfilled.
            recent6 = {a.lower() for a in recent_artists[-6:]}
            non_repeat = [c for c in avail if c.artist.lower() not in recent6]
            avail = non_repeat or avail
            if spec.harmonic == "strict" and prev:
                avail = [c for c in avail
                         if c.camelot is None
                         or camelot.harmonic(prev.camelot, c.camelot, rising=rising,
                                             mode=spec.harmonic) >= 0.85]
            scored = sorted((score_slot(c, prev, ctx) + (c,) for c in avail),
                            key=lambda r: r[0], reverse=True)
            if not scored:
                break
            best_score, reason, best = scored[0]
            alts = [{"candidate": c, "reason": r} for _, r, c in scored[1:4]]

        slots.append(Slot(index=i, candidate=best, reason=reason, alternates=alts, clock_min=clock))
        used.add(best.path)
        recent_artists.append(best.artist)
        prev = best
        clock += (best.length_s or 180.0) * 0.55 / 60.0
    return SetPlan(spec=spec, slots=slots)
