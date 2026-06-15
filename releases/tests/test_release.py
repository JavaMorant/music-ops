"""Tests for hand-curated releases: db helpers, the release engine, and
plan_release. CLI-level coverage lives in test_cli.py."""

from __future__ import annotations

from datetime import date

import pytest

from releases import db as dbmod
from releases import release as relmod
from releases.model import Project
from releases.plan import plan_release
from releases.scan import scan
from releases.release import ReleaseError


def _proj(name, stage="complete"):
    return Project(path=f"/x/{name}", name=name, kind="project", stage=stage,
                   has_bounce=True, last_modified=1_700_000_000.0)


@pytest.fixture
def conn(synth_library, tmp_path):
    c = dbmod.connect(tmp_path / "r.db")
    dbmod.upsert_projects(c, scan(synth_library))
    return c


def _paths(conn, name_substr):
    matches = dbmod.find_projects(conn, name_substr)
    assert len(matches) == 1, f"{name_substr} -> {[m.name for m in matches]}"
    return matches[0].path, matches[0].name


class TestDbReleases:
    def test_create_and_list(self, conn):
        rid = dbmod.create_release(conn, "Summer EP", "ep")
        rels = dbmod.list_releases(conn)
        assert len(rels) == 1
        assert rels[0]["name"] == "Summer EP" and rels[0]["n_tracks"] == 0

    def test_untitled_naming(self, conn):
        dbmod.create_release(conn, dbmod.next_untitled_name(conn), "ep")
        assert dbmod.next_untitled_name(conn) == "Untitled 2"

    def test_add_dedupes_and_orders(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        a, b = _paths(conn, "Encara"), _paths(conn, "joonya")
        added, skipped = dbmod.add_tracks(conn, rid, [a, b, a])  # a twice
        assert len(added) == 2 and len(skipped) == 1
        order = dbmod.ordered_paths(conn, rid)
        assert order == [a[0], b[0]]

    def test_add_at_position(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        a, b, c = _paths(conn, "Encara"), _paths(conn, "joonya"), _paths(conn, "sketch1")
        dbmod.add_tracks(conn, rid, [a, b])
        dbmod.add_tracks(conn, rid, [c], at=1)  # insert at front
        assert dbmod.ordered_paths(conn, rid) == [c[0], a[0], b[0]]

    def test_remove_repacks_positions(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        a, b, c = _paths(conn, "Encara"), _paths(conn, "joonya"), _paths(conn, "sketch1")
        dbmod.add_tracks(conn, rid, [a, b, c])
        dbmod.remove_track(conn, rid, b[0])
        tracks = dbmod.get_release_tracks(conn, rid)
        assert [t["position"] for t in tracks] == [1, 2]
        assert [t["path"] for t in tracks] == [a[0], c[0]]

    def test_delete_cascades_tracks(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        dbmod.add_tracks(conn, rid, [_paths(conn, "Encara")])
        dbmod.delete_release(conn, rid)
        # the membership rows are gone (FK ON DELETE CASCADE)
        assert dbmod.get_release_tracks(conn, rid) == []
        assert dbmod.list_releases(conn) == []

    def test_missing_track_surfaces_with_snapshot(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        # a track whose project is not in the index → MISSING with snapshot name
        dbmod.add_tracks(conn, rid, [("/gone/ghost.flp", "Ghost Tune")])
        tracks = dbmod.get_release_tracks(conn, rid)
        assert tracks[0]["missing"] is True
        assert tracks[0]["name"] == "Ghost Tune"
        assert tracks[0]["project"] is None

    def test_release_track_paths(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        a = _paths(conn, "Encara")
        dbmod.add_tracks(conn, rid, [a])
        assert a[0] in dbmod.release_track_paths(conn)

    def test_untitled_name_no_collision_after_delete(self, conn):
        # regression: COUNT(*)-based naming reused numbers after a delete
        r1 = dbmod.create_release(conn, dbmod.next_untitled_name(conn), "ep")  # Untitled 1
        dbmod.create_release(conn, dbmod.next_untitled_name(conn), "ep")       # Untitled 2
        dbmod.delete_release(conn, r1)
        assert dbmod.next_untitled_name(conn) == "Untitled 3"  # not "Untitled 2"

    def test_find_release_numeric_name_reachable(self, conn):
        # a release literally named "2024" must still resolve by name
        dbmod.create_release(conn, "2024", "ep")
        matches = dbmod.find_release(conn, "2024")
        assert len(matches) == 1 and matches[0]["name"] == "2024"

    def test_delete_clears_owned_schedule_rows(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        dbmod.replace_release_schedule(conn, rid, [
            {"path": "release:%d" % rid, "name": "EP", "slot_date": "2020-01-01",
             "slot_type": "ep", "release_id": rid},
        ])
        assert len(dbmod.get_schedule(conn)) == 1
        dbmod.delete_release(conn, rid)
        assert dbmod.get_schedule(conn) == []  # no dangling overdue rows

    def test_scoped_replace_preserves_other_releases(self, conn):
        a = dbmod.create_release(conn, "A", "ep")
        b = dbmod.create_release(conn, "B", "ep")
        dbmod.replace_release_schedule(conn, a, [
            {"path": "release:%d" % a, "name": "A", "slot_date": "2026-08-01", "slot_type": "ep", "release_id": a}])
        dbmod.replace_release_schedule(conn, b, [
            {"path": "release:%d" % b, "name": "B", "slot_date": "2026-09-01", "slot_type": "ep", "release_id": b}])
        # re-planning A leaves B's slot intact
        dbmod.replace_release_schedule(conn, a, [
            {"path": "release:%d" % a, "name": "A", "slot_date": "2026-08-15", "slot_type": "ep", "release_id": a}])
        names = {r["name"] for r in dbmod.get_schedule(conn)}
        assert names == {"A", "B"}


class TestEngine:
    def test_resolve_release_by_id_and_name(self, conn):
        rid = dbmod.create_release(conn, "Summer EP", "ep")
        assert relmod.resolve_release(conn, str(rid))["id"] == rid
        assert relmod.resolve_release(conn, "summer")["id"] == rid

    def test_resolve_release_errors(self, conn):
        dbmod.create_release(conn, "Alpha", "ep")
        dbmod.create_release(conn, "Alpine", "ep")
        with pytest.raises(ReleaseError):
            relmod.resolve_release(conn, "nope")
        with pytest.raises(ReleaseError):
            relmod.resolve_release(conn, "alp")  # ambiguous

    def test_top_excludes_released_and_already_in_a_release(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        enc = _paths(conn, "Encara")
        dbmod.add_tracks(conn, rid, [enc])  # Encara now taken
        items = relmod.top_candidates(conn, 50)
        paths = [p for p, _ in items]
        assert enc[0] not in paths

    def test_collect_add_items_reports_bad_queries(self, conn):
        items, problems = relmod.collect_add_items(conn, ["Encara", "zzzznope"], top=None)
        assert any("Encara" in p for p, _ in items)
        assert any("zzzznope" in pb for pb in problems)

    def test_collect_add_items_dedupes_top_against_query(self, conn):
        # Encara is closest-to-done; --top 1 picks it, and naming it too must not double it
        items, _ = relmod.collect_add_items(conn, ["Encara"], top=1)
        paths = [p for p, _ in items]
        assert len(paths) == len(set(paths))  # no dupes

    def test_move_between_releases(self, conn):
        a = dbmod.create_release(conn, "A", "ep")
        b = dbmod.create_release(conn, "B", "ep")
        enc = _paths(conn, "Encara")
        dbmod.add_tracks(conn, a, [enc])
        relmod.move_track(conn, "Encara", "B", at=None)
        assert dbmod.ordered_paths(conn, a) == []
        assert dbmod.ordered_paths(conn, b) == [enc[0]]

    def test_move_ambiguous_when_in_two_releases(self, conn):
        a = dbmod.create_release(conn, "A", "ep")
        b = dbmod.create_release(conn, "B", "ep")
        enc = _paths(conn, "Encara")
        dbmod.add_tracks(conn, a, [enc])
        dbmod.add_tracks(conn, b, [enc])
        with pytest.raises(ReleaseError):
            relmod.move_track(conn, "Encara", "A", at=None)

    def test_reorder(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        a, b, c = _paths(conn, "Encara"), _paths(conn, "joonya"), _paths(conn, "sketch1")
        dbmod.add_tracks(conn, rid, [a, b, c])
        relmod.reorder(conn, rid, ["sketch1"])  # named goes first, rest keep order
        assert dbmod.ordered_paths(conn, rid) == [c[0], a[0], b[0]]


class TestPlanRelease:
    def _present(self, conn, rid):
        return [t["project"] for t in dbmod.get_release_tracks(conn, rid) if not t["missing"]]

    def test_ep_lays_singles_then_ep_in_curated_order(self, conn):
        rid = dbmod.create_release(conn, "Summer EP", "ep")
        a, b = _paths(conn, "Encara"), _paths(conn, "joonya")
        dbmod.add_tracks(conn, rid, [b, a])  # b first on purpose
        slots, cad, tgt = plan_release(
            self._present(conn, rid), "single/3w", "EP by Aug 31",
            date(2026, 6, 14), "ep", rid, "Summer EP",
        )
        singles = [s for s in slots if s.slot_type == "single"]
        assert [s.scored.project.name for s in singles][0].startswith("joonya")  # order kept
        assert all(s.slot_date.weekday() == 4 for s in singles)  # Fridays
        ep = slots[-1]
        assert ep.slot_type == "ep" and ep.release_id == rid and ep.label == "Summer EP"

    def test_together_is_one_ep_slot(self, conn):
        rid = dbmod.create_release(conn, "EP", "ep")
        dbmod.add_tracks(conn, rid, [_paths(conn, "Encara"), _paths(conn, "joonya")])
        slots, _, _ = plan_release(
            self._present(conn, rid), "single/3w", "EP by Aug 31",
            date(2026, 6, 14), "ep", rid, "EP", together=True,
        )
        assert len(slots) == 1 and slots[0].slot_type == "ep"

    def test_single_release_one_slot(self, conn):
        rid = dbmod.create_release(conn, "S", "single")
        dbmod.add_tracks(conn, rid, [_paths(conn, "Encara")])
        slots, _, _ = plan_release(
            self._present(conn, rid), "single/3w", None,
            date(2026, 6, 14), "single", rid, "S",
        )
        assert len(slots) == 1 and slots[0].slot_type == "single"

    def test_ep_respects_deadline_overflow(self):
        # regression (critical): with a near deadline, lead singles must stop so
        # the EP lands ON the deadline — not slide past it.
        present = [_proj(f"t{i}") for i in range(5)]
        slots, _, tgt = plan_release(
            present, "single/3w", "EP by 2026-07-01", date(2026, 6, 14),
            "ep", 1, "EP",
        )
        singles = [s for s in slots if s.slot_type == "single"]
        ep = slots[-1]
        assert ep.slot_type == "ep"
        assert ep.slot_date == date(2026, 7, 1)        # EP exactly on the deadline
        assert len(singles) < len(present)             # overflow tracks deferred
        assert all(s.slot_date < date(2026, 7, 1) for s in singles)
