"""Classify v2: tiering (low-confidence re-run), caching, key stability."""

from __future__ import annotations

from librarian import classify_v2 as C
from librarian.classify_v2 import ClassifyCache, ClassifyResult, classify, track_key


def _track(key, **kw):
    return {"key": key, "artist": kw.get("artist", ""), "title": kw.get("title", key),
            "filename": kw.get("filename", key), "genre": kw.get("genre", "")}


def test_track_key_prefers_mbid():
    assert track_key("Drake", "Headlines", "REC1") == "mbid:REC1"
    assert track_key("Drake", "Headlines") == track_key("DRAKE", "headlines!")


def test_low_confidence_rerun_wins():
    calls = []

    def fake_call(batch, model, effort):
        calls.append((model, effort, len(batch)))
        out = []
        for i, t in enumerate(batch):
            if effort == "low":
                out.append({"index": i, "genre": "Pop", "confidence": "low", "note": "cheap"})
            else:  # strong pass
                out.append({"index": i, "genre": "US Rap", "confidence": "high", "note": "strong"})
        return out

    res = classify([_track("k1")], call=fake_call,
                   cheap=("cheap-m", "low"), strong=("strong-m", "high"))
    assert res["k1"] == ClassifyResult("US Rap", "high", "strong")  # pass-2 won
    # cheap pass ran on all, strong pass ran only on the 1 low-confidence track
    assert ("cheap-m", "low", 1) in calls
    assert ("strong-m", "high", 1) in calls


def test_high_confidence_not_rerun():
    calls = []

    def fake_call(batch, model, effort):
        calls.append(effort)
        return [{"index": i, "genre": "Pop", "confidence": "high", "note": ""}
                for i, _ in enumerate(batch)]

    classify([_track("k1")], call=fake_call, cheap=("c", "low"), strong=("s", "high"))
    assert "low" in calls and "high" not in calls  # no strong pass needed


def test_cache_skips_classified(tmp_path):
    cache = ClassifyCache(tmp_path / "c.json")
    cache.put("k1", ClassifyResult("Garage", "high"))
    cache.save()

    def boom(*a):
        raise AssertionError("should not call the model for a cached track")

    res = classify([_track("k1")], cache=ClassifyCache(tmp_path / "c.json"), call=boom)
    assert res["k1"].genre == "Garage"
