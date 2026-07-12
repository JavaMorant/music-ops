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


def test_setplan_rejects_absurd_bounds(tmp_path):
    # A single crafted request must not be able to OOM the server (review finding).
    client, root = _client(tmp_path)
    make_library(root, ["A - x.mp3"])
    assert client.post("/api/setplan", json={"minutes": 999999999999}).status_code == 422
    assert client.post("/api/setplan", json={"minutes": 9, "beam": 10000}).status_code == 422
    assert client.post("/api/setplan", json={"minutes": 9, "tracks_per_hour": 100000}).status_code == 422
    assert client.post("/api/setplan", json={"minutes": 9, "freshness": 7}).status_code == 422
