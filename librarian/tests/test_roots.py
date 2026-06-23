"""Working-folder switcher: list roots, switch safely, reject out-of-bounds."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import make_library
from librarian.webapp.app import create_app
from librarian.webapp.state import AppConfig


def _client(tmp_path: Path):
    root = tmp_path / "lib"
    root.mkdir()
    make_library(root, ["a.mp3"])
    (tmp_path / "usb").mkdir()
    make_library(tmp_path / "usb", ["b.mp3"])
    cfg = AppConfig(library_root=root, runs_dir=tmp_path / "runs",
                    allowed_root_bases=[tmp_path])   # confine switching to the tmp tree
    return TestClient(create_app(cfg), base_url="http://127.0.0.1"), root, tmp_path


def test_roots_lists_current(tmp_path: Path):
    client, root, _ = _client(tmp_path)
    d = client.get("/api/roots").json()
    assert d["current"] == str(root)
    assert any(r["path"] == str(root) and r["kind"] == "current" for r in d["roots"])


def test_switch_changes_working_dir(tmp_path: Path):
    client, root, tp = _client(tmp_path)
    target = tp / "usb"
    r = client.post("/api/root", json={"path": str(target)})
    assert r.status_code == 200
    assert Path(r.json()["library_root"]) == target
    assert Path(client.get("/api/config").json()["library_root"]) == target


def test_outside_allowed_is_blocked(tmp_path: Path):
    client, root, _ = _client(tmp_path)
    r = client.post("/api/root", json={"path": "/etc"})
    assert r.status_code == 403
    assert Path(client.get("/api/config").json()["library_root"]) == root  # unchanged


def test_traversal_outside_base_is_blocked(tmp_path: Path):
    client, root, tp = _client(tmp_path)
    # ../ climbs above the allowed base -> rejected, working dir unchanged
    r = client.post("/api/root", json={"path": str(tp / ".." / "escape")})
    assert r.status_code in (400, 403)
    assert Path(client.get("/api/config").json()["library_root"]) == root


def test_nonexistent_is_400(tmp_path: Path):
    client, root, tp = _client(tmp_path)
    assert client.post("/api/root", json={"path": str(tp / "nope")}).status_code == 400


def test_empty_path_is_400(tmp_path: Path):
    client, root, _ = _client(tmp_path)
    assert client.post("/api/root", json={"path": "   "}).status_code == 400


def test_symlink_escaping_base_is_blocked(tmp_path: Path):
    """A symlink whose NAME is under the allowed base but whose TARGET is outside
    (the /Volumes/Macintosh HD -> / class of bug) must be rejected."""
    import os
    client, root, tp = _client(tmp_path)
    link = tp / "sneaky"
    os.symlink("/etc", link)                 # name under base, real target = /etc
    r = client.post("/api/root", json={"path": str(link)})
    assert r.status_code == 403
    assert Path(client.get("/api/config").json()["library_root"]) == root  # unchanged


def test_rekordbox_flag_on_switch(tmp_path: Path):
    client, root, tp = _client(tmp_path)
    usb = tp / "usb"
    (usb / "PIONEER" / "rekordbox").mkdir(parents=True)
    (usb / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(b"x")
    assert client.post("/api/root", json={"path": str(usb)}).json()["rekordbox"] is True
