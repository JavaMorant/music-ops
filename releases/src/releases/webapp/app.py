"""FastAPI app: a thin HTTP layer over the releases engine.

Handlers are sync ``def`` (FastAPI runs them in a threadpool, so a slow walk
never blocks the loop) and each opens its own sqlite connection (connections
aren't shared across threads). The only writes go through ``db.set_marks``
(index-only) and ``organize.apply_plan`` / ``undo_run`` — no new mutation path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .. import db as dbmod
from .. import organize as orgmod
from ..model import MASTER_STATES, MIX_STATES
from .security import guard_origin
from .state import AppConfig, AppState

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class MarkBody(BaseModel):
    id: str
    genre: str | None = None
    mix: str | None = None
    master: str | None = None


class ApplyBody(BaseModel):
    plan_id: str


class UndoBody(BaseModel):
    run_id: str


def _tid(abspath: str) -> str:
    return hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:16]


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="releases — Track List")
    state = AppState(config=config)

    def conn():
        return dbmod.connect(config.db_path)

    def _track_dict(p) -> dict:
        path = Path(p.path)
        try:
            rel = path.relative_to(config.track_list_dir)
            parts = rel.parts
        except ValueError:
            parts = ()
        return {
            "id": _tid(p.path),
            "name": p.name,
            "location": "/".join(parts[:-1]) if len(parts) > 1 else "(loose)",
            "filed": len(parts) == 4,  # <genre>/<mix>/<master>/<name>
            "genre": p.effective_genre,
            "genre_marked": p.genre_manual is not None,
            "mix": p.effective_mix,
            "master": p.effective_master,
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
        if not orgmod._within(config.track_list_dir, p) or not p.is_file():
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
        for p in dbmod.all_projects(c):
            path = Path(p.path)
            if not orgmod._within(tl, path) or not path.is_file():
                continue
            if path.suffix.lower() not in orgmod.AUDIO_EXTS:
                continue
            mapping[_tid(p.path)] = p.path
            tracks.append(_track_dict(p))
        state.track_paths = mapping
        tracks.sort(key=lambda t: (t["genre"], t["name"].lower()))
        genres = sorted({t["genre"] for t in tracks if t["genre"] != "unknown"})
        return {"tracks": tracks, "genres": genres}  # no abs library path leaked to the page

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
        genre = body.genre.strip() if body.genre is not None else None
        dbmod.set_marks(c, abspath, genre=genre, mix=body.mix, master=body.master)
        rows = c.execute("SELECT * FROM projects WHERE path = ?", (abspath,)).fetchall()
        if not rows:
            raise HTTPException(404, "track not in index")
        return _track_dict(dbmod._row_to_project(rows[0]))

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
