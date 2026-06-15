"""Tests for the web layer (clipper.web).

Pure tests (no media): _downsample, _fresh_out_dir, /api/browse, /api/file,
and the job error path. Integration tests (skipped without ffmpeg) drive a real
analyze→cut round trip through the FastAPI app on a synthesized wav.
"""

from __future__ import annotations

import shutil
import time

import numpy as np
import pytest

pytest.importorskip("fastapi")
import soundfile as sf
from fastapi.testclient import TestClient

from clipper import web

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
client = TestClient(web.app)


def _wait(job_id: str, timeout: float = 30.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/job/{job_id}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} timed out")


# --------------------------------------------------------------------------- #
# _downsample
# --------------------------------------------------------------------------- #


class TestDownsample:
    def test_empty(self):
        assert web._downsample(np.array([])) == []

    def test_under_max_returns_all_points(self):
        out = web._downsample(np.array([0.1, 0.5, 0.9]), max_points=1500)
        assert out == [0.1, 0.5, 0.9]

    def test_over_max_thins_to_max_points(self):
        out = web._downsample(np.arange(10000, dtype=float), max_points=100)
        assert len(out) == 100

    def test_peak_preserved_by_max_bucketing(self):
        a = np.zeros(1000)
        a[503] = 1.0  # a single spike must survive the downsample
        out = web._downsample(a, max_points=50)
        assert max(out) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# _fresh_out_dir — mirrors the CLI's out/<date>/ behaviour
# --------------------------------------------------------------------------- #


class TestFreshOutDir:
    def test_explicit_dir_is_created(self, tmp_path):
        target = tmp_path / "nested" / "clips"
        out = web._fresh_out_dir(target)
        assert out == target and out.is_dir()

    def test_default_uses_out_date_and_suffixes_on_collision(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        first = web._fresh_out_dir(None)
        assert first.is_dir() and first.parent.name == "out"
        (first / "manifest.csv").write_text("x")  # simulate a prior run
        second = web._fresh_out_dir(None)
        assert second != first and second.is_dir()


# --------------------------------------------------------------------------- #
# /api/browse and /api/file
# --------------------------------------------------------------------------- #


class TestBrowse:
    def test_lists_dirs_and_media_only(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "set.mp4").write_bytes(b"x")
        (tmp_path / "notes.txt").write_text("x")  # non-media: excluded
        (tmp_path / ".hidden").write_text("x")  # dotfile: excluded
        r = client.get("/api/browse", params={"dir": str(tmp_path)}).json()
        names = {e["name"] for e in r["entries"]}
        assert names == {"sub", "set.mp4"}
        assert r["parent"] == str(tmp_path.parent)
        media_flags = {e["name"]: e["is_media"] for e in r["entries"]}
        assert media_flags["set.mp4"] is True and media_flags["sub"] is False

    def test_missing_dir_404(self, tmp_path):
        assert client.get("/api/browse", params={"dir": str(tmp_path / "nope")}).status_code == 404


class TestFile:
    def test_missing_file_404(self, tmp_path):
        assert client.get("/api/file", params={"path": str(tmp_path / "nope.mp4")}).status_code == 404

    def test_serves_existing_file(self, tmp_path):
        f = tmp_path / "clip.txt"
        f.write_text("hello")
        r = client.get("/api/file", params={"path": str(f)})
        assert r.status_code == 200 and r.text == "hello"

    def test_download_sets_attachment(self, tmp_path):
        f = tmp_path / "manifest.csv"
        f.write_text("a,b")
        r = client.get("/api/file", params={"path": str(f), "download": "1"})
        assert "attachment" in r.headers.get("content-disposition", "")


# --------------------------------------------------------------------------- #
# Job error path (no ffmpeg needed: fails on the is_file() check)
# --------------------------------------------------------------------------- #


def test_analyze_missing_source_reports_error():
    job_id = client.post("/api/analyze", json={"source": "/no/such/set.mp4"}).json()["job_id"]
    job = _wait(job_id)
    assert job["status"] == "error"
    assert "not found" in job["error"]


def test_job_unknown_id_404():
    assert client.get("/api/job/deadbeef").status_code == 404


# --------------------------------------------------------------------------- #
# Integration: real analyze → cut on a synthesized wav
# --------------------------------------------------------------------------- #


def _make_wav(path, seconds=8, sr=22050):
    t = np.arange(int(sr * seconds)) / sr
    tone = 0.05 * np.sin(2 * np.pi * 220 * t)
    burst = (t > 4) & (t < 6)  # a loud section the engine should pick
    tone[burst] += 0.5 * np.sin(2 * np.pi * 330 * t[burst])
    sf.write(str(path), tone.astype("float32"), sr)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
class TestAnalyzeCutRoundTrip:
    def test_analyze_then_cut_audio_source(self, tmp_path):
        wav = tmp_path / "set.wav"
        _make_wav(wav)

        job_id = client.post(
            "/api/analyze",
            json={"source": str(wav), "length": 15, "spacing": 0, "top": 3},
        ).json()["job_id"]
        job = _wait(job_id)
        assert job["status"] == "done", job.get("error")
        res = job["result"]
        assert res["is_video"] is False
        assert res["frame"] is None
        assert len(res["energy"]) > 0
        assert len(res["segments"]) >= 1
        seg = res["segments"][0]
        assert "timestamp" in seg and seg["duration"] > 0

        out_dir = tmp_path / "out"
        cut_id = client.post(
            "/api/cut",
            json={
                "source": str(wav),
                "out": str(out_dir),
                "segments": [{"start": seg["start"], "duration": seg["duration"], "score": seg["score"]}],
            },
        ).json()["job_id"]
        cut = _wait(cut_id)
        assert cut["status"] == "done", cut.get("error")
        clips = cut["result"]["clips"]
        assert len(clips) == 1
        assert clips[0]["name"].endswith(".m4a")  # audio-only source → AAC clip
        assert (out_dir / "clip_01.m4a").is_file()
        assert (out_dir / "manifest.csv").is_file()

        served = client.get(clips[0]["url"])
        assert served.status_code == 200


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_preview_compressed_audio_is_reencoded_to_aac(tmp_path):
    """A compressed-audio source must preview as real AAC, not a stream-copy into
    a container browsers can't decode (regression for the .wav-container bug)."""
    import subprocess

    src = tmp_path / "set.flac"  # compressed source (libsndfile writes FLAC)
    _make_wav(src)
    r = client.get("/api/preview", params={"source": str(src), "start": 1, "dur": 2})
    assert r.status_code == 200
    got = tmp_path / "preview.m4a"
    got.write_bytes(r.content)
    codec = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(got)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert codec == "aac"
