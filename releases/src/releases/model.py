"""Domain model: a scanned project and the stage taxonomy it lives in.

The stage taxonomy mirrors the real folders under
``ProducerLibrary/projects/Beats/Tracks`` (Complete Tracks / Need Arranged /
Remixes In Progress / Return to / Mels / Project bones / Track List). Each
stage carries a ``weight`` — how close to *shippable* a project in it is —
which drives the ``--closest`` ranking and the release calendar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    weight: int  # 0..100 — higher == closer to shippable


# Inferred stages (from the folder taxonomy) plus two pipeline stages a user
# sets by hand with ``releases status`` (scheduled / released). Ordered roughly
# by closeness-to-done.
STAGES: dict[str, Stage] = {
    "released": Stage("released", "Released", 100),
    "complete": Stage("complete", "Complete Tracks", 95),
    "track-list": Stage("track-list", "Track List", 88),
    "scheduled": Stage("scheduled", "Scheduled", 85),
    "remix": Stage("remix", "Remixes In Progress", 62),
    "need-arranged": Stage("need-arranged", "Need Arranged", 52),
    "return-to": Stage("return-to", "Return to", 40),
    "uncategorized": Stage("uncategorized", "Uncategorized", 36),
    "mels": Stage("mels", "Mels (melody sketches)", 30),
    "bones": Stage("bones", "Project bones", 12),
}

DEFAULT_STAGE = "uncategorized"

# Stages a user can move a project *to* by hand. Inferred stages are also valid
# targets (you can demote/reclassify); the two pipeline stages are added here.
SETTABLE_STAGES = list(STAGES.keys())


def stage_weight(key: str) -> int:
    s = STAGES.get(key)
    return s.weight if s else STAGES[DEFAULT_STAGE].weight


def stage_label(key: str) -> str:
    s = STAGES.get(key)
    return s.label if s else key


@dataclass
class Project:
    """One scanned project (an FL .flp project, possibly multi-version) or a
    standalone bounce that has no project folder of its own."""

    path: str  # absolute path to the project folder (or the standalone file)
    name: str
    kind: str  # "project" (has .flp) | "bounce" (standalone render, no .flp)
    stage: str  # inferred from the folder taxonomy
    genre: str = "unknown"
    bpm: Optional[int] = None
    key: Optional[str] = None
    has_bounce: bool = False
    flp_count: int = 0
    last_modified: float = 0.0  # epoch seconds — newest .flp (or the bounce)

    # Populated from the db, not the scan:
    stage_manual: Optional[str] = None  # set via `releases status`; overrides stage

    @property
    def effective_stage(self) -> str:
        """The stage that counts for ranking/display — a manual override wins."""
        return self.stage_manual or self.stage
