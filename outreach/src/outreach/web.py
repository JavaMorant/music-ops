"""Local web UI for outreach — a single-page CRM over the pipeline engine.
Launch with `outreach web` (needs the [web] extra).

The store/drafting engine is imported directly (no shelling out). Every endpoint
maps onto an existing engine call, so the web app inherits the same guarantees as
the CLI — in particular the hard rule: **this never sends.** `draft` writes a
markdown file for you to review and send by hand; there is no send endpoint and
no mail code anywhere (pinned by tests/test_never_sends.py).

Bound to 127.0.0.1: it reads and writes the local pipeline DB and writes draft
files by path, which is fine for a single-user personal tool but is NOT meant to
face a network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import db, drafting, store
from .drafting import DraftError
from .model import STAGES, Contact
from .store import StoreError

_INDEX = Path(__file__).parent / "web_static" / "index.html"

# Configured at launch by `serve()` so requests don't need to carry paths.
DB_PATH = Path("outreach.db")
DRAFTS_DIR = Path("drafts")
PROFILE_PATH: Optional[Path] = None


def get_conn():
    """One connection per request, closed when the request ends.

    SQLite connections aren't safe to share across the threadpool FastAPI runs
    sync endpoints on, and connect() is cheap — so open per request and close in
    the finally so handles don't accumulate over a long-lived session.
    """
    conn = db.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def _row_dict(c, s, today: str) -> dict:
    """Flatten a Contact + PipelineState into the JSON the board renders."""
    due = bool(s.next_followup and s.next_followup <= today)
    return {
        "id": c.id,
        "name": c.name,
        "org": c.org,
        "role": c.role,
        "city": c.city,
        "venue_capacity": c.venue_capacity,
        "genre_fit": c.genre_fit,
        "source": c.source,
        "notes": c.notes,
        "created_at": c.created_at,
        "stage": s.stage,
        "last_touch": s.last_touch,
        "next_followup": s.next_followup,
        "due": due,
    }


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class ContactIn(BaseModel):
    name: str
    org: Optional[str] = None
    role: Optional[str] = None
    city: Optional[str] = None
    venue_capacity: Optional[int] = None
    genre_fit: Optional[str] = None
    source: Optional[str] = None
    notes: Optional[str] = None
    followup_in: int = 0


class LogIn(BaseModel):
    note: str
    channel: Optional[str] = None
    stage: Optional[str] = None
    followup_in: int = store.DEFAULT_FOLLOWUP_DAYS
    followup_on: Optional[str] = None
    no_followup: bool = False


class StageIn(BaseModel):
    stage: str


class DraftIn(BaseModel):
    template: str = drafting.DEFAULT_TEMPLATE


# --------------------------------------------------------------------------- #
# App + endpoints
# --------------------------------------------------------------------------- #

app = FastAPI(title="outreach CRM")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX.read_text(encoding="utf-8")


@app.get("/api/pipeline")
def api_pipeline(conn=Depends(get_conn)) -> dict:
    """The whole pipeline plus the metadata the board needs to render."""
    today = date.today().isoformat()
    rows = [_row_dict(pr.contact, pr.state, today) for pr in store.list_contacts(conn)]
    due_ids = [pr.contact.id for pr in store.due(conn, today=date.today())]
    return {
        "today": today,
        "stages": list(STAGES),
        "terminal": list(store.TERMINAL_STAGES),
        "contacts": rows,
        "due_ids": due_ids,
        "profile_loaded": PROFILE_PATH is not None and PROFILE_PATH.exists(),
    }


@app.post("/api/contact")
def api_add(req: ContactIn, conn=Depends(get_conn)) -> dict:
    try:
        c = store.add_contact(
            conn,
            Contact(
                name=req.name, org=req.org, role=req.role, city=req.city,
                venue_capacity=req.venue_capacity, genre_fit=req.genre_fit,
                source=req.source, notes=req.notes,
            ),
            followup_in=req.followup_in,
        )
    except StoreError as exc:
        raise HTTPException(400, str(exc))
    return {"id": c.id}


@app.get("/api/contact/{contact_id}")
def api_contact(contact_id: int, conn=Depends(get_conn)) -> dict:
    try:
        c = store.get_contact(conn, str(contact_id))
        state = store.get_state(conn, c.id)
    except StoreError as exc:
        raise HTTPException(404, str(exc))
    touches = store.recent_touches(conn, c.id, limit=10)
    today = date.today().isoformat()
    return {
        "contact": _row_dict(c, state, today),
        "touches": [
            {"id": t.id, "ts": t.ts, "channel": t.channel, "note": t.note}
            for t in touches
        ],
    }


@app.post("/api/contact/{contact_id}/log")
def api_log(contact_id: int, req: LogIn, conn=Depends(get_conn)) -> dict:
    try:
        c = store.get_contact(conn, str(contact_id))
        state = store.log_touch(
            conn, c.id, req.note, channel=req.channel, advance_to=req.stage,
            followup_in=req.followup_in, followup_on=req.followup_on,
            no_followup=req.no_followup,
        )
    except StoreError as exc:
        raise HTTPException(400, str(exc))
    return {"stage": state.stage, "next_followup": state.next_followup}


@app.post("/api/contact/{contact_id}/stage")
def api_stage(contact_id: int, req: StageIn, conn=Depends(get_conn)) -> dict:
    """Move a contact to a stage without logging a touch — the board's drag-drop.
    Mirrors `set_stage`: terminal stages clear the follow-up."""
    try:
        c = store.get_contact(conn, str(contact_id))
        state = store.set_stage(conn, c.id, req.stage)
    except StoreError as exc:
        raise HTTPException(400, str(exc))
    return {"stage": state.stage, "next_followup": state.next_followup}


@app.post("/api/contact/{contact_id}/draft")
def api_draft(contact_id: int, req: DraftIn, conn=Depends(get_conn)) -> dict:
    """Render and write a draft FILE, returning a preview. Never sends — the user
    reviews and sends it themselves."""
    try:
        c = store.get_contact(conn, str(contact_id))
        state = store.get_state(conn, c.id)
        touches = store.recent_touches(conn, c.id, limit=3)
        rendered = drafting.render_draft(
            c, template=req.template, state=state, touches=touches,
            profile=drafting.load_profile(PROFILE_PATH if PROFILE_PATH and PROFILE_PATH.exists() else None),
        )
        dest = drafting.write_draft(rendered, DRAFTS_DIR, c, req.template)
    except (StoreError, DraftError) as exc:
        raise HTTPException(400, str(exc))
    return {
        "path": str(dest),
        "subject": rendered.subject,
        "body": rendered.body,
        "text": rendered.text,
        "unfilled": rendered.unfilled,
    }


@app.get("/api/templates")
def api_templates() -> dict:
    names = sorted(t.stem for t in drafting.TEMPLATES_DIR.glob("*.md"))
    return {"templates": names, "default": drafting.DEFAULT_TEMPLATE}


@app.get("/api/drafts")
def api_drafts() -> dict:
    """List draft files already written, newest first."""
    if not DRAFTS_DIR.exists():
        return {"dir": str(DRAFTS_DIR), "drafts": []}
    files = sorted(
        (p for p in DRAFTS_DIR.glob("*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return {
        "dir": str(DRAFTS_DIR),
        "drafts": [{"name": p.name, "path": str(p)} for p in files],
    }


@app.get("/api/draft")
def api_draft_file(path: str) -> dict:
    """Read back a draft file's contents. Constrained to the drafts dir so the
    endpoint can't be coaxed into reading arbitrary files off the disk."""
    p = Path(path).expanduser().resolve()
    root = DRAFTS_DIR.resolve()
    if root not in p.parents and p != root:
        raise HTTPException(403, "outside the drafts directory")
    if not p.is_file():
        raise HTTPException(404, "draft not found")
    return {"name": p.name, "path": str(p), "text": p.read_text(encoding="utf-8")}
