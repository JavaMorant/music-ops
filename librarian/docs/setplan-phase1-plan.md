# Setplan Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a local, read-only `librarian setplan` command that builds an ordered set from the library, sequenced by harmonic + BPM + energy-arc fit and the DJ's own play-history transitions, exporting m3u8 + markdown.

**Architecture:** A pure engine — `camelot.py` (key math) + a `setplan/` package (`spec`, `pool`, `score`, `search`, `export`) — plus a Typer command. Play history enters the pure code as **plain session data** (`list[list[str]]`, each a tracklist in play order), so every module is fixture-testable with no USB/DB. The CLI is the only place that touches real history (via `pulse_usb`), and it degrades gracefully to empty history.

**Tech Stack:** Python 3.12+, Typer, pytest. Reuses `metadata.read_meta`, `paths.audio_files`/`default_runs_dir`, and the `pulse_usb` session reader. No new third-party deps.

## Global Constraints

- Python **3.12+**; run everything with the tool's venv: `cd librarian && .venv/bin/python -m pytest -q`.
- **Read-only feature:** setplan NEVER moves, renames, or retags files. No use of the plan/apply/undo engine. Only writes are explicit export files + `runs/setplan-<id>.json`.
- **Never guess key/BPM:** unknown key or BPM scores **neutral 0.5**, never 0, never inferred. (Matches the tool's safety invariants.)
- Join key between library and history is `re.sub(r"[^a-z0-9]", "", s.lower())` — identical to `pulse_usb._norm`. Do not diverge.
- New code lives under `src/librarian/setplan/` and `src/librarian/camelot.py`; tests under `tests/`.
- Follow existing style: `from __future__ import annotations`, dataclasses, small focused modules, Typer command with clear `--help`.
- Commit after each task with a `feat:` message.

## File Structure

- `src/librarian/camelot.py` — key-string parsing (Camelot/Open-Key/musical) → `(1-12, "A"/"B")`; `harmonic()` compatibility score. Pure, no I/O.
- `src/librarian/setplan/__init__.py` — package marker + public re-exports.
- `src/librarian/setplan/spec.py` — `GigSpec` dataclass, energy-arc curves, genre-block helpers. Pure.
- `src/librarian/setplan/pool.py` — `Candidate` dataclass, `norm()`, `history_stats()`, `build_pool()`. The only I/O (reads tags).
- `src/librarian/setplan/score.py` — pure `score_slot()` + component functions.
- `src/librarian/setplan/search.py` — `Slot`/`SetPlan`, `build_set()` (greedy + simple anchors). Pure.
- `src/librarian/setplan/export.py` — `to_m3u8()`, `to_markdown()`.
- `src/librarian/cli.py` — add the `setplan` command (history adapter + wiring).
- Tests: `tests/test_camelot.py`, `tests/test_setplan_spec.py`, `tests/test_setplan_pool.py`, `tests/test_setplan_score.py`, `tests/test_setplan_search.py`, `tests/test_setplan_export.py`.

---

### Task 1: `camelot.py` — key parsing + harmonic score

**Files:**
- Create: `src/librarian/camelot.py`
- Test: `tests/test_camelot.py`

**Interfaces:**
- Produces: `Camelot = tuple[int, str]`; `parse_key(s: str | None) -> Camelot | None`; `harmonic(a: Camelot | None, b: Camelot | None, *, rising: bool = False, mode: str = "loose") -> float`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_camelot.py
from __future__ import annotations
from librarian import camelot as C


def test_parse_camelot_openkey_musical():
    assert C.parse_key("8A") == (8, "A")
    assert C.parse_key("12b") == (12, "B")
    assert C.parse_key("Am") == (8, "A")           # A minor
    assert C.parse_key("C") == (8, "B")            # C major
    assert C.parse_key("F#m") == (11, "A")
    assert C.parse_key("Ebmaj") == (5, "B")
    assert C.parse_key("1m") == (8, "A")           # Open Key -> Camelot (+7 offset)
    assert C.parse_key("1d") == (8, "B")
    assert C.parse_key(None) is None
    assert C.parse_key("garbage") is None


def test_harmonic_table():
    assert C.harmonic((8, "A"), (8, "A")) == 1.0            # same key
    assert C.harmonic((8, "A"), (9, "A")) == 0.90           # +1 wheel
    assert C.harmonic((8, "A"), (8, "B")) == 0.85           # relative maj/min
    assert C.harmonic((8, "A"), (10, "A"), rising=True) == 0.55   # +2 energy, rising
    assert C.harmonic((8, "A"), (10, "A"), rising=False) == 0.10  # +2 not allowed flat
    assert C.harmonic((8, "A"), (9, "B"), mode="loose") == 0.40   # diagonal, loose
    assert C.harmonic((8, "A"), (2, "A")) == 0.10           # far
    assert C.harmonic(None, (8, "A")) == 0.5                # unknown -> neutral
    assert C.harmonic((8, "A"), None) == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_camelot.py -q`
Expected: FAIL (module `librarian.camelot` not found).

- [ ] **Step 3: Write minimal implementation**

```python
# src/librarian/camelot.py
"""Camelot-wheel key parsing + harmonic compatibility. Pure, no I/O.

Camelot codes are 1-12 with letter A (minor) or B (major). We parse the three
spellings rekordbox TKEY throws at us — Camelot ("8A"), Open Key ("1m"/"1d"),
and musical ("Am", "C", "F#m", "Ebmaj") — and score how well two keys mix.
"""

from __future__ import annotations

import re

Camelot = tuple[int, str]

# Musical spelling (root lowercased, no maj/min word; minor keeps trailing "m") -> Camelot.
_MUSICAL: dict[str, Camelot] = {
    # majors (B)
    "c": (8, "B"), "g": (9, "B"), "d": (10, "B"), "a": (11, "B"), "e": (12, "B"),
    "b": (1, "B"), "f#": (2, "B"), "gb": (2, "B"), "db": (3, "B"), "c#": (3, "B"),
    "ab": (4, "B"), "g#": (4, "B"), "eb": (5, "B"), "d#": (5, "B"), "bb": (6, "B"),
    "a#": (6, "B"), "f": (7, "B"),
    # minors (A)
    "am": (8, "A"), "em": (9, "A"), "bm": (10, "A"), "f#m": (11, "A"), "gbm": (11, "A"),
    "c#m": (12, "A"), "dbm": (12, "A"), "g#m": (1, "A"), "abm": (1, "A"), "d#m": (2, "A"),
    "ebm": (2, "A"), "a#m": (3, "A"), "bbm": (3, "A"), "fm": (4, "A"), "cm": (5, "A"),
    "gm": (6, "A"), "dm": (7, "A"),
}


def parse_key(s: str | None) -> Camelot | None:
    if not s:
        return None
    t = s.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})([ab])", t)          # Camelot: 8a / 12B
    if m:
        n = int(m.group(1))
        return (n, m.group(2).upper()) if 1 <= n <= 12 else None
    m = re.fullmatch(r"(\d{1,2})([dm])", t)          # Open Key: 1d (major) / 1m (minor)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 12:
            return None
        return ((n + 6) % 12 + 1, "B" if m.group(2) == "d" else "A")
    t = t.replace("major", "").replace("maj", "")    # musical: normalise words to "" / "m"
    t = t.replace("minor", "m").replace("min", "m")
    return _MUSICAL.get(t)


