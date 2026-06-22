"""The local CRM web app over the pipeline engine.

These tests use FastAPI's TestClient (needs the [web] + [dev] extras). They point
the app's module-level paths at a tmp DB / drafts dir before building the client,
since `outreach web` configures those at launch rather than per request.

The never-sends invariant is pinned globally by test_never_sends.py (it scans all
of src, web.py included); here we add the web-specific corollary: no send route.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from outreach import web  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "o.db")
    monkeypatch.setattr(web, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(web, "PROFILE_PATH", None)
    return TestClient(web.app)


def _add(client, name="Sam", **kw):
    r = client.post("/api/contact", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_pipeline_starts_empty(client):
    data = client.get("/api/pipeline").json()
    assert data["contacts"] == []
    assert "lead" in data["stages"] and "dead" in data["stages"]
    assert data["due_ids"] == []


def test_add_then_appears_in_pipeline_as_due_lead(client):
    cid = _add(client, "Sam", org="Corsica", city="London")
    data = client.get("/api/pipeline").json()
    assert len(data["contacts"]) == 1
    row = data["contacts"][0]
    assert row["id"] == cid and row["name"] == "Sam" and row["stage"] == "lead"
    # a fresh lead is owed first contact today → flagged due
    assert row["due"] is True
    assert cid in data["due_ids"]


def test_add_requires_a_name(client):
    assert client.post("/api/contact", json={"name": "   "}).status_code == 400


def test_log_advances_stage_and_schedules_followup(client):
    cid = _add(client)
    r = client.post(f"/api/contact/{cid}/log",
                    json={"note": "sent intro", "stage": "contacted", "followup_in": 7})
    assert r.status_code == 200
    assert r.json()["stage"] == "contacted"
    assert r.json()["next_followup"]  # a date was scheduled
    detail = client.get(f"/api/contact/{cid}").json()
    assert detail["contact"]["stage"] == "contacted"
    assert detail["touches"][0]["note"] == "sent intro"


def test_log_into_terminal_stage_clears_followup(client):
    cid = _add(client)
    r = client.post(f"/api/contact/{cid}/log", json={"note": "no fit", "stage": "dead"})
    assert r.json()["next_followup"] is None


def test_stage_move_does_not_log_a_touch(client):
    cid = _add(client)
    client.post(f"/api/contact/{cid}/stage", json={"stage": "replied"})
    detail = client.get(f"/api/contact/{cid}").json()
    assert detail["contact"]["stage"] == "replied"
    assert detail["touches"] == []  # drag-drop is a move, not a touch


def test_unknown_stage_is_rejected(client):
    cid = _add(client)
    assert client.post(f"/api/contact/{cid}/stage", json={"stage": "nope"}).status_code == 400


def test_draft_writes_a_file_and_reports_unfilled(client, tmp_path):
    cid = _add(client, "Sam", org="Corsica")
    r = client.post(f"/api/contact/{cid}/draft", json={"template": "cold"})
    assert r.status_code == 200
    d = r.json()
    # the file really landed on disk in the configured drafts dir
    written = list((tmp_path / "drafts").glob("*.md"))
    assert len(written) == 1
    assert d["path"] == str(written[0])
    # with no profile loaded, the sender slots surface as FILL markers
    assert d["unfilled"]
    assert "Subject:" in d["text"]


def test_draft_listing_and_readback(client, tmp_path):
    cid = _add(client)
    written = client.post(f"/api/contact/{cid}/draft", json={"template": "cold"}).json()["path"]
    listing = client.get("/api/drafts").json()
    assert len(listing["drafts"]) == 1
    back = client.get("/api/draft", params={"path": written}).json()
    assert "Subject:" in back["text"]


def test_draft_readback_is_confined_to_the_drafts_dir(client):
    # path traversal outside the drafts dir is refused
    r = client.get("/api/draft", params={"path": "/etc/passwd"})
    assert r.status_code == 403


def test_no_send_route_exists(client):
    paths = {route.path for route in web.app.routes}
    assert not any("send" in p.lower() for p in paths)
