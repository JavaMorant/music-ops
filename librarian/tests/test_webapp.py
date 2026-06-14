"""API tests for the web app — proving it inherits the engine's invariants:
dry-run plan, subset-apply that drops an unticked import's rekordbox addition,
byte-for-byte undo, the origin guard, and safe inbox upload."""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from librarian.rekordbox import location_to_path, path_to_location
from librarian.webapp.app import create_app
from librarian.webapp.state import AppConfig

from conftest import make_library, tree_digest


def _client(tmp_path: Path, *, rekordbox_xml: Path | None = None) -> tuple[TestClient, Path]:
    root = tmp_path / "lib"
    root.mkdir(exist_ok=True)
    cfg = AppConfig(
        library_root=root,
        runs_dir=tmp_path / "runs",
        rekordbox_xml=rekordbox_xml,
    )
    # base_url host 127.0.0.1 satisfies the origin/host guard.
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    return client, root


def test_config(tmp_path: Path):
    client, root = _client(tmp_path)
    r = client.get("/api/config").json()
    assert Path(r["library_root"]) == root.resolve()
    assert r["inbox_dir"].endswith("/Inbox")


def test_plan_is_dry_run_no_mutation(tmp_path: Path):
    client, root = _client(tmp_path)
    make_library(root, ["Track_spotdown.org.mp3", "Clean.mp3"])
    before = tree_digest(root)
    r = client.post("/api/plan", json={"mode": "plan"}).json()
    assert r["plan_id"] and any(a["kind"] == "move" for a in r["actions"])
    assert tree_digest(root) == before, "scanning must change nothing"


def test_apply_subset_drops_unticked_rekordbox_addition(tmp_path: Path, monkeypatch):
    """The brief's required test: untick one import row and its rekordbox TRACK
    must NOT be added (no phantom collection entry)."""
    from librarian import inbox as inbox_mod
    from librarian.metadata import TrackMeta

    root = tmp_path / "lib"
    inbox = root / "Inbox"
    inbox.mkdir(parents=True)
    existing = root / "Existing.mp3"
    existing.write_bytes(b"already")
    (inbox / "keep.mp3").write_bytes(b"new-keep")
    (inbox / "drop.mp3").write_bytes(b"new-drop")
    xml = tmp_path / "rb.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<DJ_PLAYLISTS Version="1.0.0">\n'
        '  <PRODUCT Name="rekordbox" Version="6"/>\n'
        f'  <COLLECTION Entries="1"><TRACK TrackID="1" Name="e" Location="{path_to_location(existing)}"/></COLLECTION>\n'
        '  <PLAYLISTS><NODE Name="ROOT" Type="0" Count="0"/></PLAYLISTS>\n</DJ_PLAYLISTS>\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(inbox_mod, "read_meta", lambda p: TrackMeta(
        path=p, artist="A", title=p.stem, genre="House"))

    cfg = AppConfig(library_root=root, runs_dir=tmp_path / "runs", rekordbox_xml=xml)
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")

    plan = client.post("/api/plan", json={"mode": "inbox"}).json()
    moves = [a for a in plan["actions"] if a["kind"] == "move"]
    assert len(moves) == 2
    keep_row = next(a for a in moves if "keep" in a["src_rel"])
    drop_row = next(a for a in moves if "drop" in a["src_rel"])

    res = client.post("/api/apply", json={"plan_id": plan["plan_id"], "keep_ids": [keep_row["id"]]})
    assert res.status_code == 200, res.text
    assert res.json()["applied"] == 1 and res.json()["skipped"] == 1

    # keep.mp3 imported; drop.mp3 still in the inbox.
    assert (inbox / "drop.mp3").exists()
    assert not (inbox / "keep.mp3").exists()
    # The XML has the kept track but NO TRACK for the dropped import's dest.
    locs = [t.get("Location") for t in ET.parse(xml).getroot().find("COLLECTION").findall("TRACK")]
    paths = {location_to_path(l) for l in locs if l}
    assert root / "House" / "A - keep.mp3" in paths
    assert root / "House" / "A - drop.mp3" not in paths, "no phantom track for an unticked import"


def test_apply_then_undo_byte_for_byte(tmp_path: Path):
    client, root = _client(tmp_path)
    make_library(root, ["A_spotdown.org.mp3", "B_spotdown.org.mp3", "Clean.mp3"])
    before = tree_digest(root)
    plan = client.post("/api/plan", json={"mode": "plan"}).json()
    res = client.post("/api/apply", json={"plan_id": plan["plan_id"],
                                          "keep_ids": [a["id"] for a in plan["actions"]]}).json()
    after = tree_digest(root)
    assert after != before
    client.post("/api/undo", json={"run_id": res["run_id"]})
    # The library tree (ignoring the runs/backup dir) is restored exactly.
    assert tree_digest(root, ignore={".librarian"}) == before


