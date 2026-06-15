"""Local web UI for clipper — a single-page "clip studio" over the analyze/cut
engine. Launch with `clipper web` (needs the [web] extra).

The engine is imported directly (no shelling out): analyze and cut run in
background threads so a multi-hour set never blocks the request, and the page
polls a job endpoint for progress. Bound to 127.0.0.1 — it serves and reads
local files by absolute path, which is fine for a single-user personal tool but
is NOT meant to face a network.
"""

from __future__ import annotations

import atexit
import datetime
import hashlib
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import media
from .analyze import (
    BUILD_WINDOW,
    CROWD_WEIGHT,
    DROP_WEIGHT,
    LEAD_IN_SECONDS,
    Segment,
    align_to_beats,
    blend_visual,
    score_audio,
    select_segments,
)
from .cut import cut_audio_segment, cut_segment
from .manifest import format_timestamp, write_captions, write_manifest

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".flv"}
MEDIA_EXTS = media.AUDIO_EXTENSIONS | VIDEO_EXTS

_INDEX = Path(__file__).parent / "web_static" / "index.html"
_PREVIEW_DIR = Path(tempfile.mkdtemp(prefix="clipper-preview-"))
atexit.register(lambda: shutil.rmtree(_PREVIEW_DIR, ignore_errors=True))


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class AnalyzeRequest(BaseModel):
    source: str
    length: int = 30
    spacing: int = 60
    top: int = 10
    drop_weight: float = DROP_WEIGHT
    lead_in: int = LEAD_IN_SECONDS
    build_window: int = BUILD_WINDOW
    crowd_weight: float = CROWD_WEIGHT
    beat_align: bool = True
    visual: bool = False


class SegmentIn(BaseModel):
    start: float
    duration: float
    score: float = 0.0


class CutRequest(BaseModel):
    source: str
    segments: list[SegmentIn]
    x_offset: Optional[int] = None
    out: Optional[str] = None


# --------------------------------------------------------------------------- #
# Background jobs (analyze / cut both run off-thread; the page polls /api/job)
# --------------------------------------------------------------------------- #


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"  # running | done | error
    stage: str = ""
    result: Optional[dict] = None
    error: Optional[str] = None


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def _start(kind: str, target, *args) -> str:
    job = Job(id=uuid.uuid4().hex[:12], kind=kind)
    with _LOCK:
        JOBS[job.id] = job

    def run() -> None:
        try:
            target(job, *args)
            job.status = "done"
        except media.MediaError as exc:
            job.status, job.error = "error", str(exc)
        except Exception as exc:  # surface the failure to the page, don't 500 silently
            job.status, job.error = "error", f"{type(exc).__name__}: {exc}"

    threading.Thread(target=run, daemon=True).start()
    return job.id


def _downsample(arr: np.ndarray, max_points: int = 1500) -> list[float]:
    """Per-second energy curve thinned to <= max_points for plotting, taking the
    max per bucket so peaks (the moments that matter) survive the downsample."""
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return []
    if a.size <= max_points:
        return [round(float(x), 4) for x in a]
    edges = np.linspace(0, a.size, max_points + 1).astype(int)
    return [round(float(a[edges[i] : edges[i + 1]].max()), 4) for i in range(max_points)]


def _do_analyze(job: Job, req: AnalyzeRequest) -> None:
    source = Path(req.source).expanduser()
    if not source.is_file():
        raise media.MediaError(f"source not found: {source}")
    frame = media.video_frame_size(source)
    with tempfile.TemporaryDirectory(prefix="clipper-web-") as tmp:
        job.stage = "Extracting audio…"
        wav = media.extract_audio(source, Path(tmp))
        job.stage = "Scoring energy + onsets…"
        scores, crowd, duration = score_audio(wav, want_crowd=req.crowd_weight > 0)
        if req.visual and frame is not None:
            job.stage = "Analyzing visual activity…"
            scores = blend_visual(scores, media.visual_activity(source, Path(tmp)))
        job.stage = "Selecting moments…"
        segments = select_segments(
            scores,
            clip_len=req.length,
            max_clips=req.top,
            spacing=req.spacing,
            lead_in=req.lead_in,
            drop_weight=req.drop_weight,
            build_window=req.build_window,
            crowd=crowd,
            crowd_weight=req.crowd_weight,
        )
        if req.beat_align:
            job.stage = "Aligning to beats…"
            segments = align_to_beats(wav, segments, duration)
        energy = _downsample(scores)
    job.result = {
        "source": str(source),
        "duration": duration,
        "is_video": frame is not None,
        "frame": list(frame) if frame else None,
        "energy": energy,
        "energy_seconds": int(np.asarray(scores).size),
        "segments": [
            {
                "rank": i,
                "start": s.start,
                "end": s.end,
                "duration": s.duration,
                "score": round(s.score, 4),
                "timestamp": format_timestamp(s.start),
            }
            for i, s in enumerate(segments, 1)
        ],
    }


