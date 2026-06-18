"""Web app tests — the FastAPI layer over the same engine. The app inherits the
engine's safety; these check the HTTP contract, the marking/audio/organize
endpoints, the origin guard, and that apply/undo round-trips."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from releases import db as dbmod  # noqa: E402
from releases.scan import scan  # noqa: E402
from releases.webapp.app import create_app  # noqa: E402
from releases.webapp.state import AppConfig  # noqa: E402


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "projects"
    tld = root / "Beats" / "Tracks" / "Track List"
    for rel in ("Encara (Dibs).mp3", "Beats/afro beat_116_C_Maj.mp3"):
        p = tld / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 256)
    db = tmp_path / "index.db"
    dbmod.upsert_projects(dbmod.connect(db), scan(root))
    cfg = AppConfig(library_root=root, db_path=db, runs_dir=tmp_path / "runs")
    # base_url so the Host header is local (the origin guard rejects non-local Hosts)
    return TestClient(create_app(cfg), base_url="http://127.0.0.1:8765"), root


def _tracks(c):
    return c.get("/api/tracks").json()["tracks"]


class TestTracks:
    def test_list_tracks(self, client):
        c, _ = client
        data = c.get("/api/tracks").json()
        names = {t["name"] for t in data["tracks"]}
        assert "Encara (Dibs)" in names
        assert all("mixed" in (t["mix"],) or t["mix"] == "unmixed" for t in data["tracks"])

    def test_mark_genre_mix_master(self, client):
        c, _ = client
        tid = next(t["id"] for t in _tracks(c) if t["name"].startswith("Encara"))
        r = c.post("/api/mark", json={"id": tid, "genre": "jersey club", "mix": "mixed", "master": "mastered"})
        assert r.status_code == 200
        t = r.json()
        assert t["genre"] == "jersey club" and t["mix"] == "mixed" and t["master"] == "mastered"
        assert t["genre_marked"] is True

    def test_mark_validation(self, client):
        c, _ = client
        tid = _tracks(c)[0]["id"]
        assert c.post("/api/mark", json={"id": tid, "mix": "bogus"}).status_code == 400
        assert c.post("/api/mark", json={"id": tid, "genre": "a/b"}).status_code == 400
        assert c.post("/api/mark", json={"id": tid, "genre": "x" * 200}).status_code == 400  # too long
        assert c.post("/api/mark", json={"id": tid, "genre": "a\nb"}).status_code == 400  # control char

    def test_tracks_does_not_leak_abs_path(self, client):
        c, _ = client
        data = c.get("/api/tracks").json()
        assert "root" not in data  # no absolute library path sent to the page

    def test_audio_streams_and_bad_id_404s(self, client):
        c, _ = client
        tid = _tracks(c)[0]["id"]
        assert c.get(f"/api/audio?id={tid}").status_code == 200
        assert c.get("/api/audio?id=deadbeef").status_code == 404

    def test_unknown_track_id_rejected(self, client):
        c, _ = client
        assert c.post("/api/mark", json={"id": "nope", "genre": "x"}).status_code == 404


class TestOrganize:
    def test_preview_apply_undo_roundtrip(self, client):
        c, root = client
        tid = next(t["id"] for t in _tracks(c) if t["name"].startswith("Encara"))
        c.post("/api/mark", json={"id": tid, "genre": "jersey club", "mix": "mixed", "master": "mastered"})

        prev = c.get("/api/organize/preview").json()
        assert prev["moves"] and prev["tag_count"] >= 1
        plan_id = prev["plan_id"]

        ap = c.post("/api/organize/apply", json={"plan_id": plan_id}).json()
        assert ap["moved"] >= 1
        # the Encara file now sits in its genre/mix/master leaf
        assert (root / "Beats/Tracks/Track List/Jersey Club/mixed/mastered/Encara (Dibs).mp3").exists()
        # tracks now report it filed
        enc = next(t for t in _tracks(c) if t["name"].startswith("Encara"))
        assert enc["filed"] is True

        un = c.post("/api/organize/undo", json={"run_id": ap["run_id"]}).json()
        assert un["status"] == "undone"
        assert (root / "Beats/Tracks/Track List/Encara (Dibs).mp3").exists()

    def test_apply_unknown_plan_400(self, client):
        c, _ = client
        assert c.post("/api/organize/apply", json={"plan_id": "nope"}).status_code == 400


class TestSecurity:
    def test_cross_origin_post_blocked(self, client):
        c, _ = client
        tid = _tracks(c)[0]["id"]
        r = c.post("/api/mark", json={"id": tid, "genre": "x"},
                   headers={"origin": "http://evil.example"})
        assert r.status_code == 403

    def test_same_origin_post_allowed(self, client):
        c, _ = client
        tid = _tracks(c)[0]["id"]
        r = c.post("/api/mark", json={"id": tid, "genre": "afro"},
                   headers={"origin": "http://127.0.0.1:8765"})
        assert r.status_code == 200
