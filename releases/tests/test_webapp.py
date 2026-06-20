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

    def test_mark_artists_and_month(self, client):
        c, _ = client
        tid = next(t["id"] for t in _tracks(c) if t["name"].startswith("Encara"))
        t = c.post("/api/mark", json={"id": tid, "artists": "Jah, EJ", "month": "2026-06"}).json()
        assert t["artists"] == "Jah, EJ" and t["month"] == "2026-06"
        # the month now appears in the filterable months list
        data = c.get("/api/tracks").json()
        assert "2026-06" in data["months"]
        # over-long artists rejected
        assert c.post("/api/mark", json={"id": tid, "artists": "x" * 500}).status_code == 400

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


class TestDownload:
    def test_download_filtered_zip(self, client):
        import io, zipfile
        c, _ = client
        tid = next(t["id"] for t in _tracks(c) if t["name"].startswith("Encara"))
        c.post("/api/mark", json={"id": tid, "genre": "trap", "month": "2026-06"})
        r = c.get("/api/download?genre=trap")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert "trap.zip" in r.headers.get("content-disposition", "")
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert names == ["Encara (Dibs).mp3"]  # only the trap-tagged track, by basename

    def test_download_by_month(self, client):
        import io, zipfile
        c, _ = client
        tid = next(t["id"] for t in _tracks(c) if t["name"].startswith("Encara"))
        c.post("/api/mark", json={"id": tid, "month": "2026-06"})
        r = c.get("/api/download?month=2026-06")
        assert r.status_code == 200
        assert "2026-06" in r.headers.get("content-disposition", "")
        assert len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == 1

    def test_download_no_match_404(self, client):
        c, _ = client
        assert c.get("/api/download?genre=nonexistent-genre").status_code == 404

    def test_download_all(self, client):
        import io, zipfile
        c, _ = client
        r = c.get("/api/download")
        assert r.status_code == 200
        assert len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == len(_tracks(c))

    def test_download_excludes_symlink_escaping_track_list(self, tmp_path):
        import io, os, zipfile
        root = tmp_path / "projects"
        tld = root / "Beats" / "Tracks" / "Track List"
        tld.mkdir(parents=True)
        (tld / "real.mp3").write_bytes(b"\xff\xfb\x90\x00" + b"x" * 80)
        secret = tmp_path / "outside" / "secret.mp3"
        secret.parent.mkdir()
        secret.write_bytes(b"TOP-SECRET-OUTSIDE-LIBRARY")
        os.symlink(secret, tld / "sneaky.mp3")  # symlink in Track List → outside
        db = tmp_path / "index.db"
        dbmod.upsert_projects(dbmod.connect(db), scan(root))
        cfg = AppConfig(library_root=root, db_path=db, runs_dir=tmp_path / "runs")
        c = TestClient(create_app(cfg), base_url="http://127.0.0.1:8765")
        names = zipfile.ZipFile(io.BytesIO(c.get("/api/download").content)).namelist()
        assert "real.mp3" in names
        assert "sneaky.mp3" not in names  # the escaping symlink is excluded

    def test_download_sanitizes_separator_arcnames(self, tmp_path):
        import io, zipfile
        root = tmp_path / "projects"
        tld = root / "Beats" / "Tracks" / "Track List"
        tld.mkdir(parents=True)
        (tld / "evil\\..\\x.mp3").write_bytes(b"\xff\xfb\x90\x00")  # backslashes legal on posix
        db = tmp_path / "index.db"
        dbmod.upsert_projects(dbmod.connect(db), scan(root))
        cfg = AppConfig(library_root=root, db_path=db, runs_dir=tmp_path / "runs")
        c = TestClient(create_app(cfg), base_url="http://127.0.0.1:8765")
        names = zipfile.ZipFile(io.BytesIO(c.get("/api/download").content)).namelist()
        assert names and all("\\" not in n and "/" not in n for n in names)  # no separators survive


class TestPack:
    def test_pack_zip_has_player_tracklist_and_clean_audio(self, client):
        import io, zipfile
        c, _ = client
        tid = next(t["id"] for t in _tracks(c) if t["name"].startswith("Encara"))
        c.post("/api/mark", json={"id": tid, "genre": "trap"})
        r = c.get("/api/pack?genre=trap&name=Trap Pack June")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert any(n.endswith("/index.html") for n in names)
        assert any(n.endswith("/tracklist.txt") for n in names)
        assert any(n.endswith(".mp3") and " - " in n for n in names)  # clean-named audio
        assert all(n.startswith("Trap Pack June/") for n in names)  # one top folder

    def test_pack_no_match_404(self, client):
        c, _ = client
        assert c.get("/api/pack?genre=nope").status_code == 404


class TestDeck:
    def test_tracks_expose_producer_and_no_cover_by_default(self, client):
        c, _ = client
        d = c.get("/api/tracks").json()
        assert d["producer"] == "Dibs" and d["cover"] is None
        assert c.get("/api/cover").status_code == 404  # none configured

    def test_turntable_js_served(self, client):
        c, _ = client
        r = c.get("/api/turntable.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]
        assert "function ttRun" in r.text  # the shared visualizer module

    def test_upload_cover_in_app(self, client):
        import base64
        c, _ = client
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 60
        data = "data:image/png;base64," + base64.b64encode(png).decode()
        r = c.post("/api/cover", json={"name": "art.png", "data": data})
        assert r.status_code == 200 and r.json()["cover"] == "/api/cover"
        # now it's served + advertised
        assert c.get("/api/cover").status_code == 200
        assert c.get("/api/tracks").json()["cover"] == "/api/cover"

    def test_upload_cover_rejects_non_image(self, client):
        import base64
        c, _ = client
        data = base64.b64encode(b"nope").decode()
        assert c.post("/api/cover", json={"name": "x.txt", "data": data}).status_code == 400

    def test_upload_cover_cross_origin_blocked(self, client):
        import base64
        c, _ = client
        data = base64.b64encode(b"\x89PNG").decode()
        r = c.post("/api/cover", json={"name": "a.png", "data": data},
                   headers={"origin": "http://evil.example"})
        assert r.status_code == 403

    def test_cover_served_when_configured(self, tmp_path):
        root = tmp_path / "projects"
        tld = root / "Beats" / "Tracks" / "Track List"
        tld.mkdir(parents=True)
        (tld / "a.mp3").write_bytes(b"\xff\xfb\x90\x00")
        cov = tmp_path / "art.png"
        cov.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
        db = tmp_path / "index.db"
        dbmod.upsert_projects(dbmod.connect(db), scan(root))
        cfg = AppConfig(library_root=root, db_path=db, runs_dir=tmp_path / "runs", cover_src=cov)
        c = TestClient(create_app(cfg), base_url="http://127.0.0.1:8765")
        assert c.get("/api/tracks").json()["cover"] == "/api/cover"
        r = c.get("/api/cover")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"


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