def test_apply_empty_selection_400(tmp_path: Path):
    client, root = _client(tmp_path)
    make_library(root, ["X_spotdown.org.mp3"])
    plan = client.post("/api/plan", json={"mode": "plan"}).json()
    res = client.post("/api/apply", json={"plan_id": plan["plan_id"], "keep_ids": []})
    assert res.status_code == 400


def test_apply_expired_plan_409(tmp_path: Path):
    client, root = _client(tmp_path)
    res = client.post("/api/apply", json={"plan_id": "deadbeef", "keep_ids": [0]})
    assert res.status_code == 409


def test_origin_guard_blocks_cross_origin_post(tmp_path: Path):
    client, root = _client(tmp_path)
    res = client.post("/api/apply", json={"plan_id": "x", "keep_ids": [0]},
                      headers={"Origin": "http://evil.example.com"})
    assert res.status_code == 403


def test_upload_stages_audio_rejects_nonaudio(tmp_path: Path):
    client, root = _client(tmp_path)
    files = [
        ("files", ("track.mp3", b"audio-bytes", "audio/mpeg")),
        ("files", ("notes.txt", b"nope", "text/plain")),
    ]
    r = client.post("/api/inbox/upload", files=files).json()
    assert [s["name"] for s in r["saved"]] == ["track.mp3"]
    assert r["rejected"][0]["name"] == "notes.txt"
    assert (root / "Inbox" / "track.mp3").read_bytes() == b"audio-bytes"


def test_undo_unknown_run_409(tmp_path: Path):
    client, root = _client(tmp_path)
    res = client.post("/api/undo", json={"run_id": "nope"})
    assert res.status_code == 409


def test_undo_run_id_traversal_rejected(tmp_path: Path):
    """A crafted run_id must not load a journal outside the runs dir or move
    files anywhere (the critical path-traversal finding)."""
    client, root = _client(tmp_path)
    runs_dir = tmp_path / "runs"
    # Plant a malicious journal as a SIBLING of runs_dir, with a done action that
    # would relocate an in-library file to outside the root.
    evil = tmp_path / "evil_run"
    evil.mkdir()
    secret = root / "secret.mp3"
    secret.write_bytes(b"mine")
    outside = tmp_path / "exfil" / "stolen.mp3"
    (evil / "journal.json").write_text(
        '{"run_id":"evil_run","created":"x","status":"applied",'
        f'"library_root":"{root}","rekordbox":null,"backup":null,'
        f'"actions":[{{"kind":"move","src":"{outside}","dest":"{secret}","reason":"x","status":"done"}}]}}',
        encoding="utf-8",
    )
    res = client.post("/api/undo", json={"run_id": "../evil_run"})
    assert res.status_code == 409
    assert secret.exists() and not outside.exists(), "no file may move outside the library"


def test_undo_rejects_journal_with_action_outside_root(tmp_path: Path):
    """Even a journal living in the right place must not move files outside its
    own library root (containment re-checked on undo)."""
    client, root = _client(tmp_path)
    runs_dir = tmp_path / "runs"
    run = runs_dir / "20260614-000000-abcdef"
    run.mkdir(parents=True)
    inside = root / "track.mp3"
    inside.write_bytes(b"x")
    outside = tmp_path / "elsewhere.mp3"
    (run / "journal.json").write_text(
        '{"run_id":"20260614-000000-abcdef","created":"x","status":"applied",'
        f'"library_root":"{root}","rekordbox":null,"backup":null,'
        f'"actions":[{{"kind":"move","src":"{outside}","dest":"{inside}","reason":"x","status":"done"}}]}}',
        encoding="utf-8",
    )
    res = client.post("/api/undo", json={"run_id": "20260614-000000-abcdef"})
    assert res.status_code == 409
    assert inside.exists() and not outside.exists()


def test_undo_rejects_forged_journal_claiming_parent_root(tmp_path: Path):
    """A forged journal that declares a *parent* library_root (to slip its
    containment check) must still be refused — the web app checks against its own
    trusted launch-time root, not the journal's."""
    client, root = _client(tmp_path)  # cfg.library_root == tmp_path/lib
    runs_dir = tmp_path / "runs"
    run = runs_dir / "20260614-000000-forged"
    run.mkdir(parents=True)
    inside = root / "track.mp3"
    inside.write_bytes(b"x")
    outside = tmp_path / "exfil.mp3"
    # library_root is the PARENT (tmp_path), so the action looks "contained" to a
    # check that trusts the journal — but the server trusts cfg.library_root.
    (run / "journal.json").write_text(
        '{"run_id":"20260614-000000-forged","created":"x","status":"applied",'
        f'"library_root":"{tmp_path}","rekordbox":null,"backup":null,'
        f'"actions":[{{"kind":"move","src":"{outside}","dest":"{inside}","reason":"x","status":"done"}}]}}',
        encoding="utf-8",
    )
    res = client.post("/api/undo", json={"run_id": "20260614-000000-forged"})
    assert res.status_code == 409
    assert inside.exists() and not outside.exists()


