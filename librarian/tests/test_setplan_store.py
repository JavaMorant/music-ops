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
