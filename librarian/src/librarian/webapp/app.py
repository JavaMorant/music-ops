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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


class OrganiseRequest(BaseModel):
    folder: str = ""  # blank = the configured library root


class RootRequest(BaseModel):
    path: str


class DedupeDecision(BaseModel):
    keep: str
    drop: list[str]


class DedupeApplyRequest(BaseModel):
    decisions: list[DedupeDecision]
    backup: bool = True


class SetLabelRequest(BaseModel):
    key: str
    name: str | None = None
    recording: str | None = None


class SetplanRequest(BaseModel):
    minutes: int = Field(90, gt=0, le=720)            # cap: longest realistic set (12h)
    journey: str = ""
    arc: str = "peak"
    freshness: float = Field(0.3, ge=0.0, le=1.0)
    harmonic: Literal["strict", "loose", "off"] = "loose"
    must: list[str] = []
    avoid: list[str] = []
    opener: str | None = None
    bpm_range: str = ""
    allow_low_bitrate: bool = False
    catalogue_only: bool = True
    tracks_per_hour: int = Field(20, gt=0, le=60)
    beam: int = Field(8, ge=1, le=32)
    seed: int | None = None


class RerollRequest(BaseModel):
    locks: dict[int, str] = {}      # slot index -> candidate path to pin
    from_slot: int | None = None    # additionally pin every slot before this
    seed: int | None = None


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

    def _has_rb(p: Path) -> bool:
        """Does this folder carry a CDJ-stick rekordbox export (play history)?"""
        rb = p / "PIONEER" / "rekordbox"
        return (rb / "export.pdb").exists() or (rb / "exportLibrary.db").exists()

    @app.get("/api/roots")
    def get_roots(st: AppState = Depends(state)) -> dict:
        """Where librarian can work: the current root, mounted USB sticks, and a
        few common music folders. ``rekordbox`` flags a stick whose play history
        pulse can read — the UI redirects those to Pulse for 'what I play most'."""
        cfg = st.config
        home = Path.home()
        cur = os.path.abspath(cfg.library_root)
        out: list[dict] = []
        seen: set[str] = set()

        def add(p: Path, kind: str, label: str | None = None) -> None:
            ap = os.path.abspath(Path(p).expanduser())
            if ap in seen or not Path(ap).is_dir():
                return
            seen.add(ap)
            out.append({"path": ap, "label": label or ap.replace(str(home), "~"),
                        "kind": kind, "rekordbox": _has_rb(Path(ap))})

        add(cfg.library_root, "current")
        vols = Path("/Volumes")
        if vols.is_dir():
            for v in sorted(vols.iterdir()):
                # skip symlinks — e.g. "/Volumes/Macintosh HD" is a link to "/",
                # which must never be offered as a working root.
                if v.is_dir() and not v.name.startswith(".") and not v.is_symlink():
                    add(v, "usb", v.name)
        for p in (home / "DJ" / "library", home / "DJ" / "inbox", home / "Music"):
            add(p, "folder")
        return {"current": cur, "roots": out}

    @app.post("/api/root", dependencies=[Depends(guard_origin)])
    def set_root(req: RootRequest, st: AppState = Depends(state)) -> dict:
        """Re-point librarian at another folder/USB. Restricted to the user's home
        tree or /Volumes, and only an existing directory — so a stray request
        can't aim the file engine at the system root."""
        if not req.path.strip():
            raise HTTPException(status_code=400, detail="no folder given")
        ap = os.path.abspath(Path(req.path).expanduser())
        # Validate the REAL target (symlinks resolved): abspath alone would let a
        # symlink whose name sits under an allowed base but points outside (e.g.
        # /Volumes/Macintosh HD -> /) escape the allowlist and aim the engine at /.
        real = os.path.realpath(ap)
        if not Path(real).is_dir():
            raise HTTPException(status_code=400, detail=f"not a folder: {ap}")
        bases = [os.path.realpath(b) for b in st.config.allowed_root_bases]
        if not any(real.startswith(b + os.sep) for b in bases):
            raise HTTPException(status_code=403,
                                detail="folder must be under your home directory or /Volumes")
        with st.apply_lock:
            st.config.library_root = Path(ap)
            st.config.inbox_dir = Path(ap) / "Inbox"
            st.plans.clear()      # plans/dedupe were for the old root
            st.dedupe = None
        return {"library_root": ap, "rekordbox": _has_rb(Path(ap))}

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
            "rekordbox_usb": _has_rb(cfg.library_root),  # file moves are blocked here
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

    _RB_USB_BLOCK = ("This is a rekordbox USB — moving files here would desync its "
                     "export database and the CDJ would lose the tracks. Use Pulse to "
                     "organise it by play history instead (it never moves files).")

    _INGEST_APPLY_DISABLED = (
        "Ingest 'apply' is disabled here: it relocated, retagged and quarantined "
        "files with raw os.rename — no plan review, no undo journal, no backup — "
        "bypassing every safety guarantee. Use the journaled path instead: "
        "`librarian inbox <root>` (dry-run → review → apply → undo). Preview stays "
        "available above.")

    @app.post("/api/apply", dependencies=[Depends(guard_origin)])
    def post_apply(req: ApplyRequest, st: AppState = Depends(state)) -> dict:
        cfg = st.config
        if _has_rb(cfg.library_root):
            raise HTTPException(status_code=409, detail=_RB_USB_BLOCK)
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
            st.dedupe = None  # files moved/renamed — invalidate the cached dedupe analysis
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
    def get_pulse_usb(source: str = "all", last_n: int = 50) -> dict:
        """Per-stick play history off mounted CDJ USBs. ``source`` filters the
        export format: all (DL + DL+ combined), DL, or DL+. ``last_n`` is the
        recency window (how many recent sets the hot/cold/openers stats use)."""
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
                sticks.append(pulse_usb.usb_insights(vol, last_n=last_n, source=source))
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
        if req.apply:
            # The apply path bypasses the engine (raw os.rename, no journal/undo/
            # backup) — refuse it. Preview (dry-run) below is safe.
            raise HTTPException(status_code=409, detail=_INGEST_APPLY_DISABLED)
        lib = st.config.library_root
        plan = ingest.plan_ingest(drop, lib)
        out = {"drop": plan["drop"], "total": plan["total"],
               "survivors": len(plan["survivors"]), "dupes": len(plan["dupes"]),
               "by_genre": plan["by_genre"], "by_crate": plan["by_crate"], "applied": None}
        return out

    @app.post("/api/organise", dependencies=[Depends(guard_origin)])
    def post_organise(req: OrganiseRequest, st: AppState = Depends(state)) -> dict:
        """Organise a library/USB: identify → dedup v2 → classify → reviewable
        plans (dry-run; never applies from the web). Slow. With an AcoustID key
        it identifies + classifies; without one it dedups + folder-migrates."""
        import json as _json
        from .. import identity as idmod
        from ..organise import (build_dedup_plan, build_reorg_plan,
                                build_usb_playlists, read_usb_ratings)
        from ..organise import organise as run_organise

        root = Path(req.folder).expanduser() if req.folder else st.config.library_root
        if not root.is_dir():
            return JSONResponse(status_code=400, content={"detail": f"not a folder: {root}"})
        key = idmod.get_api_key()
        is_usb = (root / "PIONEER" / "rekordbox" / "export.pdb").exists()
        res = run_organise(root, key=key, run_ai=bool(key) or is_usb)
        out = root / "organise-run"
        out.mkdir(parents=True, exist_ok=True)
        base = {"root": str(res.root), "total": res.total, "used_ai": res.used_ai,
                "identity": "AcoustID" if key else "tags/filename (no key)",
                "dedup": res.dedup, "out": str(out)}
        if is_usb:
            summ = build_usb_playlists(res, out, ratings=read_usb_ratings(root))
            return {**base, "target": "usb", "playlists": summ["playlists"],
                    "by_bucket": summ["by_bucket"]}
        dplan = build_dedup_plan(res.root, res.dup_groups, rekordbox_xml=st.config.rekordbox_xml)
        rplan = build_reorg_plan(res, rekordbox_xml=st.config.rekordbox_xml)
        (out / "dedup-plan.json").write_text(_json.dumps(dplan.to_dict(), indent=2))
        (out / "reorg-plan.json").write_text(_json.dumps(rplan.to_dict(), indent=2))
        return {**base, "target": "library", "reorg_relocations": res.reorg_actions,
                "needs_ai": res.needs_ai}

    @app.get("/api/dedupe/plan")
    def get_dedupe(rescan: int = 0, st: AppState = Depends(state)) -> dict:
        """Metadata-dedupe buckets (auto-merge / manual-review / mixed). Cached;
        pass ``rescan=1`` to rebuild after files change."""
        from .. import dedupe
        if st.dedupe is None or rescan:
            pl = dedupe.rekordbox_playlists_by_path()
            st.dedupe = dedupe.analyze(st.config.library_root, pl_map=pl)
        return st.dedupe

    @app.post("/api/dedupe/apply", dependencies=[Depends(guard_origin)])
    def post_dedupe_apply(req: DedupeApplyRequest, st: AppState = Depends(state)) -> dict:
        """Quarantine the chosen duplicates (never deleted; rekordbox repointed at
        each kept copy) through the reversible engine. Undo via /api/undo."""
        from .. import dedupe
        cfg = st.config
        if _has_rb(cfg.library_root):
            raise HTTPException(status_code=409, detail=_RB_USB_BLOCK)
        # Constrain client input to the server's own authoritative groups: every
        # keep+drop must belong to a real duplicate group (never a crafted pair).
        if st.dedupe is None:
            pl = dedupe.rekordbox_playlists_by_path()
            st.dedupe = dedupe.analyze(cfg.library_root, pl_map=pl)
        decisions = dedupe.valid_decisions(st.dedupe, [d.model_dump() for d in req.decisions])
        plan = dedupe.build_plan(cfg.library_root, decisions, rekordbox_xml=cfg.rekordbox_xml)
        if not plan.actions:
            raise HTTPException(status_code=400, detail="no valid duplicate groups selected")
        with st.apply_lock:
            journal = apply_plan(plan, cfg.runs_dir, backup=req.backup)
            st.dedupe = None  # files moved — force a rescan next time
        applied = sum(1 for a in journal.actions if a.status == DONE)
        return {
            "run_id": journal.run_id,
            "applied": applied,
            "rekordbox_rewritten": bool(journal.rekordbox and journal.rekordbox.get("rewritten")),
        }

    @app.post("/api/pulse/organise", dependencies=[Depends(guard_origin)])
    def post_pulse_organise(stick: str = "") -> dict:
        """Build play-history crates for a mounted stick and write them as
        rekordbox-importable .m3u8 playlists. Never touches the stick's pdb."""
        from .. import pulse_organise, pulse_usb
        if not pulse_usb.available():
            return JSONResponse(status_code=503, content={"detail": "rekordcrate not installed"})
        vols = pulse_usb.find_usbs()
        if stick:
            vols = [v for v in vols if v.name == stick] or vols
        if not vols:
            return JSONResponse(status_code=400, content={"detail": "no CDJ stick mounted"})
        crates = pulse_usb.usb_crates(vols[0])
        if not crates:
            return JSONResponse(status_code=400, content={"detail": "no play history on this stick yet"})
        return pulse_organise.write_crates(vols[0].name, crates)

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

    @app.get("/api/pulse/sets/card")
    def get_set_card(key: str, download: int = 0):
        """A named set as a self-contained, shareable HTML card — each track links
        out to a Spotify / SoundCloud / Bandcamp search. Opens inline; ``download``
        forces a .html attachment to drop on the EPK site."""
        from .. import pulse_usb, setcard, setlog
        stick = key.split("|")[0]
        vols = [v for v in pulse_usb.find_usbs() if v.name == stick]
        if not vols:
            return PlainTextResponse("set not found (stick not mounted)", status_code=404)
        detail = pulse_usb.set_detail(vols[0], key)
        if not detail:
            return PlainTextResponse("set not found", status_code=404)
        log = setlog.load().get(key, {})
        name = log.get("name") or detail["auto_name"]
        doc = setcard.render_card(detail, name, recording=log.get("recording", ""), stick=stick)
        headers = {}
        if download:
            fname = re.sub(r"[^\w .-]", "_", name)[:60] or "set"
            headers["Content-Disposition"] = f'attachment; filename="{fname}.html"'
        return HTMLResponse(doc, headers=headers)

    def _setplan_pool(st: AppState):
        """Tag pool (cached) + the play-history follow-graph off any mounted
        sticks. Sessions are best-effort — a stick read failure never blocks
        set building, it just loses the history-weighted scoring."""
        from ..setplan import store
        sessions: list[list[str]] = []
        try:
            from .. import pulse_usb
            for vol in pulse_usb.find_usbs():
                sess, _m, _f = pulse_usb.read_stick(vol)
                sessions.extend(s["tracks"] for s in sess if s.get("tracks"))
        except Exception:
            pass
        cache = st.config.runs_dir / "setplan-pool.json"
        return store.load_pool_cached(st.config.library_root, cache, sessions=sessions)

    def _load_setplan(plan_id: str, st: AppState) -> dict:
        from ..setplan import store
        try:
            d = store.load_plan_dict(plan_id, st.config.runs_dir)
        except ValueError:
            raise HTTPException(400, "bad plan id")
        if d is None:
            raise HTTPException(404, "no such plan")
        return d

    @app.post("/api/setplan", dependencies=[Depends(guard_origin)])
    def post_setplan(req: SetplanRequest, st: AppState = Depends(state)) -> dict:
        from ..setplan import store
        from ..setplan.search import build_set
        from ..setplan.spec import ARC_NAMES, GigSpec, parse_journey
        if req.arc not in ARC_NAMES:
            raise HTTPException(400, f"unknown arc {req.arc!r}")
        try:
            journey = parse_journey(req.journey)
            lo_hi = None
            if req.bpm_range.strip():
                lo, hi = (int(x) for x in req.bpm_range.split("-", 1))
                if lo > hi:
                    raise ValueError
                lo_hi = (lo, hi)
        except ValueError:
            raise HTTPException(400, "bad journey or bpm_range")
        spec = GigSpec(minutes=req.minutes, journey=journey, arc=req.arc,
                       freshness=req.freshness, harmonic=req.harmonic,
                       must_play=req.must, avoid=req.avoid, opener=req.opener,
                       bpm_range=lo_hi, allow_low_bitrate=req.allow_low_bitrate,
                       tracks_per_hour=req.tracks_per_hour, seed=req.seed,
                       catalogue_only=req.catalogue_only)
        cands, follows = _setplan_pool(st)
        plan = build_set(cands, spec, follows=follows, beam_width=max(1, req.beam))
        pid = store.save_plan(plan, st.config.runs_dir)
        return {"plan": store.plan_to_dict(plan, pid)}

    @app.get("/api/setplan/{plan_id}")
    def get_setplan(plan_id: str, st: AppState = Depends(state)) -> dict:
        return _load_setplan(plan_id, st)

    @app.post("/api/setplan/{plan_id}/reroll", dependencies=[Depends(guard_origin)])
    def post_setplan_reroll(plan_id: str, req: RerollRequest,
                            st: AppState = Depends(state)) -> dict:
        from ..setplan import store
        from ..setplan.search import build_set
        d = _load_setplan(plan_id, st)
        spec = store.spec_from_dict(d["spec"])
        spec.seed = req.seed if req.seed is not None else ((spec.seed or 0) + 1)
        pins = {int(k): v for k, v in req.locks.items()}
        if req.from_slot is not None:
            for s in d["slots"]:
                if s["index"] < req.from_slot:
                    pins.setdefault(s["index"], s["candidate"]["path"])
        cands, follows = _setplan_pool(st)
        plan = build_set(cands, spec, follows=follows, beam_width=8, pinned=pins)
        store.save_plan(plan, st.config.runs_dir, plan_id=plan_id)
        return {"plan": store.plan_to_dict(plan, plan_id)}

    @app.get("/api/setplan/{plan_id}/export")
    def get_setplan_export(plan_id: str, fmt: Literal["m3u8", "md", "xml"],
                           st: AppState = Depends(state)):
        d = _load_setplan(plan_id, st)
        lines_m3u, lines_md = [], []
        if fmt == "m3u8":
            lines_m3u.append("#EXTM3U")
            for s in d["slots"]:
                c = s["candidate"]
                secs = int(c["length_s"] or -1)
                lines_m3u.append(f"#EXTINF:{secs},{c['artist']} - {c['title']}")
                lines_m3u.append(c["path"])
            return PlainTextResponse("\n".join(lines_m3u) + "\n", media_type="audio/x-mpegurl",
                                     headers={"Content-Disposition": f'attachment; filename="{plan_id}.m3u8"'})
        if fmt == "md":
            for s in d["slots"]:
                c = s["candidate"]
                key = f"{c['camelot'][0]}{c['camelot'][1]}" if c["camelot"] else "—"
                bpm = int(c["bpm"]) if c["bpm"] else "—"
                lines_md.append(f"{s['index'] + 1:>2}. **{c['artist']} — {c['title']}** · {bpm} BPM · {key}")
                lines_md.append(f"    _{s['reason']}_")
            return PlainTextResponse("\n".join(lines_md) + "\n", media_type="text/markdown",
                                     headers={"Content-Disposition": f'attachment; filename="{plan_id}.md"'})
        if st.config.rekordbox_xml is None:
            raise HTTPException(409, "no rekordbox XML configured (start serve with --rekordbox-xml)")
        from ..setplan.export import to_rekordbox_xml
        from ..setplan import store as _store
        from ..setplan.search import SetPlan, Slot
        # rebuild a minimal SetPlan from the artifact for the exporter
        from ..setplan.pool import Candidate, norm as _norm
        slots = []
        for s in d["slots"]:
            c = s["candidate"]
            slots.append(Slot(index=s["index"], reason=s["reason"], clock_min=s["clock_min"],
                              candidate=Candidate(path=Path(c["path"]), artist=c["artist"] or "",
                                                  title=c["title"], genre=c["genre"] or "",
                                                  bpm=c["bpm"],
                                                  camelot=tuple(c["camelot"]) if c["camelot"] else None,
                                                  length_s=c["length_s"],
                                                  norm_key=_norm(f"{c['artist']} {c['title']}"),
                                                  low_bitrate=False)))
        plan = SetPlan(spec=_store.spec_from_dict(d["spec"]), slots=slots)
        out = st.config.runs_dir / f"setplan-{plan_id}.rekordbox.xml"
        to_rekordbox_xml(plan, st.config.rekordbox_xml, out, f"setplan {plan_id}")
        return PlainTextResponse(out.read_text(encoding="utf-8"), media_type="application/xml",
                                 headers={"Content-Disposition": f'attachment; filename="{plan_id}.rekordbox.xml"'})

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
