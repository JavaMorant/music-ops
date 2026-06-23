"""Stem-separation wrapper tests — cache keying, the cache-hit fast path, the
Demucs output move, and that a missing Demucs fails loudly. The real Demucs run
is heavy/slow, so subprocess is mocked here (a smoke test of an actual run lives
outside the suite)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from releases import stems


def test_has_demucs_returns_bool():
    assert isinstance(stems.has_demucs(), bool)


def test_cache_key_changes_when_file_changes(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x" * 100)
    k1 = stems.cache_key(f)
    f.write_bytes(b"x" * 300)  # size (and mtime) change
    assert stems.cache_key(f) != k1


def test_separate_requires_demucs(tmp_path, monkeypatch):
    monkeypatch.setattr(stems, "has_demucs", lambda: False)
    with pytest.raises(stems.StemError):
        stems.separate(tmp_path / "x.wav", tmp_path / "cache")


def test_separate_uses_cache_without_rerunning(tmp_path, monkeypatch):
    src = tmp_path / "beat.wav"
    src.write_bytes(b"x" * 100)
    cache = tmp_path / "cache"
    dest = cache / stems.cache_key(src)
    dest.mkdir(parents=True)
    for s in stems.STEMS:  # pre-seed the cache
        (dest / f"{s}.mp3").write_bytes(b"audio")
    monkeypatch.setattr(stems, "has_demucs", lambda: True)
    ran = {"n": 0}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.__setitem__("n", ran["n"] + 1))
    out = stems.separate(src, cache)
    assert ran["n"] == 0  # cache hit → Demucs not invoked
    assert set(out) == set(stems.STEMS) and all(p.is_file() for p in out.values())


def test_separate_moves_demucs_output_into_cache(tmp_path, monkeypatch):
    src = tmp_path / "beat.wav"
    src.write_bytes(b"x" * 100)
    cache = tmp_path / "cache"
    monkeypatch.setattr(stems, "has_demucs", lambda: True)

    def fake_run(args, **kw):
        work = Path(args[args.index("-o") + 1])
        td = work / stems.MODEL / "beat"
        td.mkdir(parents=True)
        for s in stems.STEMS:
            (td / f"{s}.mp3").write_bytes(b"stem")
        return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = stems.separate(src, cache)
    key_dir = cache / stems.cache_key(src)
    assert set(out) == set(stems.STEMS)
    assert all(p.is_file() and p.parent == key_dir for p in out.values())
    assert not (key_dir / "_work").exists()  # scratch cleaned up
    assert src.read_bytes() == b"x" * 100  # source untouched
