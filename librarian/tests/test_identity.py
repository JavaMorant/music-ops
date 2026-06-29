"""Identity stage: cache, graceful degrade, and cached AcoustID lookup."""

from __future__ import annotations

from pathlib import Path

from librarian import identity as I
from librarian.identity import Identity, IdentityCache, identify


def test_cache_roundtrip(tmp_path):
    c = IdentityCache(tmp_path / "id.json")
    ident = Identity("Drake", "Headlines", "acoustid", 0.9, "mbid-1")
    c.put("FPSTRING", ident)
    c.save()
    again = IdentityCache(tmp_path / "id.json")
    assert again.get("FPSTRING") == ident
    assert again.get("missing") is None


def test_degrade_without_key(tmp_path):
    (tmp_path / "Drake - Headlines.mp3").write_bytes(b"\x00")
    out = identify([tmp_path / "Drake - Headlines.mp3"], key=None)
    ident = out[tmp_path / "Drake - Headlines.mp3"]
    assert ident.source in ("tags", "filename")
    assert ident.artist == "Drake" and ident.title == "Headlines"


def test_lookup_used_then_cached(tmp_path, monkeypatch):
    f = tmp_path / "junkname.mp3"
    f.write_bytes(b"\x00")
    monkeypatch.setattr(I, "fingerprint", lambda p: ("FP", 200))
    calls = {"n": 0}

    def fake_lookup(fp, dur, key):
        calls["n"] += 1
        return Identity("Drake", "Headlines", "acoustid", 0.95, "mbid-x")

    cache = IdentityCache(tmp_path / "id.json")
    out = identify([f], key="k", cache=cache, lookup=fake_lookup, throttle=0)
    assert out[f].source == "acoustid" and out[f].title == "Headlines"
    assert calls["n"] == 1

    # second run: fingerprint hits cache, lookup not called again
    out2 = identify([f], key="k", cache=cache, lookup=fake_lookup, throttle=0)
    assert out2[f] == out[f]
    assert calls["n"] == 1


def test_lookup_failure_degrades(tmp_path, monkeypatch):
    f = tmp_path / "Artist - Song.mp3"
    f.write_bytes(b"\x00")
    monkeypatch.setattr(I, "fingerprint", lambda p: ("FP", 200))
    out = identify([f], key="k", cache=IdentityCache(tmp_path / "id.json"),
                   lookup=lambda *a: None, throttle=0)
    assert out[f].source in ("tags", "filename")  # fell back, didn't raise
