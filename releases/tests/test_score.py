import time

from releases.model import Project
from releases.score import rank, score_project


def _p(name, stage, *, bounce=False, mtime=None, path=None):
    now = time.time()
    return Project(
        path=path or f"/x/{name}",
        name=name,
        kind="project",
        stage=stage,
        has_bounce=bounce,
        last_modified=mtime if mtime is not None else now,
    )


class TestScore:
    def test_stage_dominates(self):
        done = score_project(_p("a", "complete")).score
        raw = score_project(_p("b", "bones")).score
        assert done > raw

    def test_bounce_and_recency_help(self):
        recent = _p("r", "remix", bounce=True, mtime=time.time())
        stale = _p("s", "remix", bounce=False, mtime=time.time() - 400 * 86400)
        assert score_project(recent).score > score_project(stale).score

    def test_manual_override_changes_score(self):
        p = _p("x", "bones")
        p.stage_manual = "complete"
        assert p.effective_stage == "complete"
        assert score_project(p).score > score_project(_p("y", "bones")).score

    def test_potential_outranks_if_really_bored(self):
        good = _p("g", "return-to", path="/x/Return to/Potential/g")
        meh = _p("m", "return-to", path="/x/Return to/If really bored/m")
        assert score_project(good).score > score_project(meh).score

    def test_rank_orders_closest_first(self):
        ps = [_p("raw", "bones"), _p("done", "complete"), _p("mid", "remix")]
        ordered = [s.project.name for s in rank(ps)]
        assert ordered == ["done", "mid", "raw"]