def _step(x: int, y: int) -> int:
    """Distance on the 1-12 clock face."""
    d = abs(x - y) % 12
    return min(d, 12 - d)


def harmonic(a: Camelot | None, b: Camelot | None, *, rising: bool = False,
             mode: str = "loose") -> float:
    if a is None or b is None:
        return 0.5                                   # never punish missing data
    if a == b:
        return 1.0
    (na, la), (nb, lb) = a, b
    if la == lb:
        s = _step(na, nb)
        if s == 1:
            return 0.90
        if s == 2 and rising:
            return 0.55                              # energy boost, rising arc only
        return 0.10
    if na == nb:
        return 0.85                                  # relative major/minor
    if mode == "loose" and _step(na, nb) == 1:
        return 0.40                                  # diagonal energy raise
    return 0.10
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_camelot.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/librarian/camelot.py tests/test_camelot.py
git commit -m "feat(setplan): camelot key parsing + harmonic score"
```

---

### Task 2: `setplan/spec.py` — GigSpec + energy arcs

**Files:**
- Create: `src/librarian/setplan/__init__.py`, `src/librarian/setplan/spec.py`
- Test: `tests/test_setplan_spec.py`

**Interfaces:**
- Produces: `GenreBlock = tuple[str, float]`; `GigSpec` dataclass with `.n_slots() -> int`; `target_percentile(arc: str, t: float) -> float`; `genre_block_at(spec: GigSpec, t: float) -> str | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setplan_spec.py
from __future__ import annotations
from librarian.setplan.spec import GigSpec, target_percentile, genre_block_at


