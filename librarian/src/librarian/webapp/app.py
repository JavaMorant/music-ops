"""FastAPI app: a thin HTTP layer over the librarian engine.

Every handler is a sync ``def`` (FastAPI runs these in a threadpool, so a slow
``rglob`` over a big library never blocks the event loop). The only writes go
through ``apply_plan`` / ``undo_run`` and the inert inbox staging; everything
else is dry-run plan building.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import ai
from ..cleanup import build_cleanup_plan
from ..engine import EngineError, _within, apply_plan, undo_run
from ..inbox import InboxError, build_inbox_plan
from ..journal import DONE, REVERTED, UNDONE, list_runs
from ..metadata import read_meta
from ..organize import OrganizeError, OrganizeSpec, build_organize_plan
from ..paths import AUDIO_EXTS, audio_files, collision_free, norm_key, sanitize_component
from ..planner import build_plan
from ..select import select_actions
from .security import guard_origin
from .state import AppConfig, AppState

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Cap the server-side plan cache so a long session or a request flood can't grow
# memory without bound (each entry is one reviewed Plan awaiting apply).
MAX_CACHED_PLANS = 32


class PlanRequest(BaseModel):
    mode: Literal["plan", "cleanup", "inbox"] = "cleanup"
    organize_by_genre: bool = True
    playlist: str = "New This Week"


class ApplyRequest(BaseModel):
    plan_id: str
    keep_ids: list[int]
    backup: bool = True
    full_backup: bool = False


class UndoRequest(BaseModel):
    run_id: str


class OrganizeRequest(BaseModel):
    instruction: str | None = None
    spec: dict | None = None  # power users / tests can pass a rule-set directly


class IngestRequest(BaseModel):
    folder: str
    apply: bool = False


class SetLabelRequest(BaseModel):
    key: str
    name: str | None = None
    recording: str | None = None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _build(state: AppState, req: PlanRequest):
    """Dispatch a plan-build request to the right engine builder.

    Returns ``(plan, report_md_or_None)``. Raises InboxError (→400) for a bad
    inbox configuration; the engine builders make no filesystem changes.
    """
    cfg = state.config
    if req.mode == "plan":
        return build_plan(cfg.library_root, cfg.rekordbox_xml), None
    if req.mode == "cleanup":
        plan, report = build_cleanup_plan(
            cfg.library_root,
            organize_by_genre=req.organize_by_genre,
            rekordbox_xml=cfg.rekordbox_xml,
        )
        return plan, report.render()
    plan, report = build_inbox_plan(
        cfg.library_root,
        cfg.inbox_dir,
        organize_by_genre=req.organize_by_genre,
        rekordbox_xml=cfg.rekordbox_xml,
        playlist_name=req.playlist,
    )
    return plan, report.render()


def _cache_and_serialize(st: AppState, plan, mode: str, report_md):
    """Cache a freshly-built plan (bounded, single-use) and serialise it to the
    review-table shape the frontend renders. Shared by every plan endpoint so the
    row shape stays identical no matter which builder produced the plan."""
    cfg = st.config
    plan_id = uuid4().hex
    while len(st.plans) >= MAX_CACHED_PLANS:
        st.plans.pop(next(iter(st.plans)))
    st.plans[plan_id] = plan
    rows = [
        {
            "id": i,
            "kind": a.kind,
            "src": str(a.src),
            "dest": str(a.dest),
            "src_rel": _rel(a.src, cfg.library_root),
            "dest_rel": _rel(a.dest, cfg.library_root),
            "reason": a.reason,
        }
        for i, a in enumerate(plan.actions)
    ]
    return {
        "plan_id": plan_id,
        "mode": mode,
        "library_root": str(cfg.library_root),
        "rekordbox_xml": str(cfg.rekordbox_xml) if cfg.rekordbox_xml else None,
        "actions": rows,
        "rekordbox_additions": len(plan.rekordbox_additions or []),
        "rekordbox_redirects": len(plan.location_redirects or {}),
        "report_md": report_md,
    }


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="librarian", version="0.1.0")
    app.state.librarian = AppState(config=config)

    def state(request: Request) -> AppState:
        return request.app.state.librarian

    @app.exception_handler(EngineError)
    def _engine_error(request: Request, exc: EngineError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InboxError)
    def _inbox_error(request: Request, exc: InboxError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/config")
    def get_config(st: AppState = Depends(state)) -> dict:
        cfg = st.config
        return {
            "library_root": str(cfg.library_root),
            "runs_dir": str(cfg.runs_dir),
            "rekordbox_xml": str(cfg.rekordbox_xml) if cfg.rekordbox_xml else None,
            "inbox_dir": str(cfg.inbox_dir),
            "inbox_exists": cfg.inbox_dir.is_dir(),
            "ai_available": ai.is_available(),
            "version": app.version,
        }

    @app.post("/api/plan", dependencies=[Depends(guard_origin)])
    def post_plan(req: PlanRequest, st: AppState = Depends(state)) -> dict:
        plan, report_md = _build(st, req)
        return _cache_and_serialize(st, plan, req.mode, report_md)

    @app.post("/api/organize", dependencies=[Depends(guard_origin)])
    def post_organize(req: OrganizeRequest, st: AppState = Depends(state)) -> dict:
        """Build a plan from a plain-English instruction (AI authors rules; the
        deterministic engine applies them). A ``spec`` may be passed directly to
        replay a saved rule-set with no API call."""
        cfg = st.config
        if req.spec is not None:
            try:
                spec = OrganizeSpec.from_dict(req.spec)
            except OrganizeError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        else:
            if not (req.instruction or "").strip():
                raise HTTPException(status_code=400, detail="give an instruction to organize by")
            if not ai.is_available():
                raise HTTPException(
                    status_code=400,
                    detail="AI not configured — set ANTHROPIC_API_KEY and install the .[ai] extra, or use cleanup mode",
                )
            files = audio_files(cfg.library_root)
            genres = sorted({m.genre for m in (read_meta(p) for p in files) if m.genre})
            try:
                spec = ai.infer_spec(
                    req.instruction, sample_names=[p.name for p in files[:80]], genres=genres
                )
            except ai.AIError as exc:
                raise HTTPException(status_code=502, detail=str(exc))
        try:
            plan, report_md = build_organize_plan(cfg.library_root, spec, cfg.rekordbox_xml)
        except OrganizeError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        resp = _cache_and_serialize(st, plan, "organize", report_md)
        resp["summary"] = spec.summary
        resp["rules"] = [r.to_dict() for r in spec.rules]
        return resp

    @app.post("/api/apply", dependencies=[Depends(guard_origin)])
    def post_apply(req: ApplyRequest, st: AppState = Depends(state)) -> dict:
        cfg = st.config
        # Everything that touches the plan + applies happens under one lock, and
        # the plan is consumed only on success — so two concurrent applies of the
        # same plan_id can't both execute (the second sees it already gone).
        with st.apply_lock:
            plan = st.plans.get(req.plan_id)
            if plan is None:
                raise HTTPException(status_code=409, detail="plan expired — re-scan")
            try:
                subset = select_actions(plan, req.keep_ids)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if not subset.actions:
                raise HTTPException(status_code=400, detail="no rows selected")
            journal = apply_plan(
                subset, cfg.runs_dir, backup=req.backup, full_backup=req.full_backup
            )
            st.plans.pop(req.plan_id, None)  # single-use: consume on success
        applied = sum(1 for a in journal.actions if a.status == DONE)
        return {
            "run_id": journal.run_id,
            "applied": applied,
            "skipped": len(plan.actions) - len(subset.actions),
            "backup": journal.backup,
            "rekordbox_rewritten": bool(journal.rekordbox and journal.rekordbox.get("rewritten")),
            "library_root": str(cfg.library_root),
        }

    @app.get("/api/runs")
    def get_runs(st: AppState = Depends(state)) -> dict:
        runs = []
        for j in list_runs(st.config.runs_dir):
            runs.append(
                {
                    "run_id": j.run_id,
                    "status": j.status,
                    "created": j.created,
                    "n_actions": len(j.actions),
                    "library_root": str(j.library_root),
                    "undoable": j.status != UNDONE,
                }
            )
        return {"runs": runs}

    @app.post("/api/undo", dependencies=[Depends(guard_origin)])
    def post_undo(req: UndoRequest, st: AppState = Depends(state)) -> dict:
        with st.apply_lock:
            # Pass the trusted launch-time root: a forged journal can't relocate
            # a file outside the library the operator actually chose.
            journal = undo_run(req.run_id, st.config.runs_dir, library_root=st.config.library_root)
        reverted = sum(1 for a in journal.actions if a.status == REVERTED)
        return {"run_id": journal.run_id, "status": journal.status, "reverted": reverted}

    @app.post("/api/inbox/upload", dependencies=[Depends(guard_origin)])
    def post_upload(
        files: list[UploadFile] = File(...), st: AppState = Depends(state)
    ) -> dict:
        cfg = st.config
        inbox = cfg.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        saved, rejected = [], []
        reserved: set[str] = set()
        for upload in files:
            name = sanitize_component(Path(upload.filename or "").name)
            if Path(name).suffix.lower() not in AUDIO_EXTS:
                rejected.append({"name": upload.filename, "reason": "not an audio extension"})
                continue
            dest = collision_free(inbox / name, reserved)
            # Never honour a client path; the landing spot must be inside the inbox.
            if not _within(inbox, dest):
                rejected.append({"name": upload.filename, "reason": "rejected path"})
                continue
            reserved.add(norm_key(dest))  # same key collision_free tests against
            written = _stream_to(upload, dest, cfg.max_upload_bytes)
            if written is None:
                rejected.append({"name": upload.filename, "reason": "exceeds size limit"})
                continue
            # Report the actual on-disk name (collision_free may have suffixed it).
            saved.append({"name": dest.name, "bytes": written, "rel": _rel(dest, cfg.library_root)})
        return {"saved": saved, "rejected": rejected, "inbox_dir": str(inbox)}

    @app.get("/api/pulse")
    def get_pulse(last_n: int = 15) -> dict:
        """Library intelligence from the desktop rekordbox DB (master.db)."""
        try:
            from pyrekordbox import Rekordbox6Database
            from .. import pulse
        except ImportError as exc:
            return JSONResponse(status_code=503, content={"detail": f"pyrekordbox not installed: {exc}"})
        try:
            return pulse.pulse_json(Rekordbox6Database(), last_n=last_n)
        except Exception as exc:  # rekordbox not found / locked
            return JSONResponse(status_code=503, content={"detail": f"rekordbox DB unavailable: {exc}"})

    @app.get("/api/pulse/usb")
    def get_pulse_usb(source: str = "all") -> dict:
        """Per-stick play history off mounted CDJ USBs. ``source`` filters the
        export format: all (DL + DL+ combined), DL, or DL+."""
        from .. import pulse_usb, history_archive
        if not pulse_usb.available():
            return JSONResponse(status_code=503, content={"detail": "rekordcrate not installed (cargo install rekordcrate)"})
        vols = list(pulse_usb.find_usbs())
        names = {v.name for v in vols}
        if history_archive.DIR.exists():  # archived sticks that aren't plugged in
            for f in sorted(history_archive.DIR.glob("*.json")):
                if f.stem not in names:
                    vols.append(Path("/Volumes") / f.stem)
        sticks = []
        for vol in vols:
            try:
                sticks.append(pulse_usb.usb_insights(vol, source=source))
            except Exception as exc:
                sticks.append({"name": vol.name, "error": str(exc)})
        return {"sticks": sticks}

    @app.post("/api/pulse/ingest", dependencies=[Depends(guard_origin)])
    def post_ingest(req: IngestRequest, st: AppState = Depends(state)) -> dict:
        """Fold a drop folder into the library: classify + dedupe (always),
        then file + refresh crates if ``apply``. Slow (classify + fingerprint)."""
        from .. import ingest
        drop = Path(req.folder).expanduser()
        if not drop.is_dir():
            return JSONResponse(status_code=400, content={"detail": f"not a folder: {drop}"})
        lib = st.config.library_root
        plan = ingest.plan_ingest(drop, lib)
        out = {"drop": plan["drop"], "total": plan["total"],
               "survivors": len(plan["survivors"]), "dupes": len(plan["dupes"]),
               "by_genre": plan["by_genre"], "by_crate": plan["by_crate"], "applied": None}
        if req.apply and plan["survivors"]:
            out["applied"] = ingest.apply_ingest(lib, plan)
        return out

    @app.get("/api/pulse/sets")
    def get_sets(stick: str = "", limit: int = 40) -> dict:
        """Recent sets off a stick — tracklist in play order, custom name + linked
        recording from the sidecar."""
        from .. import pulse_usb, setlog
        vols = pulse_usb.find_usbs()
        if stick:
            vols = [v for v in vols if v.name == stick] or vols
        if not vols:
            return {"sets": [], "sticks": [], "stick": None}
        log = setlog.load()
        sets = pulse_usb.stick_sets(vols[0], limit=limit)
        for s in sets:
            e = log.get(s["key"], {})
            s["name"] = e.get("name") or s["auto_name"]
            s["recording"] = e.get("recording", "")
        return {"sets": sets, "sticks": [v.name for v in pulse_usb.find_usbs()], "stick": vols[0].name}

    @app.post("/api/pulse/sets", dependencies=[Depends(guard_origin)])
    def post_set(req: SetLabelRequest) -> dict:
        from .. import setlog
        return setlog.save_one(req.key, req.name, req.recording)

    @app.get("/api/pulse/next")
    def get_next(track: str, stick: str = "") -> dict:
        """Set-builder: what you usually play after a track."""
        from .. import pulse_usb
        vols = pulse_usb.find_usbs()
        if stick:
            vols = [v for v in vols if v.name == stick] or vols
        if not vols or not track.strip():
            return {"next": []}
        return {"next": pulse_usb.follows(vols[0], track)}

    @app.get("/api/pulse/sets/report")
    def get_set_report(key: str):
        """A named set as a downloadable markdown report — tracklist + stats."""
        from .. import pulse_usb, setlog
        stick = key.split("|")[0]
        vols = [v for v in pulse_usb.find_usbs() if v.name == stick]
        if not vols:
            return PlainTextResponse("set not found", status_code=404)
        s = next((x for x in pulse_usb.stick_sets(vols[0], limit=300) if x["key"] == key), None)
        if not s:
            return PlainTextResponse("set not found", status_code=404)
        log = setlog.load().get(key, {})
        name = log.get("name") or s["auto_name"]
        bpms = [b for b in s["bpms"] if b]
        md = [f"# {name}", "", f"- **Stick:** {stick} · {s['fmt']} · {s['n']} tracks"]
        if bpms:
            md.append(f"- **BPM:** {min(bpms)}–{max(bpms)}")
        if log.get("recording"):
            md.append(f"- **Recording:** {log['recording']}")
        md += ["", "## Tracklist", ""]
        md += [f"{i}. {t}" for i, t in enumerate(s["tracks"], 1)]
        fname = re.sub(r"[^\w .-]", "_", name)[:60] or "set"
        return PlainTextResponse("\n".join(md) + "\n",
                                 headers={"Content-Disposition": f'attachment; filename="{fname}.md"'})

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


def _stream_to(upload: UploadFile, dest: Path, max_bytes: int) -> int | None:
    """Stream an upload to a temp ``.part`` then atomically rename into ``dest``.

    Returns bytes written, or None (and cleans up) if it exceeds ``max_bytes``.
    The ``.part`` suffix isn't an audio extension, so a dropped connection never
    leaves a half-written file that a later scan would treat as real.
    """
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.part")
    total = 0
    try:
        with tmp.open("wb") as f:
            while chunk := upload.file.read(1 << 20):
                total += len(chunk)
                if total > max_bytes:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    return None
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return total
