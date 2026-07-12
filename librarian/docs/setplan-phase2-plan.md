# Setplan Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the merged Phase 1 set-prep engine into the real product: beam search that routes *into* must-play anchors, persisted plan artifacts + a pool cache, rekordbox-XML setlist export, the missing CLI flags, and a Setplan web tab (lock / reroll / export) in the redesigned UI.

**Architecture:** The pure engine grows one capability (beam search, greedy = beam_width 1 in the same code path) and one new pure-ish module (`setplan/store.py`: JSON plan artifacts + a tag cache). rekordbox gains an ordered-setlist helper (the existing `add_tracks_and_playlist` only playlists *newly added* tracks — useless for a library already in rekordbox). The web layer adds 4 API endpoints + one self-contained page following the new design-token system.

**Tech Stack:** Python 3.12+, Typer, FastAPI/pydantic (already used), pytest. No new third-party deps.

## Global Constraints

- **Isolated worktree (a parallel session drives the main checkout):** work ONLY in `/Users/awandedibidi/dev/music-ops-wt-setplan2/librarian` (branch `feat/setplan-phase2`). Tests: `PYTHONPATH=/Users/awandedibidi/dev/music-ops-wt-setplan2/librarian/src /Users/awandedibidi/dev/music-ops/librarian/.venv/bin/python -m pytest -q`. Baseline: **251 passed, 3 skipped, 1 pre-existing StarletteDeprecationWarning**.
- **Read-only on the library.** Setplan never mutates library files. New writes allowed ONLY: plan artifacts + pool cache under the runs dir, exports under `--out`, and a rekordbox XML written to an explicit output path (never overwrite the input XML in place from setplan).
- **Never guess key/BPM:** unknown → neutral 0.5, never 0, never fabricated (in XML export too: omit attributes rather than invent them — matches `RekordboxAddition` semantics).
- **`follows_boost` stays additive-only.** Beam must not change scoring semantics — only search strategy.
- Python 3.12+, `from __future__ import annotations`, no new deps. Model policy: implementer subagents run on **Opus or Sonnet** (never Fable).
- The web page must be **self-contained/offline** (inline CSS/JS, system fonts, data-URI assets only) and reuse the design-token `:root` block **byte-identical** to `web/pulse.html`'s.
- API safety: mutating routes use `Depends(guard_origin)`; plan ids are validated against `^sp-[0-9a-f]{8}$` before touching the filesystem (no traversal); the server only ever reads the launch-time `library_root`.

## File Structure

- `src/librarian/setplan/search.py` — MODIFY: beam search + segment routing + `pinned` locks + seeded tie-breaks; shared annotation pass produces reasons/alternates for both greedy and beam.
- `src/librarian/setplan/score.py` — MODIFY: add `anchor_affinity()` (terminal steering bonus).
- `src/librarian/setplan/store.py` — CREATE: plan artifact save/load (`runs/setplan-<id>.json`), `spec_from_dict`, pool cache (`runs/setplan-pool.json`, per-file mtime+size invalidation, stores RAW tag strings).
- `src/librarian/setplan/spec.py` — MODIFY: add public `parse_journey()` (moved from cli.py).
- `src/librarian/rekordbox.py` — MODIFY: add `setlist_playlist()` (ordered playlist referencing EXISTING TrackIDs, adding only missing tracks).
- `src/librarian/setplan/export.py` — MODIFY: add `to_rekordbox_xml()`.
- `src/librarian/cli.py` — MODIFY: new flags, artifact persistence, export selection; `_parse_journey` becomes an alias of `spec.parse_journey`.
- `src/librarian/webapp/app.py` — MODIFY: 4 setplan endpoints.
- `src/librarian/web/setplan.html` — CREATE: the Setplan tab. `web/index.html`, `web/pulse.html`, `web/dedupe.html` — MODIFY: one nav line each.
- Tests: extend `tests/test_setplan_search.py`, `tests/test_setplan_score.py`, `tests/test_setplan_export.py`; CREATE `tests/test_setplan_store.py`, `tests/test_webapp_setplan.py`; extend `tests/test_rekordbox.py` if present else put XML tests in `test_setplan_export.py`.

---

### Task 1: Beam search + segment routing (`search.py`, `score.py`)

**Files:**
- Modify: `src/librarian/setplan/search.py` (replace the greedy loop)
- Modify: `src/librarian/setplan/score.py` (add `anchor_affinity`)
- Test: `tests/test_setplan_search.py`, `tests/test_setplan_score.py`

**Interfaces:**
- Consumes: `score_slot(cand, prev, ctx) -> (float, str)`; `_bpm_transition` (module-internal reuse); `camelot.harmonic`; `spec.target_percentile/genre_block_at`; `pool.norm/DEFAULT_ADJACENCY`.
- Produces: `build_set(pool, spec, follows=None, adjacency=None, *, beam_width=1, pinned=None) -> SetPlan` — **default beam_width=1 keeps every Phase 1 test meaningful**; `pinned: dict[int, str] | None` maps slot index → path-string, wins over opener/musts on collision; unresolvable pins are appended to `SetPlan.unmatched`. `score.anchor_affinity(cand, anchor, *, mode: str) -> float`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setplan_score.py`:

```python
def test_anchor_affinity_prefers_harmonic_handover():
    from librarian.setplan.score import anchor_affinity
    anchor = _c("X", "anchor", bpm=120.0, key=(5, "A"))
    near = _c("A", "near", bpm=120.0, key=(6, "A"))      # 1 step from 5A
    far = _c("B", "far", bpm=120.0, key=(11, "A"))       # 6 steps
    assert anchor_affinity(near, anchor, mode="loose") > anchor_affinity(far, anchor, mode="loose")
    unknown = _c("C", "unk", bpm=None, key=None)
    a = anchor_affinity(unknown, anchor, mode="loose")   # neutral, never zero
    assert a == 0.5 * 0.5 + 0.25 * 0.5
```

Append to `tests/test_setplan_search.py`:

```python
def _k(artist, title, key, bpm=120.0):
    # identical genre/bpm/plays so ONLY harmonic distance differentiates
    return _c(artist, title, bpm=bpm, key=key)


def test_beam_routes_into_anchor_better_than_greedy():
    # 4 slots (12 min @ 20 tph), must-play anchor lands at slot 2 (int(4*0.5)).
    # From the 8A opener, greedy's locally-best chain strands it far from the
    # 5A anchor; beam(8) finds the 7A -> 6A route (6A -> 5A is a 0.90 handover).
    from librarian import camelot as C
    pool = [
        _k("Op", "Opener", (8, "A")),
        _k("A1", "Nine", (9, "A")), _k("A2", "Ten", (10, "A")),
        _k("B1", "Seven", (7, "A")), _k("B2", "Six", (6, "A")),
        _k("Anch", "Anchor", (5, "A")),
    ]
    spec = GigSpec(minutes=12, journey=[("amapiano", 1.0)],
                   opener="Opener", must_play=["Anchor"])
    greedy = build_set(pool, spec, beam_width=1)
    beam = build_set(pool, spec, beam_width=8)
    anchor_slot = next(i for i, s in enumerate(beam.slots) if s.candidate.title == "Anchor")
    g_prev = greedy.slots[anchor_slot - 1].candidate
    b_prev = beam.slots[anchor_slot - 1].candidate
    g_h = C.harmonic(g_prev.camelot, (5, "A"), rising=True)
    b_h = C.harmonic(b_prev.camelot, (5, "A"), rising=True)
    assert b_h >= g_h
    assert b_h >= 0.85          # beam genuinely lands a clean handover