def test_n_slots_and_defaults():
    s = GigSpec(minutes=90)
    assert s.n_slots() == 30              # 90/60 * 20
    assert s.arc == "peak" and s.freshness == 0.3


def test_arc_curve_monotonic_endpoints():
    assert target_percentile("warmup", 0.0) < target_percentile("warmup", 1.0)
    assert target_percentile("closing", 0.0) > target_percentile("closing", 1.0)
    mid = target_percentile("peak", 0.5)
    assert 0.8 <= mid <= 0.95             # plateau in the peak


def test_genre_block_at_splits_by_fraction():
    s = GigSpec(minutes=60, journey=[("amapiano", 0.6), ("afrobeats", 0.4)])
    assert genre_block_at(s, 0.1) == "amapiano"
    assert genre_block_at(s, 0.8) == "afrobeats"
    assert genre_block_at(GigSpec(minutes=60), 0.5) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_setplan_spec.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

```python
# src/librarian/setplan/__init__.py
"""The set-prep (setplan) engine: pure metadata-in, ordered-plan-out."""
```

```python
# src/librarian/setplan/spec.py
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

    def n_slots(self) -> int:
        return max(1, round(self.minutes / 60 * self.tracks_per_hour))


def target_percentile(arc: str, t: float) -> float:
    anchors = _ARCS.get(arc, _ARCS["peak"])
    for (t0, p0), (t1, p1) in zip(anchors, anchors[1:]):
        if t0 <= t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return p0 + f * (p1 - p0)
    return anchors[-1][1]


def genre_block_at(spec: GigSpec, t: float) -> str | None:
    if not spec.journey:
        return None
    acc = 0.0
    for genre, frac in spec.journey:
        acc += frac
        if t <= acc + 1e-9:
            return genre
    return spec.journey[-1][0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_setplan_spec.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/librarian/setplan/__init__.py src/librarian/setplan/spec.py tests/test_setplan_spec.py
git commit -m "feat(setplan): GigSpec + energy-arc curves"
```

---

### Task 3: `setplan/pool.py` — candidates + history stats

**Files:**
- Create: `src/librarian/setplan/pool.py`
- Test: `tests/test_setplan_pool.py`

**Interfaces:**
- Consumes: `camelot.parse_key`; `metadata.read_meta`; `paths.audio_files`
- Produces: `norm(s: str | None) -> str`; `Candidate` dataclass (`path, artist, title, genre, bpm: float|None, camelot, length_s, norm_key, low_bitrate, plays, positions`; `.position_prior` property); `history_stats(sessions: list[list[str]]) -> tuple[dict, dict, dict]` returning `(plays, positions, follows)`; `build_pool(root: Path, sessions: list[list[str]] | None = None) -> tuple[list[Candidate], dict]` returning `(candidates, follows)`; `DEFAULT_ADJACENCY: dict[str, set[str]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setplan_pool.py
from __future__ import annotations
from pathlib import Path
from librarian.setplan.pool import norm, history_stats, build_pool, Candidate


def test_norm_is_the_join_key():
    assert norm("Tyler ICU - Mnike!") == "tylericumnike"
    assert norm(None) == ""


def test_history_stats_plays_positions_follows():
    sessions = [["A - x", "B - y", "C - z"], ["A - x", "B - y"]]
    plays, positions, follows = history_stats(sessions)
    assert plays[norm("A - x")] == 2
    assert plays[norm("C - z")] == 1
    assert follows[norm("A - x")][norm("B - y")] == 2   # A->B twice
    assert positions[norm("A - x")] == [0.0, 0.0]       # always opened


def test_build_pool_reads_tags_and_joins_history(tmp_path, monkeypatch):
    from librarian.setplan import pool as P
    # two fake files; patch read_meta so we don't need real audio
    (tmp_path / "A - x.mp3").write_bytes(b"0")
    (tmp_path / "B - y.mp3").write_bytes(b"0")
    from librarian.metadata import TrackMeta
    metas = {
        "A - x.mp3": TrackMeta(path=tmp_path / "A - x.mp3", artist="A", title="x", genre="Amapiano", bpm="112", key="8A", length_s=200.0),
        "B - y.mp3": TrackMeta(path=tmp_path / "B - y.mp3", artist="B", title="y", genre="Afrobeats", bpm=None, key=None),
    }
    monkeypatch.setattr(P, "read_meta", lambda p: metas[p.name])
    cands, follows = build_pool(tmp_path, sessions=[["A - x", "B - y"]])
    by = {c.title: c for c in cands}
    assert by["x"].bpm == 112.0 and by["x"].camelot == (8, "A")
    assert by["x"].plays == 1 and by["x"].position_prior == 0.0
    assert by["y"].bpm is None and by["y"].camelot is None      # unknown, not guessed
    assert follows[norm("A - x")][norm("B - y")] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_setplan_pool.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_setplan_pool.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/librarian/setplan/pool.py tests/test_setplan_pool.py
git commit -m "feat(setplan): candidate pool + play-history stats"
```