def _fresh_out_dir(out: Optional[Path]) -> Path:
    """out/<date>/, suffixed with the time if that run already exists (mirrors
    the CLI so the web app and `clipper cut` land in the same place)."""
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        return out
    base = Path("out") / datetime.date.today().isoformat()
    out_dir = base
    if (out_dir / "manifest.csv").exists():
        out_dir = base.parent / f"{base.name}-{datetime.datetime.now():%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _file_url(path: Path, download: bool = False) -> str:
    url = f"/api/file?path={quote(str(path))}"
    return url + "&download=1" if download else url


def _do_cut(job: Job, req: CutRequest) -> None:
    source = Path(req.source).expanduser()
    if not source.is_file():
        raise media.MediaError(f"source not found: {source}")
    if not req.segments:
        raise media.MediaError("no segments selected to cut")
    frame = media.video_frame_size(source)
    out_dir = _fresh_out_dir(Path(req.out).expanduser() if req.out else None)
    ext = ".mp4" if frame is not None else ".m4a"
    results: list[tuple[Path, Segment]] = []
    total = len(req.segments)
    for i, s in enumerate(req.segments, 1):
        job.stage = f"Cutting clip {i}/{total}…"
        seg = Segment(float(s.start), float(s.duration), float(s.score))
        dest = out_dir / f"clip_{i:02d}{ext}"
        if frame is not None:
            cut_segment(source, seg, dest, x_offset=req.x_offset)
        else:
            cut_audio_segment(source, seg, dest)
        results.append((dest, seg))
    manifest = write_manifest(out_dir, source, results)
    captions = write_captions(out_dir, results)
    job.result = {
        "out_dir": str(out_dir),
        "is_video": frame is not None,
        "clips": [
            {
                "name": dest.name,
                "url": _file_url(dest),
                "download_url": _file_url(dest, download=True),
                "start": seg.start,
                "timestamp": format_timestamp(seg.start),
                "score": round(seg.score, 4),
            }
            for dest, seg in results
        ],
        "manifest_url": _file_url(manifest, download=True),
        "captions_url": _file_url(captions, download=True),
    }


# --------------------------------------------------------------------------- #
# App + endpoints
# --------------------------------------------------------------------------- #

app = FastAPI(title="clipper studio")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX.read_text(encoding="utf-8")


@app.post("/api/analyze")
def api_analyze(req: AnalyzeRequest) -> dict:
    return {"job_id": _start("analyze", _do_analyze, req)}


@app.post("/api/cut")
def api_cut(req: CutRequest) -> dict:
    return {"job_id": _start("cut", _do_cut, req)}


@app.get("/api/job/{job_id}")
def api_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "stage": job.stage,
        "result": job.result,
        "error": job.error,
    }


@app.get("/api/preview")
def api_preview(source: str, start: float, dur: float) -> FileResponse:
    """A fast stream-copy of the source window so a candidate moment can be
    auditioned before cutting. Copy (no re-encode) snaps to the nearest keyframe
    — close enough to judge a moment; the real cut is frame-accurate."""
    src = Path(source).expanduser()
    if not src.is_file():
        raise HTTPException(404, "source not found")
    is_video = media.video_frame_size(src) is not None
    ext = ".mp4" if is_video else ".m4a"
    key = hashlib.sha1(f"{src}|{start:.2f}|{dur:.2f}".encode()).hexdigest()[:16]
    out = _PREVIEW_DIR / f"{key}{ext}"
    if not out.exists():
        args = [
            "ffmpeg", "-v", "error",
            "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}",
            "-i", str(src),
        ]
        if is_video:
            # stream-copy: fast keyframe seek, no re-encode (good enough to audition)
            args += ["-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart"]
        else:
            # audio: re-encode to AAC. Stream-copying a compressed source (mp3/aac/
            # flac) into a container mislabels it and browsers won't decode it; a
            # short preview re-encode is cheap and isn't keyframe-bound anyway.
            args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        args += ["-y", str(out)]
        try:
            subprocess.run(args, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise HTTPException(500, f"preview failed: {exc.stderr.strip()[-400:]}")
    return FileResponse(out)


@app.get("/api/file")
def api_file(path: str, download: bool = False) -> FileResponse:
    p = Path(path).expanduser()
    if not p.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(p, filename=p.name) if download else FileResponse(p)


@app.get("/api/browse")
def api_browse(dir: Optional[str] = None) -> dict:
    """List sub-folders and media files in a directory, for the file picker."""
    d = (Path(dir).expanduser() if dir else Path.home()).resolve()
    if not d.is_dir():
        raise HTTPException(404, "not a directory")
    entries = []
    try:
        for child in sorted(d.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name.startswith("."):
                continue
            is_dir = child.is_dir()
            is_media = not is_dir and child.suffix.lower() in MEDIA_EXTS
            if is_dir or is_media:
                entries.append(
                    {"name": child.name, "path": str(child), "is_dir": is_dir, "is_media": is_media}
                )
    except PermissionError:
        pass
    return {"dir": str(d), "parent": str(d.parent) if d.parent != d else None, "entries": entries}
