"""FastAPI app: a thin HTTP layer over the releases engine.

Handlers are sync ``def`` (FastAPI runs them in a threadpool, so a slow walk
never blocks the loop) and each opens its own sqlite connection (connections
aren't shared across threads). The only writes go through ``db.set_marks``
(index-only) and ``organize.apply_plan`` / ``undo_run`` — no new mutation path.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .. import db as dbmod
from .. import organize as orgmod
from .. import pack as packmod
from ..model import MASTER_STATES, MIX_STATES
from .security import guard_origin
from .state import AppConfig, AppState

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class MarkBody(BaseModel):
    id: str
    genre: str | None = None
    mix: str | None = None
    master: str | None = None
    artists: str | None = None
    month: str | None = None
    notes: str | None = None
    suitable_for: str | None = None


class ApplyBody(BaseModel):
    plan_id: str


class UndoBody(BaseModel):
    run_id: str


class CoverBody(BaseModel):
    name: str
    data: str  # base64 (optionally a data: URL)


class SentBody(BaseModel):
    contact: str
    name: str | None = None
    genre: str | None = None
    month: str | None = None


def _tid(abspath: str) -> str:
    return hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:16]


def _zip_name(genre: str | None, month: str | None) -> str:
    base = genre or "track-list"
    if month:
        base += "_" + month
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "tracklist"
    return base + ".zip"


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="releases — Track List")
    state = AppState(config=config)

    def conn():
        return dbmod.connect(config.db_path)

    tl_real = orgmod._real(config.track_list_dir)

    def _in_tl(path: Path) -> bool:
        """Track List containment, lexical AND through symlinks (realpath) — so a
        symlink planted in Track List can't read/zip a file outside it."""
        return orgmod._within(config.track_list_dir, path) and orgmod._contained(tl_real, path)

    def _track_dict(p, recipients: list | None = None) -> dict:
        path = Path(p.path)
        try:
            rel = path.relative_to(config.track_list_dir)
            parts = rel.parts
        except ValueError:
            parts = ()
        return {
            "id": _tid(p.path),
            "sent_to": recipients or [],  # contacts who've already been sent this beat
            "name": p.name,
            "location": "/".join(parts[:-1]) if len(parts) > 1 else "(loose)",
            "filed": len(parts) == 4,  # <genre>/<mix>/<master>/<name>
            "genre": p.effective_genre,
            "genre_marked": p.genre_manual is not None,
            "mix": p.effective_mix,
            "master": p.effective_master,
            "artists": p.artists or "",
            "month": p.pack_month or "",
            "notes": p.notes or "",
            "suitable_for": p.suitable_for or "",
            "bpm": p.bpm,
            "key": p.key,
            "taggable": path.suffix.lower() == ".mp3",
        }

    def _resolve(c, tid: str) -> str:
        """Map an opaque id back to an absolute path, re-validating containment
        and existence — a stale or forged id can never read outside Track List."""
        abspath = state.track_paths.get(tid)
        if not abspath:
            raise HTTPException(404, "unknown track id (refresh the list)")
        p = Path(abspath)
        if not _in_tl(p) or not p.is_file():
            raise HTTPException(404, "track not found")
        return abspath

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/tracks")
    def list_tracks():
        c = conn()
        tl = config.track_list_dir
        tracks = []
        mapping: dict[str, str] = {}
        recipients = dbmod.recipients_by_path(c)  # path → who's already been sent it
        for p in dbmod.all_projects(c):
            path = Path(p.path)
            if not _in_tl(path) or not path.is_file():
                continue
            if path.suffix.lower() not in orgmod.AUDIO_EXTS:
                continue
            mapping[_tid(p.path)] = p.path
            tracks.append(_track_dict(p, recipients.get(p.path, [])))
        state.track_paths = mapping
        tracks.sort(key=lambda t: (t["genre"], t["name"].lower()))
        genres = sorted({t["genre"] for t in tracks if t["genre"] != "unknown"})
        months = sorted({t["month"] for t in tracks if t["month"]}, reverse=True)
        has_cover = bool(config.cover_src and config.cover_src.is_file()
                         and packmod.is_image(config.cover_src))
        return {"tracks": tracks, "genres": genres, "months": months,
                "producer": config.producer, "cover": "/api/cover" if has_cover else None}

    @app.get("/api/turntable.js")
    def turntable_js():
        from fastapi.responses import Response
        return Response(packmod.TURNTABLE_JS, media_type="application/javascript")

    @app.post("/api/cover", dependencies=[Depends(guard_origin)])
    def set_cover(body: CoverBody):
        """Upload a cover image from the browser. Saved beside the db (outside
        the library) and used as the pack/turntable label art."""
        ext = Path(body.name).suffix.lower()
        if ext not in packmod.IMAGE_EXTS:
            raise HTTPException(400, "cover must be an image (jpg/png/webp/gif)")
        raw = body.data
        if raw.startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            blob = base64.b64decode(raw)
        except Exception:
            raise HTTPException(400, "invalid image data")
        if len(blob) > 8 * 1024 * 1024:
            raise HTTPException(400, "cover too large (max 8 MB)")
        dest = config.db_path.parent / ("cover" + ext)  # fixed name → no traversal
        for old in config.db_path.parent.glob("cover.*"):
            if old != dest:
                try:
                    old.unlink()
                except OSError:
                    pass
        dest.write_bytes(blob)
        config.cover_src = dest
        return {"cover": "/api/cover"}

    @app.get("/api/cover")
    def cover():
        src = config.cover_src  # launch-time path or uploaded via POST
        if not src or not src.is_file() or not packmod.is_image(src):
            raise HTTPException(404, "no cover set")
        media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif"}
        return FileResponse(src, media_type=media.get(src.suffix.lower(), "application/octet-stream"))

    @app.post("/api/mark", dependencies=[Depends(guard_origin)])
    def mark(body: MarkBody):
        c = conn()
        abspath = _resolve(c, body.id)
        if body.genre is not None:
            g = body.genre
            if (not g.strip() or "/" in g or "\\" in g or len(g) > 80
                    or any(ord(ch) < 32 for ch in g)):
                raise HTTPException(400, "genre must be a short plain name without slashes")
        if body.mix is not None and body.mix not in MIX_STATES:
            raise HTTPException(400, f"mix must be one of {MIX_STATES}")
        if body.master is not None and body.master not in MASTER_STATES:
            raise HTTPException(400, f"master must be one of {MASTER_STATES}")
        for field_name, val, cap in (("artists", body.artists, 200), ("month", body.month, 40),
                                     ("suitable_for", body.suitable_for, 200), ("notes", body.notes, 400)):
            if val is not None and (len(val) > cap or any(ord(ch) < 32 for ch in val)):
                raise HTTPException(400, f"{field_name} too long or has control characters")
        genre = body.genre.strip() if body.genre is not None else None
        dbmod.set_marks(c, abspath, genre=genre, mix=body.mix, master=body.master,
                        artists=body.artists, month=body.month,
                        notes=body.notes, suitable_for=body.suitable_for)
        rows = c.execute("SELECT * FROM projects WHERE path = ?", (abspath,)).fetchall()
        if not rows:
            raise HTTPException(404, "track not in index")
        recips = dbmod.recipients_by_path(c).get(abspath, [])
        return _track_dict(dbmod._row_to_project(rows[0]), recips)

    @app.get("/api/audio")
    def audio(id: str):
        c = conn()
        abspath = _resolve(c, id)
        suffix = Path(abspath).suffix.lower()
        if suffix not in orgmod.AUDIO_EXTS:  # a stale id must not stream a non-audio file
            raise HTTPException(404, "not an audio file")
        media = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
                 ".m4a": "audio/mp4", ".aiff": "audio/aiff", ".aif": "audio/aiff",
                 ".ogg": "audio/ogg"}.get(suffix, "application/octet-stream")
        return FileResponse(abspath, media_type=media)  # Starlette handles Range for seeking

    @app.get("/api/organize/preview")
    def preview():
        c = conn()
        marks = {p.path: p for p in dbmod.all_projects(c)}
        moves, tag_edits, notes = orgmod.plan_tracklist(config.library_root, marks)
        plan = orgmod.Plan(config.library_root, moves, tag_edits)
        plan_id = uuid4().hex
        state.cache_plan(plan_id, plan)
        root = config.library_root
        return {
            "plan_id": plan_id,
            "moves": [
                {"src": str(a.src.relative_to(root)), "dest": str(a.dest.relative_to(root))}
                for a in moves
            ],
            "tag_count": len(tag_edits),
            "notes": notes,
        }

    @app.post("/api/organize/apply", dependencies=[Depends(guard_origin)])
    def apply(body: ApplyBody):
        plan = state.get_plan(body.plan_id)
        if plan is None:
            raise HTTPException(400, "unknown or expired plan — preview again")
        if not plan.actions and not plan.tag_edits:
            return {"run_id": None, "moved": 0, "tagged": 0}
        c = conn()
        try:
            journal = orgmod.apply_plan(plan, config.runs_dir)
        except orgmod.ApplyInterrupted as e:
            _relink(c, e.journal)
            raise HTTPException(500, f"apply interrupted (run {e.journal.run_id}, undoable): {e}")
        except orgmod.OrganizeError as e:
            raise HTTPException(400, f"refused: {e}")
        moved = _relink(c, journal)
        return {"run_id": journal.run_id, "moved": moved, "tagged": len(journal.tag_edits)}

    @app.post("/api/organize/undo", dependencies=[Depends(guard_origin)])
    def undo(body: UndoBody):
        c = conn()
        try:
            journal = orgmod.undo_run(body.run_id, config.runs_dir)
        except orgmod.OrganizeError as e:
            raise HTTPException(400, f"refused: {e}")
        for entry in journal.actions:
            if entry.status == orgmod.REVERTED:
                dbmod.repath(c, str(entry.action.dest), str(entry.action.src))
        return {"run_id": journal.run_id, "status": journal.status}

    def _matching_projects(c, genre: str | None, month: str | None) -> list:
        out = []
        for p in dbmod.all_projects(c):
            path = Path(p.path)
            if not _in_tl(path) or not path.is_file():
                continue
            if path.suffix.lower() not in orgmod.AUDIO_EXTS:
                continue
            if genre and p.effective_genre != genre:
                continue
            if month and (p.pack_month or "") != month:
                continue
            out.append(p)
        return out

    def _matching_files(c, genre: str | None, month: str | None) -> list[Path]:
        return [Path(p.path) for p in _matching_projects(c, genre, month)]

    @app.get("/api/download", dependencies=[Depends(guard_origin)])
    def download(genre: str | None = None, month: str | None = None):
        """Zip the audio files matching the genre/month filter (read-only) and
        send them as one download — for emailing a pack."""
        c = conn()
        files = _matching_files(c, genre, month)
        if not files:
            raise HTTPException(404, "no matching tracks to download")
        fd, tmp = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            used: dict[str, int] = {}
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:  # mp3s already compressed
                for path in files:
                    # Strip BOTH separators + control chars: a POSIX filename may
                    # legally contain '\\' or newlines, which become path
                    # separators when the RECIPIENT extracts on Windows (zip-slip).
                    arc = re.sub(r"[\\/\r\n\x00-\x1f]+", "_", path.name) or "track"
                    if arc in used:
                        used[arc] += 1
                        s = Path(arc)
                        arc = f"{s.stem} ({used[arc]}){s.suffix}"
                    else:
                        used[arc] = 0
                    z.write(path, arcname=arc)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        name = _zip_name(genre, month)
        return FileResponse(
            tmp, media_type="application/zip", filename=name,
            background=BackgroundTask(lambda: os.path.exists(tmp) and os.unlink(tmp)),
        )

    @app.get("/api/pack", dependencies=[Depends(guard_origin)])
    def pack(genre: str | None = None, month: str | None = None, name: str | None = None,
             preview: bool = False, preview_seconds: int = 40):
        """Build a polished pack (clean-named audio + player + tracklist) from the
        filter and return it zipped. Read-only on the library. With ``preview``,
        beats are trimmed + tagged so the pack can't be ripped (needs ffmpeg)."""
        from .. import preview as previewmod
        if preview and not previewmod.has_ffmpeg():
            raise HTTPException(400, "previews need ffmpeg installed on this machine")
        preview_seconds = max(10, min(preview_seconds, 120))
        c = conn()
        projs = _matching_projects(c, genre, month)
        if not projs:
            raise HTTPException(404, "no matching tracks for the pack")
        pack_name = (name or genre or "Beat Pack").strip()[:80] or "Beat Pack"
        tracks = [
            packmod.PackTrack(src=Path(p.path), title=p.name, bpm=p.bpm, key=p.key,
                              genre=p.effective_genre, artists=p.artists or "",
                              suitable_for=p.suitable_for or "", notes=p.notes or "")
            for p in projs
        ]
        meta = packmod.PackMeta(name=pack_name, producer=config.producer,
                                made_on=date.today().isoformat(), contact=config.contact)
        folder = packmod.safe_filename(pack_name)
        workdir = tempfile.mkdtemp()
        fd, zpath = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            root = Path(workdir) / folder
            packmod.build_pack(tracks, root, meta, cover_src=config.cover_src,
                               preview=preview, preview_seconds=preview_seconds)
            packmod.zip_pack(root, Path(zpath))
        except Exception:
            if os.path.exists(zpath):
                os.unlink(zpath)
            raise
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return FileResponse(
            zpath, media_type="application/zip", filename=f"{folder}.zip",
            background=BackgroundTask(lambda: os.path.exists(zpath) and os.unlink(zpath)),
        )

    @app.post("/api/sent", dependencies=[Depends(guard_origin)])
    def log_send(body: SentBody):
        """Record that a pack (the current genre/month filter) was sent to someone,
        so you never re-send the same beat to the same contact. Logs only."""
        contact = (body.contact or "").strip()
        if not contact or len(contact) > 120 or any(ord(ch) < 32 for ch in contact):
            raise HTTPException(400, "contact required (short, no control chars)")
        c = conn()
        projs = _matching_projects(c, body.genre, body.month)
        if not projs:
            raise HTTPException(404, "no matching tracks to log as sent")
        pack_name = (body.name or body.genre or "Beat Pack").strip()[:80] or "Beat Pack"
        sid = dbmod.record_send(c, contact, pack_name, [(p.path, p.name) for p in projs])
        return {"send_id": sid, "contact": contact, "pack": pack_name, "count": len(projs)}

    @app.get("/api/sends")
    def sends():
        c = conn()
        out = [
            {"contact": r["contact"], "pack": r["pack_name"],
             "sent_at": r["sent_at"], "count": r["n_tracks"]}
            for r in dbmod.list_sends(c)
        ]
        return {"sends": out}

    @app.get("/api/runs")
    def runs():
        out = []
        for j in orgmod.list_runs(config.runs_dir):
            done = sum(1 for a in j.actions if a.status in (orgmod.DONE, orgmod.REVERTED))
            out.append({"run_id": j.run_id, "status": j.status, "moves": done})
        return {"runs": out}

    def _relink(c, journal) -> int:
        moved = 0
        for entry in journal.actions:
            if entry.status == orgmod.DONE:
                dbmod.repath(c, str(entry.action.src), str(entry.action.dest))
                moved += 1
        return moved

    return app
