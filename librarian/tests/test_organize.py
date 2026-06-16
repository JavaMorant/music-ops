"""The NL-organize engine: an OrganizeSpec applied deterministically to a library
produces a reviewable Plan that the same engine applies and undoes. The AI is not
involved here — specs are hand-built — so these tests are fully deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from librarian import organize
from librarian.engine import apply_plan, undo_run
from librarian.metadata import TrackMeta
from librarian.model import MOVE, QUARANTINE
from librarian.organize import OrganizeError, OrganizeSpec, Rule, build_organize_plan
from librarian.paths import QUARANTINE_DIRNAME

from conftest import make_library, tree_digest


def _fake_meta(specs: dict[str, dict]):
    def reader(path: Path) -> TrackMeta:
        return TrackMeta(path=path, **specs.get(path.name, {}))
    return reader


def _plan(root, spec, monkeypatch, metas):
    monkeypatch.setattr(organize, "read_meta", _fake_meta(metas))
    return build_organize_plan(root, spec)


def test_folder_rule_relocates_keeps_name(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["Avicii - The Nights.mp3", "Akon - Lonely.mp3"])
    spec = OrganizeSpec(rules=[Rule(field="artist", op="contains", value="avicii",
                                    action="folder", target="Festival")])
    plan, _ = _plan(root, spec, monkeypatch, {
        "Avicii - The Nights.mp3": dict(artist="Avicii", title="The Nights"),
        "Akon - Lonely.mp3": dict(artist="Akon", title="Lonely"),
    })
    dests = {a.src.name: a.dest for a in plan.actions}
    assert dests == {"Avicii - The Nights.mp3": root / "Festival" / "Avicii - The Nights.mp3"}
    assert plan.actions[0].kind == MOVE


def test_default_by_genre_files_only_tagged(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["a.mp3", "b.mp3"])
    spec = OrganizeSpec(rules=[], default="by_genre")
    plan, _ = _plan(root, spec, monkeypatch, {
        "a.mp3": dict(artist="X", title="Y", genre="Amapiano"),
        "b.mp3": dict(artist="Z", title="W"),  # no genre → left in place
    })
    dests = {a.src.name: a.dest for a in plan.actions}
    assert dests == {"a.mp3": root / "Amapiano" / "a.mp3"}


def test_quarantine_by_filename_marker(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["Chammak Challo_spotdown.org.mp3", "clean.mp3"])
    spec = OrganizeSpec(rules=[Rule(field="filename", op="contains", value="_spotdown.org",
                                    action="quarantine", reason="download-site rip")])
    plan, _ = _plan(root, spec, monkeypatch, {})
    assert len(plan.actions) == 1
    a = plan.actions[0]
    assert a.kind == QUARANTINE
    assert a.src.name == "Chammak Challo_spotdown.org.mp3"
    assert QUARANTINE_DIRNAME in a.dest.parts
    assert a.reason == "download-site rip"


def test_is_low_quality_match(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["lo.mp3", "hi.mp3"])
    spec = OrganizeSpec(rules=[Rule(field="quality", op="is_low_quality", value="",
                                    action="quarantine")])
    plan, _ = _plan(root, spec, monkeypatch, {
        "lo.mp3": dict(bitrate_kbps=128),
        "hi.mp3": dict(bitrate_kbps=320),
    })
    assert [a.src.name for a in plan.actions] == ["lo.mp3"]


def test_first_matching_rule_wins(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["Avicii - X.mp3"])
    spec = OrganizeSpec(rules=[
        Rule(field="artist", op="contains", value="avicii", action="folder", target="Festival"),
        Rule(field="filename", op="any", value="", action="folder", target="Everything"),
    ])
    plan, _ = _plan(root, spec, monkeypatch, {"Avicii - X.mp3": dict(artist="Avicii", title="X")})
    assert plan.actions[0].dest == root / "Festival" / "Avicii - X.mp3"


def test_rename_artist_title_default(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["raw.mp3"])
    spec = OrganizeSpec(rules=[], default="rename_artist_title")
    plan, _ = _plan(root, spec, monkeypatch, {"raw.mp3": dict(artist="Burna Boy", title="Ye")})
    assert plan.actions[0].dest == root / "Burna Boy - Ye.mp3"


def test_collision_free_when_two_map_to_same_dest(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    (root / "A").mkdir(parents=True)
    (root / "B").mkdir()
    (root / "A" / "track.mp3").write_bytes(b"one")
    (root / "B" / "track.mp3").write_bytes(b"two")
    spec = OrganizeSpec(rules=[Rule(field="filename", op="any", value="", action="folder", target="All")])
    plan, _ = _plan(root, spec, monkeypatch, {})
    dests = sorted(a.dest.name for a in plan.actions)
    assert dests == ["track (2).mp3", "track.mp3"]  # second deduped, never clobbered


def test_redirects_track_every_move(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["a.mp3"])
    spec = OrganizeSpec(rules=[Rule(field="filename", op="any", value="", action="folder", target="X")])
    plan, _ = _plan(root, spec, monkeypatch, {})
    assert plan.location_redirects == {a.src: a.dest for a in plan.actions}


def test_apply_then_undo_conserves_files(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["Avicii - X.mp3", "Akon_spotdown.org.mp3", "untouched.mp3"])
    before = tree_digest(root)
    spec = OrganizeSpec(rules=[
        Rule(field="artist", op="contains", value="avicii", action="folder", target="Festival"),
        Rule(field="filename", op="contains", value="_spotdown.org", action="quarantine"),
    ], default="leave")
    plan, _ = _plan(root, spec, monkeypatch, {"Avicii - X.mp3": dict(artist="Avicii", title="X")})
    n_before = sum(1 for _ in root.rglob("*") if _.is_file())

    journal = apply_plan(plan, tmp_path / "runs", backup=False)
    moved = {p.name for p in root.rglob("*") if p.is_file()}
    assert "Avicii - X.mp3" in moved  # still exists, just relocated
    assert sum(1 for _ in root.rglob("*") if _.is_file()) == n_before  # nothing deleted

    undo_run(journal.run_id, tmp_path / "runs")
    assert tree_digest(root) == before  # byte-for-byte back to start


@pytest.mark.parametrize("bad", [
    dict(field="bpm", op="contains", value="x", action="folder"),
    dict(field="artist", op="sounds_like", value="x", action="folder"),
    dict(field="artist", op="contains", value="x", action="delete"),
])
def test_rule_validation_rejects_unknown(bad):
    with pytest.raises(OrganizeError):
        Rule(**bad)


@pytest.mark.parametrize("op", ["contains", "equals", "startswith", "endswith"])
def test_text_op_requires_value(op):
    with pytest.raises(OrganizeError, match="non-empty value"):
        Rule(field="artist", op=op, value="", action="folder", target="X")


def test_any_and_low_quality_allow_empty_value():
    Rule(field="filename", op="any", value="", action="folder", target="X")
    Rule(field="quality", op="is_low_quality", value="", action="quarantine")  # no raise


def test_quality_field_with_text_op_matches_nothing(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["lo.mp3"])
    # low-quality must only trigger on op=is_low_quality, never a text op
    spec = OrganizeSpec(rules=[Rule(field="quality", op="equals", value="low",
                                    action="quarantine")])
    plan, _ = _plan(root, spec, monkeypatch, {"lo.mp3": dict(bitrate_kbps=128)})
    assert plan.actions == []


def test_report_counts_only_actual_moves(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    make_library(root, ["has.mp3", "none.mp3"])
    # a by_genre rule matches both, but only the tagged file actually moves
    spec = OrganizeSpec(rules=[Rule(field="filename", op="any", value="", action="by_genre")])
    plan, report = _plan(root, spec, monkeypatch, {
        "has.mp3": dict(genre="Disco"),
        "none.mp3": dict(),  # no genre → left in place
    })
    assert len(plan.actions) == 1
    assert "applied to 1 file(s)" in report  # not 2


def test_spec_validation_rejects_unknown_default():
    with pytest.raises(OrganizeError):
        OrganizeSpec(rules=[], default="vaporise")


def test_spec_roundtrips_through_dict():
    spec = OrganizeSpec(rules=[Rule(field="genre", op="equals", value="Disco", action="by_genre")],
                        default="leave", summary="hi")
    assert OrganizeSpec.from_dict(spec.to_dict()).to_dict() == spec.to_dict()
