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