def test_runs_survives_wrong_shape_journal(tmp_path: Path):
    """Valid JSON but the wrong shape (e.g. actions not a list) must be skipped,
    not 500 the listing."""
    client, root = _client(tmp_path)
    bad = tmp_path / "runs" / "wrong_shape"
    bad.mkdir(parents=True)
    (bad / "journal.json").write_text('{"run_id":"x","status":"applied","actions":"oops"}', encoding="utf-8")
    r = client.get("/api/runs")
    assert r.status_code == 200 and r.json()["runs"] == []


def test_plan_endpoint_has_origin_guard(tmp_path: Path):
    client, root = _client(tmp_path)
    res = client.post("/api/plan", json={"mode": "plan"},
                      headers={"Origin": "http://evil.example.com"})
    assert res.status_code == 403


def test_plan_id_is_single_use(tmp_path: Path):
    """Re-applying the same plan_id after a successful apply returns 409, so a
    stale browser tab can't replay the moves."""
    client, root = _client(tmp_path)
    make_library(root, ["A_spotdown.org.mp3"])
    plan = client.post("/api/plan", json={"mode": "plan"}).json()
    ids = [a["id"] for a in plan["actions"]]
    assert client.post("/api/apply", json={"plan_id": plan["plan_id"], "keep_ids": ids}).status_code == 200
    again = client.post("/api/apply", json={"plan_id": plan["plan_id"], "keep_ids": ids})
    assert again.status_code == 409


def test_upload_rejects_oversize_and_leaves_no_part(tmp_path: Path):
    root = tmp_path / "lib"
    root.mkdir()
    cfg = AppConfig(library_root=root, runs_dir=tmp_path / "runs", max_upload_bytes=8)
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    r = client.post("/api/inbox/upload",
                    files=[("files", ("big.mp3", b"way-too-many-bytes", "audio/mpeg"))]).json()
    assert r["saved"] == [] and r["rejected"][0]["reason"] == "exceeds size limit"
    # No leftover .part and no real file in the inbox.
    assert not list((root / "Inbox").glob("*.part")) if (root / "Inbox").exists() else True
    assert not (root / "Inbox" / "big.mp3").exists() if (root / "Inbox").exists() else True


def test_static_ui_is_served(tmp_path: Path):
    client, root = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "<title>librarian" in r.text


def test_runs_survives_a_corrupt_journal(tmp_path: Path):
    client, root = _client(tmp_path)
    runs_dir = tmp_path / "runs"
    bad = runs_dir / "bad_run"
    bad.mkdir(parents=True)
    (bad / "journal.json").write_text("{ not json", encoding="utf-8")
    r = client.get("/api/runs")
    assert r.status_code == 200 and r.json()["runs"] == []


def test_apply_undo_byte_for_byte_with_rekordbox(tmp_path: Path):
    """Byte-for-byte undo through HTTP including the rekordbox XML restore."""
    root = tmp_path / "lib"
    root.mkdir()
    make_library(root, ["A_spotdown.org.mp3", "B_spotdown.org.mp3"])
    xml = tmp_path / "rb.xml"
    tracked = root / "A_spotdown.org.mp3"
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<DJ_PLAYLISTS Version="1.0.0">\n'
        '  <PRODUCT Name="rekordbox" Version="6"/>\n'
        f'  <COLLECTION Entries="1"><TRACK TrackID="1" Name="a" Location="{path_to_location(tracked)}"/></COLLECTION>\n'
        '  <PLAYLISTS><NODE Name="ROOT" Type="0" Count="0"/></PLAYLISTS>\n</DJ_PLAYLISTS>\n',
        encoding="utf-8",
    )
    xml_before = xml.read_bytes()
    cfg = AppConfig(library_root=root, runs_dir=tmp_path / "runs", rekordbox_xml=xml)
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    plan = client.post("/api/plan", json={"mode": "plan"}).json()
    res = client.post("/api/apply", json={"plan_id": plan["plan_id"],
                                          "keep_ids": [a["id"] for a in plan["actions"]]}).json()
    assert res["rekordbox_rewritten"] is True
    assert xml.read_bytes() != xml_before  # actually rewrote it
    client.post("/api/undo", json={"run_id": res["run_id"]})
    assert xml.read_bytes() == xml_before, "undo restores the rekordbox XML byte-for-byte"
