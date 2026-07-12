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


def test_setplan_page_served_and_navs_link_it(tmp_path):
    client, root = _client(tmp_path)
    r = client.get("/setplan.html")
    assert r.status_code == 200
    assert "<title>setplan — librarian</title>" in r.text
    assert 'id="spForm"' in r.text and 'id="spOut"' in r.text
    assert "--raised" in r.text                      # design tokens present
    for page in ("/", "/pulse.html", "/dedupe.html"):
        assert "setplan.html" in client.get(page).text


def test_setplan_page_has_no_js_string_sinks(tmp_path):
    # XSS regression guard: no JS source built from interpolated data (review PoC).
    client, root = _client(tmp_path)
    page = client.get("/setplan.html").text
    assert 'onclick="swap(' not in page
    assert "data-swap-path" in page and "data-swap-i" in page


def test_setplan_bad_input_returns_readable_error_not_500(tmp_path):
    client, root = _client(tmp_path)
    make_library(root, ["A - x.mp3"])
    # empty-ish minutes coerces to 0 -> gt=0 fails -> 422 with structured detail
    r = client.post("/api/setplan", json={"minutes": 0})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)   # the shape the page must now stringify


def test_setplan_accepts_catalogue_only_flag(tmp_path):
    client, root = _client(tmp_path)
    make_library(root, [f"A{i} - T{i}.mp3" for i in range(6)])
    r = client.post("/api/setplan", json={"minutes": 9, "catalogue_only": False})
    assert r.status_code == 200
    # threaded into the persisted spec so the page can show the "coming soon" note
    assert r.json()["plan"]["spec"]["catalogue_only"] is False
    assert client.post("/api/setplan", json={"minutes": 9}).json()["plan"]["spec"]["catalogue_only"] is True
