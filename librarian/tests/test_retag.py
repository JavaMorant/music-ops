"""The reversible tag-writing engine + retag builder.

The never-corrupt invariant is the point: applying a tag repair and then undoing
it restores every file's tags to *exactly* what they were (including deleting a
tag that was absent before). Tag I/O is faked with an in-memory store so these
tests are deterministic and portable; real mutagen I/O is covered in test_tags.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from librarian import tags
from librarian.engine import EngineError, apply_plan, undo_run
from librarian.journal import DONE, REVERTED
from librarian.model import Plan, TagEdit
from librarian.retag import TagProposal, build_retag_plan


@pytest.fixture
def store(monkeypatch):
    """An in-memory stand-in for file tags: {path_str: {field: value}}."""
    data: dict[str, dict[str, str]] = {}

    def read_tags(path, fields):
        cur = data.get(str(path), {})
        return {f: cur.get(f) for f in fields}

    def write_tags(path, fields):
        cur = data.setdefault(str(path), {})
        for f, v in fields.items():
            if v is None:
                cur.pop(f, None)
            else:
                cur[f] = v

    monkeypatch.setattr(tags, "read_tags", read_tags)
    monkeypatch.setattr(tags, "write_tags", write_tags)
    monkeypatch.setattr(tags, "is_taggable", lambda p: True)
    return data


def _file(root: Path, name: str, *, content=b"audio") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    p.write_bytes(content)
    return p


def test_apply_writes_tags_and_journals_old(tmp_path, store):
    root = tmp_path / "lib"
    f = _file(root, "messy.mp3")
    store[str(f)] = {"title": "old title"}  # artist + genre absent
    plan = Plan(library_root=root, actions=[], tag_edits=[
        TagEdit(path=f, fields={"artist": "Burna Boy", "title": "Ye", "genre": "Afrobeats"}, reason="repair")])

    journal = apply_plan(plan, tmp_path / "runs", backup=True)

    assert store[str(f)] == {"artist": "Burna Boy", "title": "Ye", "genre": "Afrobeats"}
    te = journal.tag_edits[0]
    assert te.status == DONE
    # the journal captured exactly what was there before (absent → None)
    assert te.old == {"artist": None, "title": "old title", "genre": None}
    assert f.read_bytes() == b"audio"  # the file itself was never moved/deleted


def test_undo_restores_tags_exactly(tmp_path, store):
    root = tmp_path / "lib"
    f = _file(root, "messy.mp3")
    store[str(f)] = {"title": "old title"}
    plan = Plan(library_root=root, actions=[], tag_edits=[
        TagEdit(path=f, fields={"artist": "Burna Boy", "title": "Ye", "genre": "Afrobeats"})])

    journal = apply_plan(plan, tmp_path / "runs", backup=False)
    undone = undo_run(journal.run_id, tmp_path / "runs")

    # title restored; artist + genre (absent before) deleted again — exact state.
    assert store[str(f)] == {"title": "old title"}
    assert all(t.status == REVERTED for t in undone.tag_edits)


def test_backup_includes_retagged_file(tmp_path, store):
    root = tmp_path / "lib"
    f = _file(root, "track.mp3", content=b"original-bytes")
    plan = Plan(library_root=root, actions=[], tag_edits=[TagEdit(path=f, fields={"title": "X"})])
    journal = apply_plan(plan, tmp_path / "runs", backup=True)
    backup_copy = Path(journal.backup["dir"]) / "track.mp3"
    assert backup_copy.read_bytes() == b"original-bytes"  # snapshot before the write


def test_preflight_rejects_tag_path_outside_root(tmp_path, store):
    root = tmp_path / "lib"
    root.mkdir()
    outside = _file(tmp_path, "outside.mp3")
    plan = Plan(library_root=root, actions=[], tag_edits=[TagEdit(path=outside, fields={"title": "x"})])
    with pytest.raises(EngineError, match="escapes the library root"):
        apply_plan(plan, tmp_path / "runs", backup=False)
    assert "title" not in store.get(str(outside), {})  # nothing written


def test_preflight_rejects_untaggable_file(tmp_path, store, monkeypatch):
    root = tmp_path / "lib"
    f = _file(root, "weird.mp3")
    monkeypatch.setattr(tags, "is_taggable", lambda p: False)
    plan = Plan(library_root=root, actions=[], tag_edits=[TagEdit(path=f, fields={"title": "x"})])
    with pytest.raises(EngineError, match="cannot carry tags"):
        apply_plan(plan, tmp_path / "runs", backup=False)


def test_tagedit_rejects_forbidden_field():
    with pytest.raises(ValueError, match="not writable"):
        TagEdit(path=Path("x.mp3"), fields={"key": "8A"})  # key is never writable


def test_plan_with_tag_edits_roundtrips_through_dict(tmp_path):
    f = tmp_path / "a.mp3"
    plan = Plan(library_root=tmp_path, actions=[],
                tag_edits=[TagEdit(path=f, fields={"artist": "A", "title": "B"}, reason="r")])
    back = Plan.from_dict(plan.to_dict())
    assert back.tag_edits[0].fields == {"artist": "A", "title": "B"}
    assert back.tag_edits[0].path == f


# --- builder --------------------------------------------------------------


def test_build_retag_filters_to_actual_changes(tmp_path, store):
    root = tmp_path / "lib"
    a = _file(root, "a.mp3")
    b = _file(root, "b.mp3")
    store[str(a)] = {"artist": "Avicii", "title": "old"}
    store[str(b)] = {"artist": "Akon", "title": "Lonely"}
    proposals = [
        TagProposal(path=a, fields={"artist": "Avicii", "title": "The Nights"}, confidence="high"),
        TagProposal(path=b, fields={"artist": "Akon", "title": "Lonely"}),  # identical → no change
    ]
    plan, report = build_retag_plan(root, proposals)
    assert len(plan.tag_edits) == 1
    assert plan.tag_edits[0].fields == {"title": "The Nights"}  # only the changed field
    assert "confidence: high" in plan.tag_edits[0].reason
    assert "The Nights" in report


def test_build_retag_skips_empty_proposals(tmp_path, store):
    root = tmp_path / "lib"
    a = _file(root, "a.mp3")
    plan, _ = build_retag_plan(root, [TagProposal(path=a, fields={"artist": "  ", "title": ""})])
    assert plan.tag_edits == []


def test_build_retag_apply_undo_roundtrip(tmp_path, store):
    root = tmp_path / "lib"
    f = _file(root, "B Jack$ ft Zeddy Will - Get Jiggy (Intro Dirty).mp3")
    store[str(f)] = {}  # no tags at all
    proposals = [TagProposal(path=f, fields={"artist": "B Jack$ ft. Zeddy Will", "title": "Get Jiggy"},
                             confidence="high")]
    plan, _ = build_retag_plan(root, proposals)
    journal = apply_plan(plan, tmp_path / "runs", backup=False)
    assert store[str(f)] == {"artist": "B Jack$ ft. Zeddy Will", "title": "Get Jiggy"}
    undo_run(journal.run_id, tmp_path / "runs")
    assert store[str(f)] == {}  # back to untagged
