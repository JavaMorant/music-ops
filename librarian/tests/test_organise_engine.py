"""Organise engine: dedup + folder-migration reorg + reversible plans (no key)."""

from __future__ import annotations

from librarian import organise
from librarian.model import MOVE, QUARANTINE


def _lib(root):
    files = {
        "Rap - UK Drill/Central Cee - Doja.mp3": b"\x00",
        "Pop - 2015+/Dua Lipa - Levitating.mp3": b"\x00",
        "Pop - 2015+/Dua Lipa - Levitating (Official Video).mp3": b"\x00",  # dup of above
        "Other/mystery track.mp3": b"\x00",  # ambiguous -> needs AI
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def test_engine_dedup_and_migration(tmp_path):
    _lib(tmp_path)
    res = organise.organise(tmp_path, key=None, run_ai=False)

    # 4 files, one is an alt-source dup of another -> 1 drop, 3 keepers
    assert res.total == 4
    assert res.dedup["drops"] == 1
    assert res.dedup["unique_keepers"] == 3

    # folder migration: UK Drill -> UK Rap, Pop - 2015+ -> Pop (both relocate)
    assert res.reorg_actions >= 2
    # the 'Other' track can't be migrated deterministically -> flagged for AI
    assert res.needs_ai >= 1
    assert res.used_ai is False


def test_plans_are_reversible_and_typed(tmp_path):
    _lib(tmp_path)
    res = organise.organise(tmp_path, key=None, run_ai=False)

    dplan = organise.build_dedup_plan(tmp_path, res.drops)
    assert dplan.actions and all(a.kind == QUARANTINE for a in dplan.actions)

    rplan = organise.build_reorg_plan(res)
    assert rplan.actions and all(a.kind == MOVE for a in rplan.actions)
    # every move files into one of the new buckets, never back to an old folder name
    assert all(a.dest.parent.name in ("UK Rap", "Pop") for a in rplan.actions)
    # nothing is deleted: every drop maps to a quarantine destination
    assert all(a.src != a.dest for a in dplan.actions)