---

### Task 4: `setplan/score.py` — slot scoring

**Files:**
- Create: `src/librarian/setplan/score.py`
- Test: `tests/test_setplan_score.py`

**Interfaces:**
- Consumes: `camelot.harmonic`; `Candidate` (from pool)
- Produces: `score_slot(cand: Candidate, prev: Candidate | None, ctx: dict) -> tuple[float, str]` where `ctx` has keys `rising: bool, target_bpm: float|None, block: str|None, adjacency: dict, max_plays: int, freshness: float, follows: dict, recent_artists: list[str], harmonic_mode: str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setplan_score.py
from __future__ import annotations
from librarian.setplan.pool import Candidate, norm
from librarian.setplan.score import score_slot


def _c(artist, title, bpm=120.0, key=(8, "A"), genre="amapiano", plays=0):
    return Candidate(path=None, artist=artist, title=title, genre=genre, bpm=bpm, camelot=key,
                     length_s=180.0, norm_key=norm(f"{artist} {title}"), low_bitrate=False, plays=plays)


def _ctx(**kw):
    base = dict(rising=True, target_bpm=120.0, block="amapiano", adjacency={}, max_plays=10,
                freshness=0.3, follows={}, recent_artists=[], harmonic_mode="loose")
    base.update(kw)
    return base


def test_unknown_key_and_bpm_are_neutral_not_zero():
    prev = _c("A", "x")
    cand = _c("B", "y", bpm=None, key=None)
    s, _ = score_slot(cand, prev, _ctx())
    # neutral harmonic(0.5) + neutral bpm(0.5) still yields a usable score
    assert s > 0.2


def test_follows_boost_is_additive_not_a_penalty_when_absent():
    prev = _c("A", "x")
    perfect = _c("B", "y", key=(8, "A"))            # same key, no history
    s_perfect, _ = score_slot(perfect, prev, _ctx(follows={}))
    with_follows = _ctx(follows={prev.norm_key: {perfect.norm_key: 4}})
    s_hist, _ = score_slot(perfect, prev, with_follows)
    assert s_hist > s_perfect                       # history adds, never subtracts
    assert s_perfect > 0.4                          # a no-history perfect pick is still strong


def test_artist_repeat_penalised():
    prev = _c("A", "x")
    cand = _c("Repeat", "y")
    clean, _ = score_slot(cand, prev, _ctx(recent_artists=[]))
    repeat, _ = score_slot(cand, prev, _ctx(recent_artists=["repeat"]))
    assert clean - repeat >= 0.29
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_setplan_score.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_setplan_score.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/librarian/setplan/score.py tests/test_setplan_score.py
git commit -m "feat(setplan): pure slot scoring + reasons"
```

---

### Task 5: `setplan/search.py` — greedy ordering + anchors

**Files:**
- Create: `src/librarian/setplan/search.py`
- Test: `tests/test_setplan_search.py`

**Interfaces:**
- Consumes: `spec.target_percentile`, `spec.genre_block_at`, `GigSpec`; `score.score_slot`; `pool.DEFAULT_ADJACENCY`, `pool.norm`, `Candidate`; `camelot.harmonic`
- Produces: `Slot` (`index, candidate, reason, alternates: list[dict], clock_min`); `SetPlan` (`spec, slots: list[Slot]`); `build_set(pool: list[Candidate], spec: GigSpec, follows: dict | None = None, adjacency: dict | None = None) -> SetPlan`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setplan_search.py
from __future__ import annotations
from librarian.setplan.pool import Candidate, norm
from librarian.setplan.spec import GigSpec
from librarian.setplan.search import build_set


