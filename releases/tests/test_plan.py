from datetime import date

import pytest

from releases.model import Project
from releases.plan import (
    PlanError,
    parse_cadence,
    parse_target,
    plan_releases,
)


def _p(name, stage, **kw):
    return Project(path=f"/x/{name}", name=name, kind="project", stage=stage,
                   last_modified=1_700_000_000.0, **kw)


class TestParseCadence:
    def test_units(self):
        assert parse_cadence("single/3w").interval_days == 21
        assert parse_cadence("track/10d").interval_days == 10
        assert parse_cadence("ep/1m").interval_days == 30
        assert parse_cadence("single/3w").kind == "single"

    def test_bad(self):
        with pytest.raises(PlanError):
            parse_cadence("every other tuesday")
        with pytest.raises(PlanError):
            parse_cadence("single/0w")


class TestParseTarget:
    def test_month_day_resolves_future_year(self):
        t = parse_target("EP by Aug 31", date(2026, 6, 14))
        assert t.label == "EP" and t.deadline == date(2026, 8, 31)

    def test_past_month_rolls_to_next_year(self):
        t = parse_target("EP by Feb 1", date(2026, 6, 14))
        assert t.deadline == date(2027, 2, 1)

    def test_iso_and_day_month(self):
        assert parse_target("album by 2026-08-31", date(2026, 1, 1)).deadline == date(2026, 8, 31)
        assert parse_target("EP by 31 August", date(2026, 1, 1)).deadline == date(2026, 8, 31)

    def test_no_target(self):
        assert parse_target(None, date(2026, 6, 14)).deadline is None

    def test_unparseable_date_raises(self):
        with pytest.raises(PlanError):
            parse_target("EP by someday", date(2026, 6, 14))

    def test_impossible_date_raises_planerror_not_valueerror(self):
        # must surface as PlanError so the CLI catches it (no raw traceback)
        with pytest.raises(PlanError):
            parse_target("EP by Feb 30", date(2026, 6, 14))
        with pytest.raises(PlanError):
            parse_target("EP by 2026-13-01", date(2026, 6, 14))


class TestPlanReleases:
    def _projects(self):
        return [
            _p("done1", "complete", has_bounce=True),
            _p("done2", "complete", has_bounce=True),
            _p("mid", "remix"),
            _p("raw", "bones"),
        ]

    def test_singles_on_fridays_then_ep(self):
        slots, cad, tgt = plan_releases(
            self._projects(), "single/3w", "EP by Aug 31", date(2026, 6, 14))
        assert cad.interval_days == 21
        singles = [s for s in slots if s.slot_type == "single"]
        assert all(s.slot_date.weekday() == 4 for s in singles)  # Fridays
        assert slots[-1].slot_type == "ep"
        assert slots[-1].slot_date == date(2026, 8, 31)
        # closest-to-done fills the earliest slots
        assert singles[0].scored.project.name in {"done1", "done2"}

    def test_released_excluded_from_candidates(self):
        ps = self._projects()
        ps[0].stage_manual = "released"
        slots, _, _ = plan_releases(ps, "single/3w", None, date(2026, 6, 14), count=4)
        names = [s.scored.project.name for s in slots]
        assert "done1" not in names

    def test_count_without_target(self):
        slots, _, _ = plan_releases(self._projects(), "single/2w", None, date(2026, 6, 14), count=2)
        assert len([s for s in slots if s.slot_type == "single"]) == 2
        assert not any(s.slot_type == "ep" for s in slots)

    def test_past_deadline_rejected(self):
        with pytest.raises(PlanError):
            plan_releases(self._projects(), "single/3w", "EP by 2020-01-01", date(2026, 6, 14))
