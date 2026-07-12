# src/librarian/setplan/score.py
from __future__ import annotations

import math

from .. import camelot


def _bpm_transition(prev_bpm: float | None, bpm: float | None) -> float:
    if prev_bpm is None or bpm is None:
        return 0.5
    d = min(abs(prev_bpm * m - bpm) / bpm for m in (0.5, 1.0, 2.0))   # half/double-time aware
    if d <= 0.02:
        return 1.0
    if d >= 0.08:
        return 0.0
    return 1.0 - (d - 0.02) / 0.06


def _arc_fit(bpm: float | None, target: float | None) -> float:
    if bpm is None or target is None:
        return 0.5
    return math.exp(-((bpm - target) ** 2) / (2 * 4.0 ** 2))


def _genre_fit(genre: str, block: str | None, adjacency: dict) -> float:
    if not block:
        return 0.5
    g = (genre or "").lower()
    b = block.lower()
    if g == b:
        return 1.0
    if g in adjacency.get(b, set()):
        return 0.6
    return 0.0


def _proven_mix(plays: int, max_plays: int, freshness: float) -> float:
    proven = math.log(1 + plays) / math.log(1 + max(1, max_plays))
    novelty = 1.0 if plays == 0 else 0.3
    return (1 - freshness) * proven + freshness * novelty


def _follows_boost(follows: dict, prev_key: str, cand_key: str) -> float:
    count = follows.get(prev_key, {}).get(cand_key, 0) if follows else 0
    return min(1.0, math.log2(1 + count) / 2) if count else 0.0


def score_slot(cand, prev, ctx) -> tuple[float, str]:
    prev_key = prev.norm_key if prev else ""
    h = camelot.harmonic(prev.camelot if prev else None, cand.camelot,
                         rising=ctx["rising"], mode=ctx["harmonic_mode"]) if prev else 0.5
    t = _bpm_transition(prev.bpm if prev else None, cand.bpm) if prev else 0.5
    a = _arc_fit(cand.bpm, ctx["target_bpm"])
    g = _genre_fit(cand.genre, ctx["block"], ctx["adjacency"])
    pm = _proven_mix(cand.plays, ctx["max_plays"], ctx["freshness"])
    fb = _follows_boost(ctx["follows"], prev_key, cand.norm_key)
    base = 0.20 * h + 0.10 * t + 0.10 * a + 0.15 * g + 0.20 * pm + 0.25 * fb
    penalty = 0.30 if (cand.artist and cand.artist.lower() in ctx["recent_artists"]) else 0.0

    # Reason: name the single strongest real signal.
    parts = []
    if fb > 0:
        cnt = ctx["follows"].get(prev_key, {}).get(cand.norm_key, 0)
        parts.append(f"you've played this next in {cnt} set(s)")
    if prev and cand.camelot and prev.camelot:
        parts.append(f"{prev.camelot[0]}{prev.camelot[1]}→{cand.camelot[0]}{cand.camelot[1]}")
    elif cand.camelot is None:
        parts.append("no key tag — neutral match")
    if cand.plays == 0:
        parts.append("fresh (never played out)")
    elif pm > 0.5:
        parts.append(f"proven ({cand.plays} plays)")
    reason = "; ".join(parts[:2]) or "best fit for this slot"
    return base - penalty, reason


def anchor_affinity(cand, anchor, *, mode: str) -> float:
    """Terminal steering bonus: how well `cand` hands over into a fixed anchor.

    Added to the LAST expansion of a beam segment so the search routes toward
    the must-play instead of arriving with a train-wreck transition. Neutral
    (never zero) on unknown key/BPM, like every other component.
    """
    h = camelot.harmonic(cand.camelot, anchor.camelot, rising=True, mode=mode)
    t = _bpm_transition(cand.bpm, anchor.bpm)
    return 0.5 * h + 0.25 * t