def _c(artist, title, bpm=112.0, key=(8, "A"), genre="amapiano", plays=1, length=180.0):
    return Candidate(path=f"/lib/{title}.mp3", artist=artist, title=title, genre=genre, bpm=bpm,
                     camelot=key, length_s=length, norm_key=norm(f"{artist} {title}"),
                     low_bitrate=False, plays=plays)


def _pool(n):
    keys = [(8, "A"), (9, "A"), (10, "A"), (8, "B")]
    return [_c(f"Artist{i}", f"Track{i}", bpm=108 + i, key=keys[i % 4]) for i in range(n)]


def test_build_set_has_requested_length_and_no_immediate_repeats():
    spec = GigSpec(minutes=30, tracks_per_hour=20, journey=[("amapiano", 1.0)])
    plan = build_set(_pool(20), spec)
    assert len(plan.slots) == spec.n_slots() == 10
    picked = [s.candidate.path for s in plan.slots]
    assert len(set(picked)) == len(picked)                     # no track used twice
    # no artist twice within 6 slots
    for i in range(len(plan.slots)):
        window = [plan.slots[j].candidate.artist for j in range(max(0, i - 5), i)]
        assert plan.slots[i].candidate.artist not in window


def test_must_play_appears_as_anchor():
    pool = _pool(20) + [_c("SpecialArtist", "Mnike", key=(8, "A"))]
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)], must_play=["Mnike"])
    plan = build_set(pool, spec)
    titles = [s.candidate.title for s in plan.slots]
    assert "Mnike" in titles
    anchor = next(s for s in plan.slots if s.candidate.title == "Mnike")
    assert "anchor" in anchor.reason.lower()


def test_opener_is_forced_first():
    pool = _pool(20) + [_c("Opener", "FirstOne", key=(8, "A"))]
    spec = GigSpec(minutes=30, journey=[("amapiano", 1.0)], opener="FirstOne")
    plan = build_set(pool, spec)
    assert plan.slots[0].candidate.title == "FirstOne"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_setplan_search.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

```python
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
        slot = min(n - 1, int(n * 0.5) + j)          # drop into the peak plateau
        while slot in anchors and slot < n - 1:
            slot += 1
        anchors[slot] = m

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
            if spec.harmonic == "strict" and prev:
                avail = [c for c in avail
                         if c.camelot is None
                         or camelot.harmonic(prev.camelot, c.camelot, rising=rising) >= 0.85]
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_setplan_search.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/librarian/setplan/search.py tests/test_setplan_search.py
git commit -m "feat(setplan): greedy set ordering with anchors"
```

---

### Task 6: `setplan/export.py` — m3u8 + markdown

**Files:**
- Create: `src/librarian/setplan/export.py`
- Test: `tests/test_setplan_export.py`

**Interfaces:**
- Consumes: `SetPlan` (from search)
- Produces: `to_m3u8(plan: SetPlan, path: Path) -> None`; `to_markdown(plan: SetPlan, path: Path) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setplan_export.py
from __future__ import annotations
from librarian.setplan.pool import Candidate, norm
from librarian.setplan.spec import GigSpec
from librarian.setplan.search import Slot, SetPlan
from librarian.setplan.export import to_m3u8, to_markdown


def _plan():
    c = Candidate(path="/lib/Mnike.mp3", artist="Tyler ICU", title="Mnike", genre="amapiano",
                  bpm=112.0, camelot=(8, "A"), length_s=200.0, norm_key=norm("Tyler ICU Mnike"),
                  low_bitrate=False, plays=3)
    slot = Slot(index=0, candidate=c, reason="⭐ anchor (must-play)", clock_min=0.0)
    return SetPlan(spec=GigSpec(minutes=30), slots=[slot])


def test_m3u8_lists_track_paths(tmp_path):
    out = tmp_path / "set.m3u8"
    to_m3u8(_plan(), out)
    text = out.read_text()
    assert text.startswith("#EXTM3U")
    assert "/lib/Mnike.mp3" in text
    assert "Tyler ICU - Mnike" in text


def test_markdown_has_reasons_and_badges(tmp_path):
    out = tmp_path / "set.md"
    to_markdown(_plan(), out)
    text = out.read_text()
    assert "Mnike" in text and "8A" in text and "112" in text
    assert "anchor" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_setplan_export.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

```python
# src/librarian/setplan/export.py
from __future__ import annotations

