"""Web app tests — the FastAPI layer over the same engine. The app inherits the
engine's safety; these check the HTTP contract, the marking/audio/organize
endpoints, the origin guard, and that apply/undo round-trips."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from releases import db as dbmod  # noqa: E402
from releases.preview import has_ffmpeg  # noqa: E402
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

    def test_mark_notes_and_suitable_for(self, client):
        c, _ = client
        tid = next(t["id"] for t in _tracks(c) if t["name"].startswith("Encara"))
        t = c.post("/api/mark", json={"id": tid, "notes": "open for placement",
                                      "suitable_for": "Drake, Travis Scott"}).json()
        assert t["notes"] == "open for placement"
        assert t["suitable_for"] == "Drake, Travis Scott"
        # over-long / control-char values rejected
        assert c.post("/api/mark", json={"id": tid, "suitable_for": "x" * 300}).status_code == 400
        assert c.post("/api/mark", json={"id": tid, "notes": "a\nb"}).status_code == 400

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

    def test_pack_includes_suitable_for_and_notes(self, client):
        import io, zipfile
        c, _ = client
        tid = next(t["id"] for t in _tracks(c) if t["name"].startswith("Encara"))
        c.post("/api/mark", json={"id": tid, "genre": "trap",
                                  "suitable_for": "Drake", "notes": "exclusive avail"})
        r = c.get("/api/pack?genre=trap&name=Trap Pack")
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        tl = next(n for n in zf.namelist() if n.endswith("tracklist.txt"))
        text = zf.read(tl).decode()
        assert "suitable for: Drake" in text and "note: exclusive avail" in text

    def test_pack_no_match_404(self, client):
        c, _ = client
        assert c.get("/api/pack?genre=nope").status_code == 404


class TestSends:
    def test_log_send_history_and_per_track_badge(self, client):
        c, _ = client
        tid = next(t["id"] for t in _tracks(c) if t["name"].startswith("Encara"))
        c.post("/api/mark", json={"id": tid, "genre": "trap"})
        r = c.post("/api/sent", json={"contact": "John @ XYZ", "name": "Trap Pack", "genre": "trap"})
        assert r.status_code == 200 and r.json()["count"] >= 1
        sends = c.get("/api/sends").json()["sends"]
        assert any(s["contact"] == "John @ XYZ" and s["pack"] == "Trap Pack" for s in sends)
        # the beat now records who it was sent to (so you don't re-send it)
        t = next(t for t in _tracks(c) if t["id"] == tid)
        assert "John @ XYZ" in t["sent_to"]

    def test_log_send_requires_contact_and_a_match(self, client):
        c, _ = client
        assert c.post("/api/sent", json={"contact": "  ", "genre": "trap"}).status_code == 400
        assert c.post("/api/sent", json={"contact": "X", "genre": "nope-genre"}).status_code == 404


class TestExport:
    def test_tomp4_transcodes_webm_to_mp4(self, client, tmp_path):
        import shutil
        import subprocess
        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg not installed")
        c, _ = client
        webm = tmp_path / "in.webm"
        subprocess.run(
            ["ffmpeg", "-v", "error",
             "-f", "lavfi", "-i", "testsrc2=size=180x320:duration=1",
             "-f", "lavfi", "-i", "sine=frequency=200:duration=1",
             "-c:v", "libvpx", "-c:a", "libopus", "-shortest", "-y", str(webm)],
            check=True,
        )
        r = c.post("/api/tomp4", content=webm.read_bytes(),
                   headers={"Content-Type": "video/webm"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "video/mp4"
        assert b"ftyp" in r.content[:32]  # a real mp4 container

    def test_tomp4_rejects_empty(self, client):
        c, _ = client
        assert c.post("/api/tomp4", content=b"", headers={"Content-Type": "video/webm"}).status_code == 400

    def test_mux_unknown_id_404(self, client):
        # /api/mux re-validates the track id exactly like /api/audio — a forged
        # or stale id can never pull audio from outside Track List
        c, _ = client
        r = c.post("/api/mux?id=deadbeef&fps=60&lead_ms=350&tail_ms=500",
                   content=b"\x00\x00\x00\x01e",
                   headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 404

    def test_mux_rejects_empty_body(self, client):
        c, _ = client
        tid = _tracks(c)[0]["id"]
        r = c.post(f"/api/mux?id={tid}", content=b"",
                   headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 400

    @pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
    def test_mux_roundtrip_video_plus_audio(self, tmp_path):
        # real round-trip: a tiny annex-B H.264 stream POSTed for a real (sine)
        # track comes back as an mp4 holding BOTH a video and an audio stream
        import shutil as _sh
        import subprocess
        if not _sh.which("ffprobe"):
            pytest.skip("ffprobe not installed")
        root = tmp_path / "projects"
        tld = root / "Beats" / "Tracks" / "Track List"
        tld.mkdir(parents=True)
        wav = tld / "beat.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi",
             "-i", "sine=frequency=220:duration=2", "-y", str(wav)],
            check=True,
        )
        db = tmp_path / "index.db"
        dbmod.upsert_projects(dbmod.connect(db), scan(root))
        cfg = AppConfig(library_root=root, db_path=db, runs_dir=tmp_path / "runs")
        c = TestClient(create_app(cfg), base_url="http://127.0.0.1:8765")
        tid = c.get("/api/tracks").json()["tracks"][0]["id"]
        h264 = tmp_path / "v.h264"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi",
             "-i", "testsrc=duration=1:size=320x240:rate=30",
             "-c:v", "libx264", "-f", "h264", "-y", str(h264)],
            check=True,
        )
        r = c.post(f"/api/mux?id={tid}&fps=30&lead_ms=350&tail_ms=500&dur_ms=1000",
                   content=h264.read_bytes(),
                   headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "video/mp4"
        assert b"ftyp" in r.content[:32]  # a real mp4 container
        out = tmp_path / "out.mp4"
        out.write_bytes(r.content)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
             "-of", "csv=p=0", str(out)],
            capture_output=True, text=True, check=True,
        )
        rows = [ln.split(",") for ln in probe.stdout.split()]
        kinds = {rw[0] for rw in rows}
        assert kinds == {"video", "audio"}  # muxed, not video-only
        # dur_ms trimmed the (2s beat + lead + pad) audio to the 1s video length
        assert all(float(rw[1]) <= 1.2 for rw in rows if len(rw) > 1)


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


class TestTheme:
    def test_theme_token_parity(self, client):
        c, _ = client
        css = c.get("/static/css/themes.css").text

        def tokens(theme):
            block = re.search(r'\[data-theme="%s"\]\s*{([^}]*)}' % theme, css).group(1)
            return set(re.findall(r"--[\w-]+", block))

        assert tokens("classic") == tokens("editorial") != set()

    def test_app_css_uses_tokens_not_literals(self, client):
        c, _ = client
        assert "#c9a227" not in c.get("/static/css/app.css").text

    def test_editorial_is_default_and_distinct(self, client):
        c, _ = client
        assert 'data-theme' in c.get("/").text  # boot attr present
        css = c.get("/static/css/themes.css").text
        assert "#d4aa5e" in css and "#b3352c" in css and "Fraunces" in css
        assert c.get("/static/fonts/fraunces.woff2").status_code == 200

    def test_default_theme_is_editorial(self, client):
        c, _ = client
        # boot fallback in app.js flips to editorial
        assert '||"editorial"' in c.get("/static/js/app.js").text

    def test_editorial_polish_is_theme_scoped(self, client):
        # the visual-design pass ships as editorial-only decoration on shared
        # surface tokens: each new token is defined once per theme (parity keeps
        # classic a faithful flat revert), the chrome restyle is scoped under
        # the editorial attribute, and motion respects prefers-reduced-motion.
        c, _ = client
        themes = c.get("/static/css/themes.css").text
        for tok in ("--raise:", "--well:", "--hover:", "--line-soft:", "--good-bg:", "--good-fg:"):
            assert themes.count(tok) == 2, tok
        css = c.get("/static/css/app.css").text
        assert "prefers-reduced-motion" in css
        assert '[data-theme="editorial"] header button' in css


class TestSharedDeckModule:
    def test_index_includes_shared_deck(self, client):
        c, _ = client
        html = c.get("/").text
        assert "deck-module" in html          # server-side include ran
        assert "/static/deck/deck.css" in html

    def test_static_deck_assets_served(self, client):
        c, _ = client
        assert c.get("/static/deck/deck.css").status_code == 200
        assert "--deck-size" in c.get("/static/deck/deck.css").text

    def test_app_assets_split_out(self, client):
        c, _ = client  # brief writes client.get() — fixture returns (TestClient, root)
        html = c.get("/").text
        assert '/static/css/app.css' in html and '/static/js/app.js' in html
        assert c.get("/static/deck/deck.js").text.count("function deckSetSkin") == 1
        assert "<style>" not in html.split("deck-module")[0]  # no inline app stylesheet left

    def test_deck_css_serves_vinyl_fidelity(self, client):
        c, _ = client
        css = c.get("/static/deck/deck.css").text
        assert ".vinyl .lring" in css and "closest-side" in css

    def test_app_has_cinematic_fx_layer(self, client):
        c, _ = client
        page = c.get("/").text
        assert 'class="fx-vhs"' in page and 'class="fx-vig"' in page
        assert 'data-fx="vhs"' in page and 'data-fx="vignette"' in page
        css = c.get("/static/deck/deck.css").text
        assert ".off-vhs .fx-vhs" in css and ".off-vignette .fx-vig" in css

    def test_app_has_dust_and_hue_controls(self, client):
        c, _ = client
        assert "dustSpawn" in c.get("/api/turntable.js").text
        page = c.get("/").text
        assert 'id="ro-hue"' in page and 'id="ro-hueauto"' in page
        assert "hueOverride" in c.get("/static/js/app.js").text

    def test_app_has_title_and_end_cards(self, client):
        c, _ = client
        page = c.get("/").text
        assert 'id="tcard"' in page and 'id="ecard"' in page
        assert 'data-rofx="titlecard"' in page and 'data-rofx="endcard"' in page
        js = c.get("/static/js/app.js").text
        assert "showTitleCard" in js and "showEndCard" in js

    def test_app_chrome_accessibility_and_states(self, client):
        # UI/UX clear-wins: live regions for feedback, focus-visible + real disabled
        # styling, keyboard-operable toggles, a dismissable panel, and table states.
        c, _ = client
        page = c.get("/").text
        assert 'aria-live' in page                      # feedback is announced to screen readers
        assert 'id="toast"' in page and 'role="status"' in page
        assert 'aria-label="Close panel"' in page       # #panel has a close affordance
        css = c.get("/static/css/app.css").text
        assert "button:disabled" in css                 # disabled Apply reads as inert
        assert ":focus-visible" in css                  # visible keyboard focus
        js = c.get("/static/js/app.js").text
        assert "aria-pressed" in js                     # mix/master toggles are real buttons
        assert "function hidePanel" in js               # panel is dismissable (+ Esc)
        assert "tablestate" in js                       # loading / error / empty states

    def test_app_chrome_grouping_and_table_semantics(self, client):
        # header controls are chunked into labelled groups (not a flat wall), the
        # table headers are column-scoped, and the theme picker is labelled.
        c, _ = client
        page = c.get("/").text
        assert 'class="hgroup"' in page
        assert 'scope="col"' in page
        assert 'aria-label="Theme"' in page


class TestOneClickExport:
    """The one-click video export: the deck is rendered onto an offscreen
    canvas at the chosen aspect (9:16 1080x1920 default, 1:1, 16:9) recorded
    via canvas.captureStream + MediaRecorder — no getDisplayMedia prompt —
    mixed with the beat through a MediaStreamAudioDestinationNode, then mp4
    (direct or /api/tomp4)."""

    def test_export_button_and_overlay_served(self, client):
        c, _ = client
        page = c.get("/").text
        assert 'id="vidbtn"' in page and 'exportVideo()' in page   # the one-click button
        assert 'id="exportov"' in page and 'id="exportstatus"' in page  # live preview + status
        assert 'id="exportstop"' in page                           # stop early, still saves
        assert '/static/js/export.js' in page                      # renderer is loaded
        # the old prompt-based capture stays available as a fallback
        assert 'id="exportbtn"' in page and 'exportReel()' in page

    def test_export_renderer_served_multi_aspect(self, client):
        c, _ = client
        r = c.get("/static/js/export.js")
        assert r.status_code == 200
        js = r.text
        assert "function ttExportRender" in js
        assert "cfg.w || 1080" in js and "cfg.h || 1920" in js  # default frame stays 9:16
        # per-aspect composition: layout mode + a min(FW,FH) text/overlay scale
        assert "Math.min(FW, FH)" in js
        assert "'land'" in js and "'square'" in js and "'port'" in js
        assert "FH * 0.82" in js                           # 16:9: deck is the hero (~0.82 of height)
        assert "getDisplayMedia" not in js                 # renderer never screen-shares
        assert js.isascii()                                # packs/app JS stay ASCII

    def test_aspect_picker_and_per_aspect_dims(self, client):
        # segmented 9:16 / 1:1 / 16:9 control in the reel panel, remembered in
        # localStorage, feeding the export canvas size
        c, _ = client
        page = c.get("/").text
        for bid, label in (("ro-ar-916", "9:16"), ("ro-ar-11", "1:1"), ("ro-ar-169", "16:9")):
            assert f'id="{bid}"' in page and f">{label}</button>" in page
        js = c.get("/static/js/app.js").text
        assert "function reelSetAspect" in js and "applyReelAspect" in js
        assert "localStorage.setItem('reelAspect'" in js   # remembered like reelSize
        assert "'9:16': [1080, 1920]" in js                # the three presets
        assert "'1:1': [1080, 1080]" in js
        assert "'16:9': [1920, 1080]" in js
        assert "REEL_DIMS[reelAspect] || REEL_DIMS['9:16']" in js  # 9:16 stays the default
        assert "w: dims[0], h: dims[1]" in js              # export canvas sized from the preset

    def test_export_pipeline_is_prompt_free(self, client):
        c, _ = client
        js = c.get("/static/js/app.js").text
        assert "async function exportVideo" in js
        assert "captureStream" in js                       # canvas capture, not a screen share
        assert "createMediaStreamDestination" in js        # beat audio into the recording
        assert js.count("getDisplayMedia") > 0             # fallback path retained
        assert "'/api/tomp4'" in js                        # webm recordings still transcode
        assert "pauseDraw" in js                           # live deck yields the frame budget

    def test_export_overlay_styles_present(self, client):
        c, _ = client
        css = c.get("/static/css/app.css").text
        assert ".exportov" in css and ".exportstatus" in css

    def test_export_tag_caption_and_background_render(self, client):
        # producer tag on the video + generated caption + hidden-tab render fallback
        c, _ = client
        page = c.get("/").text
        assert 'id="ro-tag"' in page and 'id="capcard"' in page
        js = c.get("/static/js/app.js").text
        assert "reelTag" in js and "genreTags" in js and "showCaption" in js
        assert "@dibsss" in js and "@def.sted" in js       # default credit handles
        ex = c.get("/static/js/export.js").text
        assert "track.tag" in ex                            # tag drawn into the frame
        assert "document.hidden" in ex                      # keeps rendering when the tab is hidden

    def test_fast_export_webcodecs_pipeline(self, client):
        # the offline fast path: WebCodecs feature-detected, MessageChannel-paced
        # (not throttled in hidden tabs), backpressure-aware, muxed on the server;
        # the realtime captureStream recorder stays as the automatic fallback.
        c, _ = client
        js = c.get("/static/js/app.js").text
        assert "window.VideoEncoder" in js and "window.VideoFrame" in js  # feature detect
        assert "isConfigSupported" in js                    # config probed, never assumed
        assert "'avc1.640028'" in js and "'avc1.42e028'" in js  # codec fallback ladder
        assert "avc: {format: 'annexb'}" in js              # raw annex-B for the server mux
        assert "new MessageChannel()" in js                 # pacing survives hidden tabs
        assert "encodeQueueSize" in js                      # encoder backpressure respected
        assert "renderFrame" in js and "ttOfflineSpectrum" in js  # driven renderer + offline FFT
        assert "'/api/mux?id='" in js and "lead_ms=" in js  # server mux with the 0.35s lead
        assert "x realtime'" in js                          # measured speed in the overlay
        assert "captureStream" in js                        # realtime fallback retained
        fast = js.split("async function exportVideoFast")[1].split("async function exportVideo(")[0]
        assert "getDisplayMedia" not in fast                # fast path never screen-shares

    def test_export_renderer_driven_mode(self, client):
        # DRIVEN mode: the offline exporter re-uses the exact same frame() as the
        # realtime path (no duplicated draw code), on a synthetic frame clock
        c, _ = client
        ex = c.get("/static/js/export.js").text
        assert "function renderFrame" in ex
        assert "renderFrame: renderFrame" in ex             # exposed in the returned API
        assert "drivenBytes" in ex                          # injected spectrum, not the analyser
        assert ex.count("function frame(") == 1             # one frame loop for both modes
        assert "requestAnimationFrame(frame)" in ex         # realtime rAF path untouched
        assert ex.isascii()

    def test_offline_spectrum_module_served(self, client):
        c, _ = client
        page = c.get("/").text
        assert "/static/js/offline.js" in page              # loaded by the app page
        r = c.get("/static/js/offline.js")
        assert r.status_code == 200
        js = r.text
        assert "function ttOfflineSpectrum" in js
        assert "0.42" in js and "0.08" in js                # Blackman window (Web Audio flavour)
        assert "getChannelData" in js                       # works from the decoded AudioBuffer
        assert "leadSec" in js                              # reproduces the 0.35s audio lead-in
        assert js.isascii()
