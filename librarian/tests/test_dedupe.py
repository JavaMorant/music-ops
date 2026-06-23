"""Metadata-dedupe: grouping buckets, plan safety, and the reversible apply."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import make_library
from librarian import dedupe
from librarian.webapp.app import create_app
from librarian.webapp.state import AppConfig


def _make(root: Path) -> None:
    make_library(root, [
        "Big Hit (Official Video).mp3",      # \
        "Big Hit (2).mp3",                    #  } same song, 3 source copies -> auto
        "Big Hit.mp3",                        # /
        "Big Hit (Official Video)-bass.mp3",  # a STEM — must be left out of the group
        "Jam Session (Remix) (Official Audio).mp3",  # \ duplicate that carries a
        "Jam Session (Remix).mp3",                   # / version marker -> mixed
        "Lonely Track.mp3",                   # singleton — not a duplicate
    ])


def test_buckets(tmp_path: Path):
    root = tmp_path / "lib"
    _make(root)
    res = dedupe.analyze(root, pl_map={})

    assert res["stats"]["auto_groups"] == 1
    auto = res["auto"][0]
    assert auto["key"] == "big hit"
    rels = {f["rel"] for f in auto["files"]}
    assert "Big Hit (Official Video)-bass.mp3" not in rels   # stem excluded
    assert len(auto["files"]) == 3
    assert auto["keep"] == "Big Hit.mp3"                     # cleanest name kept
    assert res["stats"]["to_quarantine"] == 2

    # the remix pair is recognised as a version variant, never auto-merged
    assert res["stats"]["mixed_groups"] == 1
    assert res["mixed"][0]["key"] == "jam session remix"
    assert res["stats"]["review_groups"] == 0


def test_build_plan_quarantines_and_redirects(tmp_path: Path):
    root = tmp_path / "lib"
    _make(root)
    plan = dedupe.build_plan(root, [
        {"keep": "Big Hit.mp3", "drop": ["Big Hit (2).mp3", "Big Hit (Official Video).mp3"]},
    ])
    assert len(plan.actions) == 2
    assert all(a.kind == "quarantine" for a in plan.actions)
    # every dropped dup is repointed at the kept copy (cues/playlists follow it)
    keeper = (root / "Big Hit.mp3").absolute()
    assert set(plan.location_redirects.values()) == {keeper}


def test_build_plan_rejects_paths_outside_library(tmp_path: Path):
    root = tmp_path / "lib"
    _make(root)
    (tmp_path / "evil.mp3").write_bytes(b"x")
    plan = dedupe.build_plan(root, [
        {"keep": "Big Hit.mp3", "drop": ["../evil.mp3", "Big Hit (2).mp3"]},
    ])
    # the traversal drop is ignored; only the in-library dup is quarantined
    assert len(plan.actions) == 1
    assert plan.actions[0].src == (root / "Big Hit (2).mp3").absolute()
    assert (tmp_path / "evil.mp3").exists()


def test_apply_is_reversible_and_never_deletes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(dedupe, "rekordbox_playlists_by_path", lambda: {})
    root = tmp_path / "lib"
    root.mkdir()
    _make(root)
    before = sum(1 for _ in root.rglob("*.mp3"))

    cfg = AppConfig(library_root=root, runs_dir=tmp_path / "runs")
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")

    plan = client.get("/api/dedupe/plan").json()
    assert plan["stats"]["auto_groups"] == 1
    g = plan["auto"][0]
    decision = {"keep": g["keep"], "drop": [f["rel"] for f in g["files"] if not f["keep"]]}

    r = client.post("/api/dedupe/apply", json={"decisions": [decision], "backup": False}).json()
    assert r["applied"] == 2
    # the two dropped copies are gone from their folder but NOT deleted
    assert not (root / "Big Hit (2).mp3").exists()
    assert (root / "Big Hit.mp3").exists()
    assert sum(1 for _ in root.rglob("*.mp3")) == before, "never-delete: file count conserved"

    # and the run is undoable
    u = client.post("/api/undo", json={"run_id": r["run_id"]}).json()
    assert u["reverted"] == 2
    assert (root / "Big Hit (2).mp3").exists()
    assert sum(1 for _ in root.rglob("*.mp3")) == before


def test_apply_rejects_crafted_unrelated_pair(tmp_path: Path, monkeypatch):
    """A crafted POST can't quarantine a file that isn't in a real dup group."""
    monkeypatch.setattr(dedupe, "rekordbox_playlists_by_path", lambda: {})
    root = tmp_path / "lib"
    root.mkdir()
    _make(root)
    cfg = AppConfig(library_root=root, runs_dir=tmp_path / "runs")
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    client.get("/api/dedupe/plan")  # establish the authoritative groups

    # "Lonely Track" is a singleton — pairing it with another song must be refused
    r = client.post("/api/dedupe/apply",
                    json={"decisions": [{"keep": "Big Hit.mp3", "drop": ["Lonely Track.mp3"]}],
                          "backup": False})
    assert r.status_code == 400
    assert (root / "Lonely Track.mp3").exists()       # untouched
    assert (root / "Big Hit.mp3").exists()