from pathlib import Path


def to_m3u8(plan, path: Path) -> None:
    lines = ["#EXTM3U"]
    for s in plan.slots:
        c = s.candidate
        secs = int(c.length_s or -1)
        lines.append(f"#EXTINF:{secs},{c.artist} - {c.title}".rstrip())
        lines.append(str(c.path))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def to_markdown(plan, path: Path) -> None:
    sp = plan.spec
    journey = " → ".join(f"{g} {int(f * 100)}%" for g, f in sp.journey) or "any"
    out = [f"# Set plan — {sp.minutes} min · {sp.arc} · {journey}", ""]
    for s in plan.slots:
        c = s.candidate
        key = f"{c.camelot[0]}{c.camelot[1]}" if c.camelot else "—"
        bpm = f"{int(c.bpm)}" if c.bpm else "—"
        clock = f"+{int(s.clock_min)}m"
        out.append(f"{s.index + 1:>2}. [{clock}] **{c.artist} — {c.title}**  ·  {bpm} BPM · {key}")
        out.append(f"    _{s.reason}_")
        for alt in s.alternates:
            ac = alt["candidate"]
            out.append(f"    alt: {ac.artist} — {ac.title} ({alt['reason']})")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_setplan_export.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/librarian/setplan/export.py tests/test_setplan_export.py
git commit -m "feat(setplan): m3u8 + markdown export"
```

---

### Task 7: `setplan` CLI command + history adapter

**Files:**
- Modify: `src/librarian/cli.py` (add the `setplan` command near the other commands; import at top of the function like the `organise` command does its local imports)

**Interfaces:**
- Consumes: `paths.audio_files`, `paths.default_runs_dir`; `setplan.pool.build_pool`; `setplan.search.build_set`; `setplan.spec.GigSpec`; `setplan.export.to_m3u8/to_markdown`; `pulse_usb.find_usbs`/`read_stick` (best-effort history)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setplan_search.py  (append — exercises the parse helper the CLI uses)
from librarian.cli import _parse_journey


def test_parse_journey_string():
    assert _parse_journey("amapiano:60,afrobeats:40") == [("amapiano", 0.6), ("afrobeats", 0.4)]
    assert _parse_journey("") == []
    assert _parse_journey("house") == [("house", 1.0)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_setplan_search.py::test_parse_journey_string -q`
Expected: FAIL (`_parse_journey` not defined).

- [ ] **Step 3: Write minimal implementation**

Add this helper near the top of `cli.py` (module level, after the imports):

```python
def _parse_journey(s: str) -> list[tuple[str, float]]:
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
```

Add the command (place it after the `organise` command; use function-local imports as `organise` does):

```python
@app.command()
def setplan(
    library_root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Library root")],
    minutes: Annotated[int, typer.Option("--minutes", help="Set length in minutes")] = 90,
    journey: Annotated[str, typer.Option("--journey", help="Genre blocks, e.g. 'amapiano:60,afrobeats:40'")] = "",
    arc: Annotated[str, typer.Option("--arc", help="Energy arc: warmup|build|peak|closing|journey")] = "peak",
    freshness: Annotated[float, typer.Option("--freshness", help="0=proven bangers … 1=surface the unplayed")] = 0.3,
    harmonic: Annotated[str, typer.Option("--harmonic", help="strict|loose|off")] = "loose",
    must: Annotated[list[str], typer.Option("--must", help="Must-play track query (repeatable)")] = [],
    avoid: Annotated[list[str], typer.Option("--avoid", help="Track/artist/genre to exclude (repeatable)")] = [],
    opener: Annotated[Optional[str], typer.Option("--opener", help="Force this track first")] = None,
    out_dir: Annotated[Path, typer.Option("--out", help="Where to write the plan + exports")] = Path("setplan-run"),
) -> None:
    """Build an ordered set from the library — harmonic + BPM + energy arc + your own play history.

    Read-only: reads tags + rekordbox/USB play history, writes a plan and exports. Moves nothing.
    """
    from .setplan.spec import GigSpec
    from .setplan.pool import build_pool
    from .setplan.search import build_set
    from .setplan.export import to_m3u8, to_markdown

    root = library_root.absolute()
    # Best-effort history: read every mounted CDJ stick; degrade to none if unavailable.
    sessions: list[list[str]] = []
    try:
        from . import pulse_usb
        for vol in pulse_usb.find_usbs():
            sess, _meta, _fmts = pulse_usb.read_stick(vol)
            sessions.extend(s["tracks"] for s in sess if s.get("tracks"))
    except Exception:
        pass  # history is optional; the engine works without it

    spec = GigSpec(minutes=minutes, journey=_parse_journey(journey), arc=arc,
                   freshness=freshness, harmonic=harmonic, must_play=list(must),
                   avoid=list(avoid), opener=opener)
    typer.echo(f"Prepping {minutes}min {arc} set from {root} · history: {len(sessions)} past set(s)")
    cands, follows = build_pool(root, sessions=sessions)
    plan = build_set(cands, spec, follows=follows)
    if not plan.slots:
        typer.secho("No candidates matched — loosen the journey/BPM filters.", fg="yellow")
        raise typer.Exit(1)

    for s in plan.slots:
        c = s.candidate
        key = f"{c.camelot[0]}{c.camelot[1]}" if c.camelot else "--"
        bpm = f"{int(c.bpm)}" if c.bpm else "--"
        typer.echo(f"{s.index + 1:>2} +{int(s.clock_min):>3}m  {c.artist} - {c.title}  [{bpm} {key}]")
        typer.echo(f"      {s.reason}")

    out_dir.mkdir(parents=True, exist_ok=True)
    to_m3u8(plan, out_dir / "set.m3u8")
    to_markdown(plan, out_dir / "set.md")
    typer.echo(f"\nExports → {out_dir}/set.m3u8, {out_dir}/set.md  (nothing in the library was changed)")
```

