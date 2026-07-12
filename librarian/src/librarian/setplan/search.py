# src/librarian/setplan/search.py
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .. import camelot
from .pool import DEFAULT_ADJACENCY, norm
from .score import anchor_affinity, score_slot
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
    unmatched: list = field(default_factory=list)   # opener/must queries that matched nothing


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


def _slot_ctx(i, n, pool, spec, follows, adjacency, max_plays, recent_artists):
    """Context for scoring slot i — everything state-independent except recent_artists."""
    t = i / max(1, n - 1)
    block = genre_block_at(spec, t)
    target = _percentile_to_bpm(pool, block, target_percentile(spec.arc, t), adjacency)
    prev_t = (i - 1) / max(1, n - 1)
    rising = i == 0 or target_percentile(spec.arc, t) >= target_percentile(spec.arc, prev_t)
    ctx = dict(rising=rising, target_bpm=target, block=block, adjacency=adjacency,
               max_plays=max_plays, freshness=spec.freshness, follows=follows,
               recent_artists=list(recent_artists), harmonic_mode=spec.harmonic)
    return ctx, block, rising


@dataclass
class _State:
    seq: tuple            # candidates picked in this segment so far
    used: frozenset       # paths unavailable to this state
    artists: tuple        # trailing (≤6) lowercased artist window
    score: float


def _candidates(state, prev, ctx, block, rising, pool, spec, adjacency, anchor_cands):
    avail = [c for c in pool if c.path not in state.used
             and _in_block(c.genre, block, adjacency) and c not in anchor_cands]
    recent6 = set(state.artists)
    # Hard no-repeat window; empty artist never poisons the window (untagged
    # files must not exclude each other). Fall back rather than leave a hole.
    non_repeat = [c for c in avail if not c.artist or c.artist.lower() not in recent6]
    avail = non_repeat or avail
    if spec.harmonic == "strict" and prev is not None:
        avail = [c for c in avail
                 if c.camelot is None
                 or camelot.harmonic(prev.camelot, c.camelot, rising=rising,
                                     mode=spec.harmonic) >= 0.85]
    return avail


