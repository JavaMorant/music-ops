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

    dplan = organise.build_dedup_plan(tmp_path, res.dup_groups)
    assert dplan.actions and all(a.kind == QUARANTINE for a in dplan.actions)

    rplan = organise.build_reorg_plan(res)
    assert rplan.actions and all(a.kind == MOVE for a in rplan.actions)
    # every move files into one of the new buckets, never back to an old folder name
    assert all(a.dest.parent.name in ("UK Rap", "Pop") for a in rplan.actions)
    # nothing is deleted: every drop maps to a quarantine destination
    assert all(a.src != a.dest for a in dplan.actions)


def test_dedup_redirects_cues_to_keeper_not_quarantine(tmp_path):
    """rekordbox cue-safety invariant: a quarantined duplicate's cues must follow
    the KEPT copy that stays in the library, never the reject in _quarantine."""
    _lib(tmp_path)
    res = organise.organise(tmp_path, key=None, run_ai=False)

    dplan = organise.build_dedup_plan(tmp_path, res.dup_groups)
    assert dplan.actions and dplan.location_redirects

    # the true drop -> keeper mapping straight from the dedup grouping
    drop_to_keeper = {d.path: g.keep.path for g in res.dup_groups for d in g.drops}
    assert drop_to_keeper  # the fixture contains a real recording-level duplicate

    for a in dplan.actions:
        keeper = dplan.location_redirects[a.src]
        assert keeper == drop_to_keeper[a.src]  # points at the kept copy...
        assert keeper != a.dest                 # ...not the quarantine reject
        assert keeper.exists()                  # the keeper stays in the library


def test_unclassified_keepers_are_counted_not_dropped_c13(tmp_path):
    """When the AI pass runs but returns no genre for a keeper, it must be COUNTED
    (and left in place) — never vanish silently from the reorg plan and every
    printed total, which is what masked whole failed classify batches."""
    _lib(tmp_path)
    # run_ai on, but the injected classifier returns nothing for anyone.
    res = organise.organise(tmp_path, key=None, run_ai=True,
                            classify_call=lambda batch, model, effort: [])
    assert res.used_ai is True
    assert res.unclassified == res.dedup["unique_keepers"]  # all keepers accounted for
    assert res.reorg_actions == 0
    # left in place, not force-filed into a junk bucket
    assert organise.build_reorg_plan(res).actions == []