- [ ] **Step 4: Run tests to verify pass + no regressions**

Run: `.venv/bin/python -m pytest tests/test_setplan_search.py -q && .venv/bin/python -m pytest -q`
Expected: setplan tests PASS; full suite still green (existing 225 + new tests).

- [ ] **Step 5: Manual smoke (real, read-only)**

Run against a throwaway or the real library (read-only — it moves nothing):
```bash
.venv/bin/librarian setplan ~/DJ/library --minutes 60 --journey "amapiano:60,afrobeats:40" --arc peak --freshness 0.3
```
Expected: a printed, ordered ~20-track setlist with BPM/Camelot badges and reasons; `setplan-run/set.m3u8` + `set.md` written; no library changes.

- [ ] **Step 6: Commit**

```bash
git add src/librarian/cli.py tests/test_setplan_search.py
git commit -m "feat(setplan): CLI command + best-effort play-history adapter"
```

---

## Self-Review

**Spec coverage** (spec §→ task):
- §1 reuse map → Tasks 3/7 (pool reads metadata/audio_files; CLI reads pulse_usb). ✅
- §2 GigSpec + arcs → Task 2. ✅
- §3.1 pool → Task 3; §3.2 camelot → Task 1; §3.3 BPM + §3.4 slot score → Task 4; §3.5 greedy ordering + anchors → Task 5. ✅ (Beam search explicitly deferred to Phase 2 — greedy ships here.)
- §4 output/reasons/alternates → Tasks 5 (reasons/alternates) + 6 (export). rekordbox XML export deferred to Phase 2 (m3u8/markdown ship now). ✅
- §5 missing key/BPM degradation → neutral scores in Tasks 1 & 4; `strict` prune in Task 5. `setplan doctor` deferred (not blocking). ✅
- §6 read-only safety → no engine use anywhere; only export writes. ✅
- §7 testing → one test file per module, all behaviors covered. ✅

**Placeholder scan:** no TBD/TODO; every code step is complete runnable code. ✅

**Type consistency:** `Candidate` fields identical across Tasks 3–6; `ctx` dict keys defined in Task 4 and produced identically in Task 5; `score_slot -> (float, str)` consumed as `(score, reason, cand)` triples via `+ (c,)` in Task 5; `SetPlan.slots: list[Slot]` consumed by Task 6. `follows` shape (`dict[str, dict|Counter]`) consistent between `history_stats` (Task 3), `_follows_boost` (Task 4), and `build_set` (Task 5). ✅

**Deferred to Phase 2/3 (not in this plan, per scope):** beam search + segment routing, web tab + API, rekordbox XML export, `setplan review` (plan-vs-played), `setplan doctor`, venue-filtered follows.

---

## Execution Handoff

Plan complete. Two execution options:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks.
2. **Inline Execution** — tasks run in this session with checkpoints.