def build_set(pool, spec: GigSpec, follows: dict | None = None,
              adjacency: dict | None = None, *,
              beam_width: int = 1, pinned: dict[int, str] | None = None) -> SetPlan:
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
    by_path = {str(c.path): c for c in pool}
    unmatched: list[str] = []

    # --- fixed slots: pins first (explicit user locks win), then opener, then musts
    anchors: dict[int, object] = {}
    kinds: dict[int, str] = {}
    for idx, path in (pinned or {}).items():
        c = by_path.get(str(path))
        if c is not None and 0 <= int(idx) < n:
            anchors[int(idx)] = c
            kinds[int(idx)] = "locked"
        else:
            unmatched.append(str(path))
    if spec.opener:
        m = _match(spec.opener, pool)
        if m is None:
            unmatched.append(spec.opener)
        elif 0 not in anchors and not any(a is m for a in anchors.values()):
            anchors[0] = m
            kinds[0] = "opener"
        elif not any(a is m for a in anchors.values()):
            # slot 0 already pinned and the opener isn't placed anywhere —
            # surface the unhonored request instead of silently dropping it
            unmatched.append(spec.opener)
    for q in spec.must_play:
        m = _match(q, pool)
        if m is None:
            unmatched.append(q)
            continue
        if any(a is m for a in anchors.values()):
            continue                                  # already placed (e.g. pinned)
        preferred = min(n - 1, int(n * 0.5) + sum(1 for k in kinds.values() if k == "must-play"))
        free = ([s for s in range(preferred, n) if s not in anchors]
                or [s for s in range(preferred - 1, -1, -1) if s not in anchors])
        if not free:
            break
        anchors[free[0]] = m
        kinds[free[0]] = "must-play"

    max_plays = max((c.plays for c in pool), default=1)
    anchor_cands = list(anchors.values())
    rng = random.Random(spec.seed) if spec.seed is not None else None
    M = 20

    # --- walk the slots; beam-search each run of free slots between anchors
    chosen: list[tuple[int, object, str | None]] = []   # (index, candidate, kind)
    used_global: set = {c.path for c in anchor_cands}
    artists_global: list[str] = []
    i = 0
    truncated = False
    while i < n and not truncated:
        if i in anchors:
            chosen.append((i, anchors[i], kinds[i]))
            if anchors[i].artist:
                artists_global.append(anchors[i].artist.lower())
            i += 1
            continue
        seg = []
        j = i
        while j < n and j not in anchors:
            seg.append(j)
            j += 1
        end_anchor = anchors.get(j)                    # steer into this (if any)
        prev_fixed = chosen[-1][1] if chosen else None
        states = [_State((), frozenset(used_global), tuple(artists_global[-6:]), 0.0)]
        for pos, idx in enumerate(seg):
            is_last = pos == len(seg) - 1
            nxt: list[_State] = []
            for st in states:
                prev = st.seq[-1] if st.seq else prev_fixed
                ctx, block, rising = _slot_ctx(idx, n, pool, spec, follows, adjacency,
                                               max_plays, st.artists)
                scored = []
                for c in _candidates(st, prev, ctx, block, rising, pool, spec,
                                     adjacency, anchor_cands):
                    s, _ = score_slot(c, prev, ctx)
                    if is_last and end_anchor is not None:
                        s += anchor_affinity(c, end_anchor, mode=spec.harmonic)
                    if rng is not None:
                        s += rng.random() * 1e-6       # reproducible reroll variety
                    scored.append((s, c))
                scored.sort(key=lambda r: r[0], reverse=True)
                for s, c in scored[:M]:
                    arts = (st.artists + (c.artist.lower(),))[-6:] if c.artist else st.artists
                    nxt.append(_State(st.seq + (c,), st.used | {c.path}, arts, st.score + s))
            if not nxt:
                truncated = True                       # pool exhausted — end the set here
                break
            nxt.sort(key=lambda st: st.score, reverse=True)
            states = nxt[:max(1, beam_width)]
        if states and states[0].seq:
            for off, c in enumerate(states[0].seq):
                chosen.append((seg[0] + off, c, None))
                used_global.add(c.path)
                if c.artist:
                    artists_global.append(c.artist.lower())
        i = j if not truncated else n

    # --- annotation pass: reasons + alternates in each slot's REAL context
    slots: list[Slot] = []
    all_used = {c.path for _, c, _ in chosen}
    prev = None
    recent: list[str] = []
    clock = 0.0
    # Truncation (pool exhaustion) can leave later anchors unplaced — surface any
    # matched opener/must-play that never made it into the set, never drop it silently.
    placed_paths = {c.path for _, c, _ in chosen}
    for a_idx, a_cand in anchors.items():
        if a_cand.path not in placed_paths and a_cand not in unmatched:
            label = kinds.get(a_idx, "must-play")
            q = spec.opener if label == "opener" else None
            # prefer the original query text when we have it; fall back to "artist - title"
            unmatched.append(q or f"{a_cand.artist} - {a_cand.title}".strip(" -"))
    for out_idx, (idx, cand, kind) in enumerate(sorted(chosen)):
        ctx, block, rising = _slot_ctx(idx, n, pool, spec, follows, adjacency,
                                       max_plays, recent[-6:])
        _, why = score_slot(cand, prev, ctx)
        if kind:
            reason, alts = f"⭐ anchor ({kind}) — {why}", []
        else:
            reason = why
            others = [c for c in pool if c.path not in all_used
                      and _in_block(c.genre, block, adjacency)]
            ranked = sorted((score_slot(c, prev, ctx) + (c,) for c in others),
                            key=lambda r: r[0], reverse=True)
            alts = [{"candidate": c, "reason": r} for _, r, c in ranked[:3]]
        slots.append(Slot(index=out_idx, candidate=cand, reason=reason,
                          alternates=alts, clock_min=clock))
        if cand.artist:
            recent.append(cand.artist.lower())
        prev = cand
        clock += (cand.length_s or 180.0) * 0.55 / 60.0
    return SetPlan(spec=spec, slots=slots, unmatched=unmatched)