def test_pinned_slot_is_respected_and_labelled():
    pool = _pool(20)
    target = pool[7]
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)])
    plan = build_set(pool, spec, pinned={3: str(target.path)})
    assert plan.slots[3].candidate.path == target.path
    assert "locked" in plan.slots[3].reason


def test_unresolvable_pin_reported_not_silent():
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)])
    plan = build_set(_pool(20), spec, pinned={2: "/nope/ghost.mp3"})
    assert "/nope/ghost.mp3" in plan.unmatched


def test_same_seed_same_plan():
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)], seed=42)
    a = build_set(_pool(30), spec, beam_width=8)
    b = build_set(_pool(30), spec, beam_width=8)
    assert [s.candidate.path for s in a.slots] == [s.candidate.path for s in b.slots]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/Users/awandedibidi/dev/music-ops-wt-setplan2/librarian/src /Users/awandedibidi/dev/music-ops/librarian/.venv/bin/python -m pytest -q tests/test_setplan_score.py tests/test_setplan_search.py`
Expected: FAIL — `anchor_affinity` not defined; `build_set() got an unexpected keyword argument 'beam_width'`.

- [ ] **Step 3: Implement**

Add to `src/librarian/setplan/score.py` (bottom of file):

```python
def anchor_affinity(cand, anchor, *, mode: str) -> float:
    """Terminal steering bonus: how well `cand` hands over into a fixed anchor.

    Added to the LAST expansion of a beam segment so the search routes toward
    the must-play instead of arriving with a train-wreck transition. Neutral
    (never zero) on unknown key/BPM, like every other component.
    """
    h = camelot.harmonic(cand.camelot, anchor.camelot, rising=True, mode=mode)
    t = _bpm_transition(cand.bpm, anchor.bpm)
    return 0.5 * h + 0.25 * t
```

Replace `src/librarian/setplan/search.py`'s `build_set` (keep `Slot`, `SetPlan`, `_in_block`, `_percentile_to_bpm`, `_match` as they are; add `import random` and the imports shown):

```python
import random

from .score import anchor_affinity, score_slot


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
```

Note the deliberate behavior changes vs Phase 1 (all improvements, none breaking the existing tests): alternates now exclude every chosen track (an alternate already in the set is useless); the empty-artist guard keeps untagged files from excluding each other; `Slot.index` is the output position (contiguous even if a segment truncated).

- [ ] **Step 4: Run the focused tests, then the full suite**

Run: `... -m pytest -q tests/test_setplan_search.py tests/test_setplan_score.py` → all pass (incl. the 8 pre-existing search tests, unchanged).
Then: `... -m pytest -q` → expected **256 passed** (251 + 5 new), 3 skipped.

- [ ] **Step 5: Commit**

```bash
git add src/librarian/setplan/search.py src/librarian/setplan/score.py tests/test_setplan_search.py tests/test_setplan_score.py
git commit -m "feat(setplan): beam search with segment routing into anchors (greedy = B1)"
```

---

### Task 2: Plan artifacts + pool cache (`setplan/store.py`)

**Files:**
- Create: `src/librarian/setplan/store.py`
- Test: `tests/test_setplan_store.py`

**Interfaces:**
- Consumes: `SetPlan`/`Slot` (search), `Candidate`, `history_stats`, `norm` (pool), `camelot.parse_key`, `metadata.read_meta`, `paths.audio_files`, `GigSpec`.
- Produces: `SETPLAN_ID_RE`; `new_plan_id() -> str` (`sp-` + 8 hex); `plan_to_dict(plan, plan_id) -> dict`; `save_plan(plan, runs_dir, plan_id=None) -> str`; `load_plan_dict(plan_id, runs_dir) -> dict | None` (returns None for missing; raises `ValueError` on malformed id); `spec_from_dict(d) -> GigSpec`; `load_pool_cached(root, cache_path, sessions=None) -> tuple[list[Candidate], dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setplan_store.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from librarian.setplan.pool import Candidate, norm
from librarian.setplan.search import Slot, SetPlan
from librarian.setplan.spec import GigSpec
from librarian.setplan import store


def _plan():
    c = Candidate(path=Path("/lib/a.mp3"), artist="A", title="x", genre="amapiano",
                  bpm=112.0, camelot=(8, "A"), length_s=200.0,
                  norm_key=norm("A x"), low_bitrate=False, plays=3)
    alt = Candidate(path=Path("/lib/b.mp3"), artist="B", title="y", genre="amapiano",
                    bpm=None, camelot=None, length_s=None,
                    norm_key=norm("B y"), low_bitrate=False)
    slot = Slot(index=0, candidate=c, reason="r",
                alternates=[{"candidate": alt, "reason": "alt r"}], clock_min=0.0)
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)], must_play=["x"], seed=7)
    return SetPlan(spec=spec, slots=[slot], unmatched=["ghost"])


def test_save_load_round_trip(tmp_path):
    pid = store.save_plan(_plan(), tmp_path)
    assert store.SETPLAN_ID_RE.fullmatch(pid)
    d = store.load_plan_dict(pid, tmp_path)
    assert d["id"] == pid and d["unmatched"] == ["ghost"]
    s0 = d["slots"][0]
    assert s0["candidate"]["path"] == "/lib/a.mp3" and s0["candidate"]["camelot"] == [8, "A"]
    assert s0["alternates"][0]["candidate"]["bpm"] is None
    spec = store.spec_from_dict(d["spec"])
    assert spec.journey == [("amapiano", 1.0)] and spec.seed == 7


def test_bad_ids_rejected_and_missing_none(tmp_path):
    with pytest.raises(ValueError):
        store.load_plan_dict("../../etc/passwd", tmp_path)
    assert store.load_plan_dict("sp-00000000", tmp_path) is None


