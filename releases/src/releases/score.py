"""Rank projects by how close to *shippable* they are.

closeness = stage weight (the dominant signal) + a bounce bonus + a recency
bonus. The stage is what really matters — a Complete Track beats a fresh
sketch regardless of dates — but among similar stages, "has a render" and
"touched recently" break the tie toward what's realistically finishable next.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .model import Project, stage_weight

BOUNCE_BONUS = 12.0
RECENCY_BONUS_MAX = 18.0
RECENCY_HALFLIFE_DAYS = 120.0  # bonus halves every ~4 months of staleness


@dataclass
class Scored:
    project: Project
    score: float
    recency_days: float


def _recency_bonus(last_modified: float, now: float) -> tuple[float, float]:
    if last_modified <= 0:
        return 0.0, float("inf")
    days = max(0.0, (now - last_modified) / 86400.0)
    bonus = RECENCY_BONUS_MAX * (0.5 ** (days / RECENCY_HALFLIFE_DAYS))
    return bonus, days


def score_project(p: Project, now: float | None = None) -> Scored:
    now = time.time() if now is None else now
    base = float(stage_weight(p.effective_stage))
    bounce = BOUNCE_BONUS if p.has_bounce else 0.0
    recency, days = _recency_bonus(p.last_modified, now)
    # "Return to / Potential" was filed as worth finishing — nudge it above the
    # "If really bored" pile that shares the return-to stage.
    if "potential" in p.path.lower():
        base += 6.0
    return Scored(project=p, score=round(base + bounce + recency, 2), recency_days=days)


def rank(projects: list[Project], now: float | None = None) -> list[Scored]:
    """Score and sort projects, closest-to-done first."""
    now = time.time() if now is None else now
    scored = [score_project(p, now) for p in projects]
    scored.sort(key=lambda s: (-s.score, s.recency_days, s.project.name.lower()))
    return scored