def test_pool_cache_avoids_rereads(tmp_path, monkeypatch):
    root = tmp_path / "lib"; root.mkdir()
    for name in ("a.mp3", "b.mp3"):
        (root / name).write_bytes(b"x" * 64)
    calls = []
    from librarian.metadata import TrackMeta
    def fake_read(p):
        calls.append(p.name)
        return TrackMeta(path=p, artist="A", title=p.stem, genre="amapiano",
                         bpm="120", key="8A", length_s=100.0)
    monkeypatch.setattr(store, "read_meta", fake_read)
    cache = tmp_path / "pool.json"
    cands, _ = store.load_pool_cached(root, cache, sessions=[["A - a"]])
    assert len(cands) == 2 and len(calls) == 2
    assert cands[0].bpm == 120.0 and cands[0].plays in (0, 1)
    calls.clear()
    cands2, _ = store.load_pool_cached(root, cache, sessions=[["A - a"]])
    assert len(cands2) == 2 and calls == []            # served from cache
    import os, time
    (root / "a.mp3").write_bytes(b"y" * 65)            # size change -> re-read just a
    cands3, _ = store.load_pool_cached(root, cache)
    assert calls == ["a.mp3"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `... -m pytest -q tests/test_setplan_store.py` — FAIL (no module `librarian.setplan.store`).

- [ ] **Step 3: Implement**

```python
# src/librarian/setplan/store.py
"""Persistence for setplan: plan artifacts (runs/setplan-<id>.json) and the
pool cache (tag reads are the slow part of a 9k-file run).

The cache stores RAW tag strings (bpm/key as tagged), so parser fixes apply on
the next load without invalidating the cache. Writes are atomic (tmp+rename).
Nothing here ever writes inside the library root.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict
from pathlib import Path

from ..metadata import read_meta
from ..paths import audio_files
from .. import camelot
from .pool import Candidate, history_stats, norm
from .search import SetPlan, Slot
from .spec import GigSpec

SETPLAN_ID_RE = re.compile(r"^sp-[0-9a-f]{8}$")
CACHE_VERSION = 1


def new_plan_id() -> str:
    return "sp-" + uuid.uuid4().hex[:8]


def _cand_dict(c) -> dict:
    return {"path": str(c.path), "artist": c.artist, "title": c.title,
            "genre": c.genre, "bpm": c.bpm,
            "camelot": list(c.camelot) if c.camelot else None,
            "length_s": c.length_s, "plays": c.plays}


def plan_to_dict(plan: SetPlan, plan_id: str) -> dict:
    return {
        "id": plan_id,
        "spec": asdict(plan.spec),
        "unmatched": list(plan.unmatched),
        "slots": [{
            "index": s.index, "clock_min": s.clock_min, "reason": s.reason,
            "candidate": _cand_dict(s.candidate),
            "alternates": [{"reason": a["reason"], "candidate": _cand_dict(a["candidate"])}
                           for a in s.alternates],
        } for s in plan.slots],
    }


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_plan(plan: SetPlan, runs_dir: Path, plan_id: str | None = None) -> str:
    pid = plan_id or new_plan_id()
    if not SETPLAN_ID_RE.fullmatch(pid):
        raise ValueError(f"bad plan id: {pid!r}")
    runs_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(runs_dir / f"setplan-{pid}.json",
                  json.dumps(plan_to_dict(plan, pid), indent=1))
    return pid


def load_plan_dict(plan_id: str, runs_dir: Path) -> dict | None:
    if not SETPLAN_ID_RE.fullmatch(plan_id):
        raise ValueError(f"bad plan id: {plan_id!r}")
    f = runs_dir / f"setplan-{plan_id}.json"
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def spec_from_dict(d: dict) -> GigSpec:
    known = {k: v for k, v in d.items() if k in GigSpec.__dataclass_fields__}
    if known.get("journey"):
        known["journey"] = [tuple(b) for b in known["journey"]]
    if known.get("bpm_range"):
        known["bpm_range"] = tuple(known["bpm_range"])
    return GigSpec(**known)


# --- pool cache -------------------------------------------------------------

def _stat_sig(p: Path) -> list[int]:
    st = p.stat()
    return [st.st_mtime_ns, st.st_size]


def load_pool_cached(root: Path, cache_path: Path,
                     sessions: list[list[str]] | None = None) -> tuple[list[Candidate], dict]:
    """build_pool, but tag reads are served from cache_path when (mtime,size) match."""
    cached: dict = {}
    try:
        blob = json.loads(cache_path.read_text(encoding="utf-8"))
        if blob.get("version") == CACHE_VERSION and blob.get("root") == str(root):
            cached = blob.get("files", {})
    except (OSError, ValueError):
        cached = {}

    plays, positions, follows = history_stats(sessions or [])
    files_out: dict = {}
    cands: list[Candidate] = []
    dirty = False
    for p in audio_files(root):
        rel = str(p.relative_to(root))
        sig = _stat_sig(p)
        entry = cached.get(rel)
        if entry and entry.get("sig") == sig:
            meta = entry["meta"]
        else:
            m = read_meta(p)
            meta = {"artist": m.artist, "title": m.title, "genre": m.genre,
                    "bpm": m.bpm, "key": m.key, "length_s": m.length_s,
                    "low_bitrate": m.low_bitrate}
            dirty = True
        files_out[rel] = {"sig": sig, "meta": meta}
        title = meta["title"] or p.stem
        nk = norm(f"{meta['artist'] or ''} {title}")
        from .pool import _to_bpm
        cands.append(Candidate(
            path=p, artist=meta["artist"] or "", title=title,
            genre=(meta["genre"] or "").strip(), bpm=_to_bpm(meta["bpm"]),
            camelot=camelot.parse_key(meta["key"]), length_s=meta["length_s"],
            norm_key=nk, low_bitrate=bool(meta["low_bitrate"]),
            plays=plays.get(nk, 0), positions=positions.get(nk, []),
        ))
    if dirty or set(files_out) != set(cached):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(cache_path, json.dumps(
            {"version": CACHE_VERSION, "root": str(root), "files": files_out}))
    return cands, follows
```

- [ ] **Step 4: Run tests** — `... -m pytest -q tests/test_setplan_store.py` → 3 passed; full suite → **259 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/librarian/setplan/store.py tests/test_setplan_store.py
git commit -m "feat(setplan): plan artifacts (runs/setplan-<id>.json) + pool tag cache"
```

---

### Task 3: rekordbox ordered-setlist export

**Files:**
- Modify: `src/librarian/rekordbox.py` (add `setlist_playlist`)
- Modify: `src/librarian/setplan/export.py` (add `to_rekordbox_xml`)
- Test: `tests/test_setplan_export.py`

**Interfaces:**
- Consumes: `RekordboxAddition` (model.py: `location, name, artist=None, genre=None, total_time=None, average_bpm=None, tonality=None, bitrate_kbps=None, kind=None`), rekordbox module internals `_match_key`, `_find_or_create_playlist`, `path_to_location`.
- Produces: `rekordbox.setlist_playlist(xml_in: Path, ordered: list[RekordboxAddition], playlist_name: str, xml_out: Path | None = None) -> tuple[int, int]` (n added, n playlist entries); `export.to_rekordbox_xml(plan, xml_in: Path, xml_out: Path, playlist_name: str) -> tuple[int, int]`.

**Why a new function:** `add_tracks_and_playlist` playlists only *newly added* TrackIDs — a setplan is almost entirely tracks already in the collection, so the playlist would come out empty. `setlist_playlist` references existing TrackIDs by Location match, adds only missing tracks, and (re)builds the playlist node **in set order** (replace semantics: re-export is deterministic, order is the product).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_setplan_export.py`:

```python
def _xml(tmp_path, locations):
    import xml.etree.ElementTree as ET
    from librarian.rekordbox import path_to_location
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    coll = ET.SubElement(root, "COLLECTION", {"Entries": str(len(locations))})
    for i, loc in enumerate(locations, start=1):
        ET.SubElement(coll, "TRACK", {"TrackID": str(i), "Name": loc.stem,
                                      "Location": path_to_location(loc)})
    ET.SubElement(root, "PLAYLISTS")
    p = tmp_path / "collection.xml"
    ET.ElementTree(root).write(p, encoding="utf-8", xml_declaration=True)
    return p


def test_rekordbox_setlist_orders_existing_and_adds_missing(tmp_path):
    import xml.etree.ElementTree as ET
    from librarian.setplan.export import to_rekordbox_xml
    from librarian.setplan.search import Slot, SetPlan
    from librarian.setplan.spec import GigSpec
    from librarian.setplan.pool import Candidate, norm

    existing = tmp_path / "lib" / "Known.mp3"
    missing = tmp_path / "lib" / "New.mp3"
    existing.parent.mkdir()
    existing.write_bytes(b"0"); missing.write_bytes(b"0")
    xml_in = _xml(tmp_path, [existing])

    def cand(p, key):
        return Candidate(path=p, artist="A", title=p.stem, genre="amapiano", bpm=120.0,
                         camelot=key, length_s=180.0, norm_key=norm(f"A {p.stem}"),
                         low_bitrate=False)
    plan = SetPlan(spec=GigSpec(minutes=6), slots=[
        Slot(index=0, candidate=cand(missing, (8, "A")), reason="r"),
        Slot(index=1, candidate=cand(existing, (9, "A")), reason="r"),
    ])
    out = tmp_path / "setplan.rekordbox.xml"
    added, entries = to_rekordbox_xml(plan, xml_in, out, "setplan test")
    assert (added, entries) == (1, 2)

    tree = ET.parse(out)
    coll = tree.getroot().find("COLLECTION")
    assert len(coll.findall("TRACK")) == 2               # Known kept, New added once
    node = next(n for n in tree.getroot().find("PLAYLISTS").iter("NODE")
                if n.get("Name") == "setplan test")
    keys = [t.get("Key") for t in node.findall("TRACK")]
    by_id = {t.get("TrackID"): t.get("Name") for t in coll.findall("TRACK")}
    assert [by_id[k] for k in keys] == ["New", "Known"]  # SET ORDER preserved

    added2, entries2 = to_rekordbox_xml(plan, out, out, "setplan test")
    assert added2 == 0 and entries2 == 2                 # idempotent re-export
```

- [ ] **Step 2: Run to verify FAIL** — `to_rekordbox_xml` not defined.

- [ ] **Step 3: Implement**

Add to `src/librarian/rekordbox.py` (after `add_tracks_and_playlist`; reuse its conventions — `_match_key`, `location_to_path`, `path_to_location`, `_find_or_create_playlist`, atomic write via the module's existing pattern):

```python
def setlist_playlist(
    xml_in: Path,
    ordered: list[RekordboxAddition],
    playlist_name: str,
    xml_out: Path | None = None,
) -> tuple[int, int]:
    """(Re)build a playlist NODE containing ``ordered`` IN ORDER.

    Unlike add_tracks_and_playlist (which playlists only newly-added tracks),
    this references EXISTING collection tracks by their TrackID and adds only
    the missing ones — a setlist is mostly tracks rekordbox already knows.
    Replace semantics on the node: re-export is deterministic. Never guesses
    metadata; attributes are written only when present on the addition.
    """
    out = xml_out or xml_in
    tree = ET.parse(xml_in)
    root = tree.getroot()
    collection = root.find("COLLECTION")
    if collection is None:
        raise ValueError("rekordbox XML has no <COLLECTION>")

    loc_to_id: dict[str, str] = {}
    existing_ids: list[int] = []
    for track in collection.iter("TRACK"):
        tid = track.get("TrackID")
        loc = track.get("Location")
        if tid and tid.isdigit():
            existing_ids.append(int(tid))
        if tid and loc and loc.startswith("file://"):
            loc_to_id[_match_key(location_to_path(loc))] = tid
    playlists = root.find("PLAYLISTS")
    if playlists is None:
        playlists = ET.SubElement(root, "PLAYLISTS")
    for entry in playlists.iter("TRACK"):
        key = entry.get("Key")
        if key and key.isdigit():
            existing_ids.append(int(key))
    next_id = (max(existing_ids) + 1) if existing_ids else 1

    added = 0
    keys_in_order: list[str] = []
    for add in ordered:
        k = _match_key(add.location)
        tid = loc_to_id.get(k)
        if tid is None:
            tid = str(next_id)
            next_id += 1
            attrs = {"TrackID": tid, "Name": add.name,
                     "Location": path_to_location(add.location.absolute())}
            if add.artist:
                attrs["Artist"] = add.artist
            if add.genre:
                attrs["Genre"] = add.genre
            if add.total_time is not None:
                attrs["TotalTime"] = str(add.total_time)
            if add.average_bpm:
                attrs["AverageBpm"] = add.average_bpm
            if add.tonality:
                attrs["Tonality"] = add.tonality
            ET.SubElement(collection, "TRACK", attrs)
            loc_to_id[k] = tid
            added += 1
        keys_in_order.append(tid)
    collection.set("Entries", str(len(collection.findall("TRACK"))))

    node = _find_or_create_playlist(playlists, playlist_name)
    for child in list(node):
        node.remove(child)                              # replace: order is the product
    for tid in keys_in_order:
        ET.SubElement(node, "TRACK", {"Key": tid})
    node.set("Entries", str(len(keys_in_order)))

    _atomic_write_xml(tree, out)
    return added, len(keys_in_order)
```

(If the module's atomic-write helper has a different name, use the exact same call `add_tracks_and_playlist` ends with — read the end of that function and mirror it.)

Add to `src/librarian/setplan/export.py`:

```python
def to_rekordbox_xml(plan, xml_in: Path, xml_out: Path, playlist_name: str) -> tuple[int, int]:
    """Write the plan as an ordered rekordbox playlist (new XML at xml_out)."""
    from ..model import RekordboxAddition
    from .. import rekordbox

    ordered = []
    for s in plan.slots:
        c = s.candidate
        ordered.append(RekordboxAddition(
            location=Path(c.path), name=c.title, artist=c.artist or None,
            genre=c.genre or None,
            total_time=int(c.length_s) if c.length_s else None,
            average_bpm=f"{c.bpm:.2f}" if c.bpm else None,
            tonality=None,        # we hold a Camelot tuple, not the tagged spelling — never guess
        ))
    return rekordbox.setlist_playlist(xml_in, ordered, playlist_name, xml_out=xml_out)
```

- [ ] **Step 4: Run** focused → passes; full suite → **260 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/librarian/rekordbox.py src/librarian/setplan/export.py tests/test_setplan_export.py
git commit -m "feat(setplan): ordered rekordbox setlist export (existing TrackIDs + add-missing)"
```

---

### Task 4: CLI — flags, artifact, export selection

**Files:**
- Modify: `src/librarian/setplan/spec.py` (add `parse_journey`), `src/librarian/cli.py`
- Test: `tests/test_setplan_search.py` (parse helpers), `tests/test_setplan_spec.py`

**Interfaces:**
- Produces: `spec.parse_journey(s: str) -> list[tuple[str, float]]` (moved verbatim from cli.py; cli keeps `_parse_journey = parse_journey` alias so the existing test still imports it); `cli._parse_bpm_range(s: str) -> tuple[int, int] | None` (`"108-128"` → `(108, 128)`; `""` → None; junk → `typer.BadParameter`).
- The `setplan` command gains: `--bpm-range ""`, `--allow-low-bitrate`, `--tracks-per-hour 20`, `--beam 8`, `--seed None`, `--export "m3u8,md"` (csv of `m3u8|md|xml`), `--rekordbox-xml None`, `--playlist-name None`; persists the artifact via `store.save_plan(plan, default_runs_dir(root))` and echoes the id; uses `store.load_pool_cached(root, default_runs_dir(root) / "setplan-pool.json", sessions)`.

- [ ] **Step 1: Failing tests**

Append to `tests/test_setplan_spec.py`:

```python
def test_parse_journey_moved_public():
    from librarian.setplan.spec import parse_journey
    assert parse_journey("amapiano:60,afrobeats:40") == [("amapiano", 0.6), ("afrobeats", 0.4)]
```

Append to `tests/test_setplan_search.py`:

```python
def test_parse_bpm_range():
    import pytest, typer
    from librarian.cli import _parse_bpm_range
    assert _parse_bpm_range("108-128") == (108, 128)
    assert _parse_bpm_range("") is None
    with pytest.raises(typer.BadParameter):
        _parse_bpm_range("fast")
    with pytest.raises(typer.BadParameter):
        _parse_bpm_range("128-108")
```

- [ ] **Step 2: Run → FAIL** (no `parse_journey` in spec, no `_parse_bpm_range`).

- [ ] **Step 3: Implement**

`spec.py`: move the body of `cli._parse_journey` verbatim into a public `parse_journey(s)` (same docstring). `cli.py`: replace the function with

```python
from .setplan.spec import parse_journey as _parse_journey   # module level, near other imports


def _parse_bpm_range(s: str) -> tuple[int, int] | None:
    """'108-128' -> (108, 128); '' -> None; anything else -> BadParameter."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{2,3})\s*-\s*(\d{2,3})", s)
    if not m or int(m.group(1)) > int(m.group(2)):
        raise typer.BadParameter("expected LO-HI, e.g. 108-128", param_hint="--bpm-range")
    return int(m.group(1)), int(m.group(2))
```

(`import re` is already at the top of cli.py — verify; add if absent.)

Extend the `setplan` command signature (after the existing options, before `out_dir`):

```python
    bpm_range: Annotated[str, typer.Option("--bpm-range", help="Hard BPM filter, e.g. 108-128")] = "",
    allow_low_bitrate: Annotated[bool, typer.Option("--allow-low-bitrate", help="Include <320kbps files")] = False,
    tracks_per_hour: Annotated[int, typer.Option("--tracks-per-hour", help="Slot density")] = 20,
    beam: Annotated[int, typer.Option("--beam", help="Beam width (1 = greedy)")] = 8,
    seed: Annotated[Optional[int], typer.Option("--seed", help="Reroll seed (reproducible)")] = None,
    export: Annotated[str, typer.Option("--export", help="Comma list: m3u8,md,xml")] = "m3u8,md",
    rekordbox_xml: Annotated[Optional[Path], typer.Option("--rekordbox-xml", help="Collection XML (needed for xml export)")] = None,
    playlist_name: Annotated[Optional[str], typer.Option("--playlist-name", help="Playlist name for xml export")] = None,
```

And rework the body after validation:

```python
    fmts = {f.strip() for f in export.split(",") if f.strip()}
    if not fmts <= {"m3u8", "md", "xml"}:
        raise typer.BadParameter("choose from m3u8, md, xml", param_hint="--export")
    if "xml" in fmts and rekordbox_xml is None:
        raise typer.BadParameter("xml export needs --rekordbox-xml <collection.xml>",
                                 param_hint="--export")

    spec = GigSpec(minutes=minutes, journey=_parse_journey(journey), arc=arc,
                   freshness=freshness, harmonic=harmonic, must_play=list(must),
                   avoid=list(avoid), opener=opener, seed=seed,
                   bpm_range=_parse_bpm_range(bpm_range),
                   allow_low_bitrate=allow_low_bitrate, tracks_per_hour=tracks_per_hour)
    runs_dir = default_runs_dir(root)
    typer.echo(f"Prepping {minutes}min {arc} set from {root} · history: {len(sessions)} past set(s)")
    from .setplan import store
    cands, follows = store.load_pool_cached(root, runs_dir / "setplan-pool.json", sessions=sessions)
    plan = build_set(cands, spec, follows=follows, beam_width=max(1, beam))
    ...                                   # (unchanged: unmatched warnings, empty check, slot print)
    plan_id = store.save_plan(plan, runs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if "m3u8" in fmts:
        to_m3u8(plan, out_dir / "set.m3u8")
    if "md" in fmts:
        to_markdown(plan, out_dir / "set.md")
    if "xml" in fmts:
        from .setplan.export import to_rekordbox_xml
        added, entries = to_rekordbox_xml(plan, rekordbox_xml, out_dir / "setplan.rekordbox.xml",
                                          playlist_name or f"setplan {plan_id}")
        typer.echo(f"rekordbox: {entries} playlist entries ({added} new tracks) → {out_dir}/setplan.rekordbox.xml")
    typer.echo(f"\nplan {plan_id} saved → {runs_dir}/setplan-{plan_id}.json")
    typer.echo(f"Exports → {out_dir}  (nothing in the library was changed)")
```

(Keep the existing arc/harmonic validation, history adapter, unmatched warnings, and slot printing exactly as they are.)

- [ ] **Step 4: Run** focused + full suite → **262 passed**. Then a real read-only smoke from the worktree (controller repeats this at review):

```bash
PYTHONPATH=/Users/awandedibidi/dev/music-ops-wt-setplan2/librarian/src \
  /Users/awandedibidi/dev/music-ops/librarian/.venv/bin/python -c "from librarian.cli import app; app()" \
  setplan ~/DJ/library/Afrobeats --minutes 20 --beam 8 --bpm-range 100-130 --out /tmp/sp-smoke
```
Expected: a set prints; second run is markedly faster (cache); `setplan-sp-*.json` + `setplan-pool.json` appear under `~/DJ/.librarian-runs/`; library untouched.

- [ ] **Step 5: Commit**

```bash
git add src/librarian/setplan/spec.py src/librarian/cli.py tests/test_setplan_spec.py tests/test_setplan_search.py
git commit -m "feat(setplan): CLI flags (bpm-range/beam/seed/export/xml), artifact + pool cache wiring"
```

---

### Task 5: API endpoints (`webapp/app.py`)

**Files:**
- Modify: `src/librarian/webapp/app.py`
- Test: `tests/test_webapp_setplan.py` (create)

**Interfaces:**
- Consumes: `store.*`, `spec.parse_journey/ARC_NAMES/GigSpec`, `build_set`, `to_m3u8/to_markdown/to_rekordbox_xml` (render md/m3u8 to strings via tmp files in runs_dir), `AppState` (`st.config.library_root/runs_dir/rekordbox_xml`), `guard_origin`.
- Produces routes: `POST /api/setplan` (SetplanRequest → `{"plan": <dict>}`), `GET /api/setplan/{plan_id}`, `POST /api/setplan/{plan_id}/reroll` (RerollRequest), `GET /api/setplan/{plan_id}/export?fmt=m3u8|md|xml`.

- [ ] **Step 1: Failing test**

```python
# tests/test_webapp_setplan.py
"""Setplan API: build, fetch, reroll-with-locks, export — read-only on the library."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from librarian.webapp.app import create_app
from librarian.webapp.state import AppConfig

from conftest import make_library, tree_digest


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    root = tmp_path / "lib"; root.mkdir(exist_ok=True)
    cfg = AppConfig(library_root=root, runs_dir=tmp_path / "runs")
    return TestClient(create_app(cfg), base_url="http://127.0.0.1"), root


def test_setplan_build_fetch_reroll_export(tmp_path):
    client, root = _client(tmp_path)
    make_library(root, [f"Artist{i} - Track{i}.mp3" for i in range(12)])
    before = tree_digest(root)

    r = client.post("/api/setplan", json={"minutes": 9})       # 3 slots
    assert r.status_code == 200
    plan = r.json()["plan"]
    pid = plan["id"]
    assert len(plan["slots"]) == 3

    assert client.get(f"/api/setplan/{pid}").json()["id"] == pid
    assert client.get("/api/setplan/sp-00000000").status_code == 404
    assert client.get("/api/setplan/..evil").status_code == 400

    locked = plan["slots"][0]["candidate"]["path"]
    r2 = client.post(f"/api/setplan/{pid}/reroll", json={"from_slot": 1, "seed": 9})
    assert r2.status_code == 200
    plan2 = r2.json()["plan"]
    assert plan2["id"] == pid
    assert plan2["slots"][0]["candidate"]["path"] == locked    # prefix held

    m3u = client.get(f"/api/setplan/{pid}/export", params={"fmt": "m3u8"})
    assert m3u.status_code == 200 and m3u.text.startswith("#EXTM3U")
    assert client.get(f"/api/setplan/{pid}/export", params={"fmt": "xml"}).status_code == 409
    assert client.get(f"/api/setplan/{pid}/export", params={"fmt": "tar"}).status_code == 422

    assert tree_digest(root) == before                          # library untouched


def test_setplan_validates_arc_and_guards_origin(tmp_path):
    client, root = _client(tmp_path)
    make_library(root, ["A - x.mp3"])
    assert client.post("/api/setplan", json={"minutes": 9, "arc": "banana"}).status_code == 400
    evil = client.post("/api/setplan", json={"minutes": 9},
                       headers={"Origin": "http://evil.example"})
    assert evil.status_code in (400, 403)
```

- [ ] **Step 2: Run → FAIL** (404 on /api/setplan).

- [ ] **Step 3: Implement** — in `webapp/app.py`, add models near the other BaseModels and routes near the pulse block:

```python
class SetplanRequest(BaseModel):
    minutes: int = 90
    journey: str = ""
    arc: str = "peak"
    freshness: float = 0.3
    harmonic: Literal["strict", "loose", "off"] = "loose"
    must: list[str] = []
    avoid: list[str] = []
    opener: str | None = None
    bpm_range: str = ""
    allow_low_bitrate: bool = False
    tracks_per_hour: int = 20
    beam: int = 8
    seed: int | None = None


class RerollRequest(BaseModel):
    locks: dict[int, str] = {}      # slot index -> candidate path to pin
    from_slot: int | None = None    # additionally pin every slot before this
    seed: int | None = None
```

```python
    def _setplan_pool(st: AppState):
        from ..setplan import store
        sessions: list[list[str]] = []
        try:
            from .. import pulse_usb
            for vol in pulse_usb.find_usbs():
                sess, _m, _f = pulse_usb.read_stick(vol)
                sessions.extend(s["tracks"] for s in sess if s.get("tracks"))
        except Exception:
            pass
        cache = st.config.runs_dir / "setplan-pool.json"
        return store.load_pool_cached(st.config.library_root, cache, sessions=sessions)

    @app.post("/api/setplan", dependencies=[Depends(guard_origin)])
    def post_setplan(req: SetplanRequest, st: AppState = Depends(state)) -> dict:
        from ..setplan import store
        from ..setplan.search import build_set
        from ..setplan.spec import ARC_NAMES, GigSpec, parse_journey
        if req.arc not in ARC_NAMES:
            raise HTTPException(400, f"unknown arc {req.arc!r}")
        try:
            journey = parse_journey(req.journey)
            lo_hi = None
            if req.bpm_range.strip():
                lo, hi = (int(x) for x in req.bpm_range.split("-", 1))
                if lo > hi:
                    raise ValueError
                lo_hi = (lo, hi)
        except ValueError:
            raise HTTPException(400, "bad journey or bpm_range")
        spec = GigSpec(minutes=req.minutes, journey=journey, arc=req.arc,
                       freshness=req.freshness, harmonic=req.harmonic,
                       must_play=req.must, avoid=req.avoid, opener=req.opener,
                       bpm_range=lo_hi, allow_low_bitrate=req.allow_low_bitrate,
                       tracks_per_hour=req.tracks_per_hour, seed=req.seed)
        cands, follows = _setplan_pool(st)
        plan = build_set(cands, spec, follows=follows, beam_width=max(1, req.beam))
        pid = store.save_plan(plan, st.config.runs_dir)
        return {"plan": store.plan_to_dict(plan, pid)}

    def _load_setplan(plan_id: str, st: AppState) -> dict:
        from ..setplan import store
        try:
            d = store.load_plan_dict(plan_id, st.config.runs_dir)
        except ValueError:
            raise HTTPException(400, "bad plan id")
        if d is None:
            raise HTTPException(404, "no such plan")
        return d

    @app.get("/api/setplan/{plan_id}")
    def get_setplan(plan_id: str, st: AppState = Depends(state)) -> dict:
        return _load_setplan(plan_id, st)

    @app.post("/api/setplan/{plan_id}/reroll", dependencies=[Depends(guard_origin)])
    def post_setplan_reroll(plan_id: str, req: RerollRequest,
                            st: AppState = Depends(state)) -> dict:
        from ..setplan import store
        from ..setplan.search import build_set
        d = _load_setplan(plan_id, st)
        spec = store.spec_from_dict(d["spec"])
        spec.seed = req.seed if req.seed is not None else ((spec.seed or 0) + 1)
        pins = {int(k): v for k, v in req.locks.items()}
        if req.from_slot is not None:
            for s in d["slots"]:
                if s["index"] < req.from_slot:
                    pins.setdefault(s["index"], s["candidate"]["path"])
        cands, follows = _setplan_pool(st)
        plan = build_set(cands, spec, follows=follows, beam_width=8, pinned=pins)
        store.save_plan(plan, st.config.runs_dir, plan_id=plan_id)
        return {"plan": store.plan_to_dict(plan, plan_id)}

    @app.get("/api/setplan/{plan_id}/export")
    def get_setplan_export(plan_id: str, fmt: Literal["m3u8", "md", "xml"],
                           st: AppState = Depends(state)):
        d = _load_setplan(plan_id, st)
        lines_m3u, lines_md = [], []
        if fmt == "m3u8":
            lines_m3u.append("#EXTM3U")
            for s in d["slots"]:
                c = s["candidate"]
                secs = int(c["length_s"] or -1)
                lines_m3u.append(f"#EXTINF:{secs},{c['artist']} - {c['title']}")
                lines_m3u.append(c["path"])
            return PlainTextResponse("\n".join(lines_m3u) + "\n", media_type="audio/x-mpegurl",
                                     headers={"Content-Disposition": f'attachment; filename="{plan_id}.m3u8"'})
        if fmt == "md":
            for s in d["slots"]:
                c = s["candidate"]
                key = f"{c['camelot'][0]}{c['camelot'][1]}" if c["camelot"] else "—"
                bpm = int(c["bpm"]) if c["bpm"] else "—"
                lines_md.append(f"{s['index'] + 1:>2}. **{c['artist']} — {c['title']}** · {bpm} BPM · {key}")
                lines_md.append(f"    _{s['reason']}_")
            return PlainTextResponse("\n".join(lines_md) + "\n", media_type="text/markdown",
                                     headers={"Content-Disposition": f'attachment; filename="{plan_id}.md"'})
        if st.config.rekordbox_xml is None:
            raise HTTPException(409, "no rekordbox XML configured (start serve with --rekordbox-xml)")
        from ..setplan.export import to_rekordbox_xml
        from ..setplan import store as _store
        from ..setplan.search import SetPlan, Slot
        # rebuild a minimal SetPlan from the artifact for the exporter
        from ..setplan.pool import Candidate, norm as _norm
        slots = []
        for s in d["slots"]:
            c = s["candidate"]
            slots.append(Slot(index=s["index"], reason=s["reason"], clock_min=s["clock_min"],
                              candidate=Candidate(path=Path(c["path"]), artist=c["artist"] or "",
                                                  title=c["title"], genre=c["genre"] or "",
                                                  bpm=c["bpm"],
                                                  camelot=tuple(c["camelot"]) if c["camelot"] else None,
                                                  length_s=c["length_s"],
                                                  norm_key=_norm(f"{c['artist']} {c['title']}"),
                                                  low_bitrate=False)))
        plan = SetPlan(spec=_store.spec_from_dict(d["spec"]), slots=slots)
        out = st.config.runs_dir / f"setplan-{plan_id}.rekordbox.xml"
        to_rekordbox_xml(plan, st.config.rekordbox_xml, out, f"setplan {plan_id}")
        return PlainTextResponse(out.read_text(encoding="utf-8"), media_type="application/xml",
                                 headers={"Content-Disposition": f'attachment; filename="{plan_id}.rekordbox.xml"'})
```

- [ ] **Step 4: Run** `tests/test_webapp_setplan.py` → 2 passed; full suite → **264 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/librarian/webapp/app.py tests/test_webapp_setplan.py
git commit -m "feat(setplan): web API — build/fetch/reroll-with-locks/export endpoints"
```

---

### Task 6: The Setplan web tab

**Files:**
- Create: `src/librarian/web/setplan.html`
- Modify: `src/librarian/web/index.html`, `web/pulse.html`, `web/dedupe.html` (add one nav tab each)
- Test: `tests/test_webapp_setplan.py` (page-served test)

**Interfaces:** consumes the four Task 5 endpoints only. Follows the redesigned design system: copy the `:root` token block **byte-identical** from `web/pulse.html` (lines starting `  :root {` through the closing `  }` — verify with `grep -A17 ':root {' web/pulse.html`), the same app-bar/nav markup, kickers, `.stat`/`.pill`/`.banner` vocabulary. Setplan's identity dot: `#b98ede` (violet). No external assets.

- [ ] **Step 1: Failing test** — append to `tests/test_webapp_setplan.py`:

```python
def test_setplan_page_served_and_navs_link_it(tmp_path):
    client, root = _client(tmp_path)
    r = client.get("/setplan.html")
    assert r.status_code == 200
    assert "<title>setplan — librarian</title>" in r.text
    assert 'id="spForm"' in r.text and 'id="spOut"' in r.text
    assert "--raised" in r.text                      # design tokens present
    for page in ("/", "/pulse.html", "/dedupe.html"):
        assert "setplan.html" in client.get(page).text
```

- [ ] **Step 2: Run → FAIL** (404 / missing nav links).

- [ ] **Step 3: Implement**

`web/setplan.html` — structure (self-contained; token block copied byte-identical from pulse.html; app bar copied from pulse.html with a 4th tab marked active):

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>setplan — librarian</title>
<style>
  /* [PASTE the :root token block from web/pulse.html here — BYTE-IDENTICAL] */
  /* [PASTE pulse.html's shared base styles: body, .wrap, .appbar, .tabs, .tab,
     .dot, .kicker, .panel, .field, .btn/.btn-primary, .pill, .banner ok/bad,
     .stat — copy the exact rule blocks so the pages stay visually identical] */
  .slot{display:flex;gap:12px;align-items:baseline;padding:9px 12px;border-bottom:1px solid var(--line)}
  .slot:hover{background:var(--raised)}
  .slot .n{color:var(--faint);min-width:2ch;text-align:right;font-variant-numeric:tabular-nums}
  .slot .clock{color:var(--dim);min-width:5ch;font-variant-numeric:tabular-nums}
  .slot .name{flex:1}
  .slot .badge{font:12px var(--mono);color:var(--dim);background:var(--inset);
    border:1px solid var(--line);border-radius:var(--r-sm);padding:2px 7px}
  .slot .why{display:block;color:var(--faint);font-size:12px;margin-top:2px}
  .slot.anchor .name::before{content:"⭐ ";color:var(--warm)}
  .slot .lock{accent-color:var(--accent)}
  details.alts{margin:2px 0 0 0}
  details.alts summary{color:var(--faint);font-size:12px;cursor:pointer}
  .toolbar{display:flex;gap:10px;align-items:center;margin:14px 0}
</style>
</head>
<body>
<header class="appbar">
  <!-- [copy brand mark from pulse.html] -->
  <nav class="tabs">
    <a class="tab" href="/"><span class="dot" style="background:#7da7ff"></span>Review</a>
    <a class="tab" href="/pulse.html"><span class="dot" style="background:#56d38f"></span>Pulse</a>
    <a class="tab" href="/dedupe.html"><span class="dot" style="background:#e5ab5b"></span>Duplicates</a>
    <a class="tab active" href="/setplan.html"><span class="dot" style="background:#b98ede"></span>Setplan</a>
  </nav>
</header>
<main class="wrap">
  <div class="kicker">PLAN A SET <span class="muted">— harmonic + BPM arc + your own play history</span></div>
  <form id="spForm" class="panel" onsubmit="return runPlan(event)">
    <div class="row">
      <label class="field">minutes <input id="spMin" type="number" value="90" min="10" max="360"></label>
      <label class="field grow">journey <input id="spJourney" placeholder="amapiano:60,afrobeats:40"></label>
      <label class="field">arc
        <select id="spArc"><option>peak</option><option>warmup</option><option>build</option><option>closing</option><option>journey</option></select></label>
      <label class="field">harmonic
        <select id="spHarm"><option>loose</option><option>strict</option><option>off</option></select></label>
      <label class="field">freshness <input id="spFresh" type="range" min="0" max="1" step="0.05" value="0.3"></label>
    </div>
    <div class="row">
      <label class="field grow">must-play <input id="spMust" placeholder="comma-separated queries"></label>
      <label class="field grow">avoid <input id="spAvoid" placeholder="tracks / artists / genres"></label>
      <label class="field grow">opener <input id="spOpener"></label>
      <button class="btn btn-primary" type="submit" id="spGo">Build set</button>
    </div>
  </form>
  <div id="spOut"></div>
</main>
<script>
let PLAN = null;
const $ = id => document.getElementById(id);
const esc = s => (s ?? "").toString().replace(/[&<>"']/g, m =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));

function req() {
  return {
    minutes: +$("spMin").value, journey: $("spJourney").value.trim(),
    arc: $("spArc").value, harmonic: $("spHarm").value, freshness: +$("spFresh").value,
    must: $("spMust").value.split(",").map(s=>s.trim()).filter(Boolean),
    avoid: $("spAvoid").value.split(",").map(s=>s.trim()).filter(Boolean),
    opener: $("spOpener").value.trim() || null,
  };
}
async function runPlan(e) {
  e.preventDefault();
  $("spOut").innerHTML = '<div class="banner">building…</div>';
  const r = await fetch("/api/setplan", {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify(req())});
  if (!r.ok) { $("spOut").innerHTML = `<div class="banner bad">${esc((await r.json()).detail || r.status)}</div>`; return false; }
  PLAN = (await r.json()).plan;
  render();
  return false;
}
function locks() {
  const out = {};
  document.querySelectorAll(".lock:checked").forEach(cb => { out[+cb.dataset.i] = cb.dataset.path; });
  return out;
}
async function reroll(fromSlot) {
  const body = {locks: locks()};
  if (fromSlot != null) body.from_slot = fromSlot;
  const r = await fetch(`/api/setplan/${PLAN.id}/reroll`, {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
  if (r.ok) { PLAN = (await r.json()).plan; render(); }
}
function render() {
  const w = PLAN.unmatched.length
    ? `<div class="banner bad">no match: ${PLAN.unmatched.map(esc).join(" · ")}</div>` : "";
  const rows = PLAN.slots.map(s => {
    const c = s.candidate;
    const key = c.camelot ? c.camelot[0] + c.camelot[1] : "—";
    const bpm = c.bpm ? Math.round(c.bpm) : "—";
    const anchor = s.reason.startsWith("⭐");
    const alts = s.alternates.length
      ? `<details class="alts"><summary>${s.alternates.length} alternates</summary>` +
        s.alternates.map(a =>
          `<div class="why">↳ ${esc(a.candidate.artist)} — ${esc(a.candidate.title)} · ${esc(a.reason)}
             <button class="btn" onclick="swap(${s.index}, '${esc(a.candidate.path)}')">use</button></div>`).join("") +
        `</details>` : "";
    return `<div class="slot${anchor ? " anchor" : ""}">
      <span class="n">${s.index + 1}</span><span class="clock">+${Math.round(s.clock_min)}m</span>
      <span class="name">${esc(c.artist)} — ${esc(c.title)}
        <span class="why">${esc(s.reason)}</span>${alts}</span>
      <span class="badge">${bpm} · ${key}</span>
      <label><input type="checkbox" class="lock" data-i="${s.index}" data-path="${esc(c.path)}"> lock</label>
      <button class="btn" onclick="reroll(${s.index + 1})">reroll ↓</button>
    </div>`;
  }).join("");
  $("spOut").innerHTML = `${w}
    <div class="toolbar">
      <span class="pill">plan ${esc(PLAN.id)}</span>
      <button class="btn" onclick="reroll(null)">Reroll (respect locks)</button>
      <a class="btn" href="/api/setplan/${PLAN.id}/export?fmt=m3u8">m3u8</a>
      <a class="btn" href="/api/setplan/${PLAN.id}/export?fmt=md">markdown</a>
      <a class="btn" href="/api/setplan/${PLAN.id}/export?fmt=xml">rekordbox xml</a>
    </div>
    <div class="panel" style="padding:0">${rows}</div>`;
}
async function swap(i, path) {
  const l = locks(); l[i] = path;
  const r = await fetch(`/api/setplan/${PLAN.id}/reroll`, {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify({locks: l})});
  if (r.ok) { PLAN = (await r.json()).plan; render(); }
}
</script>
</body>
</html>
```

The two `[PASTE …]` markers are **transcription instructions, not TBDs**: copy the exact `:root` block and the shared base-style rules (`body`, `.wrap`, `.appbar`, `.tabs`, `.tab`, `.dot`, `.kicker`, `.panel`, `.field`, `.row`, `.btn`, `.btn-primary`, `.pill`, `.banner`, `.muted`) out of `web/pulse.html` so all four pages share one look. After pasting, verify: `python3 -c "import re,hashlib; a=re.search(r':root \{.*?\n  \}', open('src/librarian/web/pulse.html').read(), re.S).group(); b=re.search(r':root \{.*?\n  \}', open('src/librarian/web/setplan.html').read(), re.S).group(); assert a==b, 'token drift'"`.

Nav additions — in each of `index.html`, `pulse.html`, `dedupe.html`, add inside `<nav class="tabs">` after the Duplicates tab (exact line):

```html
    <a class="tab" href="/setplan.html"><span class="dot" style="background:#b98ede"></span>Setplan</a>
```

- [ ] **Step 4: Run** the page test + full suite → **265 passed**. Manual: controller screenshots the page (headless Chrome) at review.

- [ ] **Step 5: Commit**

```bash
git add src/librarian/web/setplan.html src/librarian/web/index.html src/librarian/web/pulse.html src/librarian/web/dedupe.html tests/test_webapp_setplan.py
git commit -m "feat(setplan): web tab — gig form, lock/reroll/swap, exports (design-system native)"
```

---

## Self-Review

**Spec coverage:** §3.5 beam+anchors+seeded reroll → Task 1; §4 artifact (`runs/setplan-<id>.json`) → Task 2 + wired in Tasks 4/5; §4 rekordbox export → Task 3 (+ the ordered-playlist gap the stock helper can't do); §3.1 pool cache → Task 2; review-recommended CLI flags → Task 4; §4 web/API surface (the 4 endpoints) + lock/reroll/crate-ish alternates → Tasks 5/6. Deferred (explicitly out): `setplan review`/`doctor`, venue-filtered follows, NL→GigSpec (Phase 3).
**Placeholder scan:** the two `[PASTE …]` blocks are precise transcription instructions with source + a byte-equality verification command — no TBDs remain.
**Type consistency:** `build_set(..., beam_width, pinned)` used identically in Tasks 1/4/5; `store.plan_to_dict/save_plan/load_plan_dict/spec_from_dict/load_pool_cached` signatures match between Task 2 (producer) and Tasks 4/5 (consumers); `to_rekordbox_xml(plan, xml_in, xml_out, playlist_name)` matches Tasks 3/4/5; slot dict shape (`index/clock_min/reason/candidate/alternates`) consistent across store, API and page JS.

## Execution Handoff

Two options: **1. Subagent-Driven (recommended)** — fresh subagent per task (Opus/Sonnet per model policy), review gate per task; **2. Inline Execution** — tasks in-session with checkpoints.
