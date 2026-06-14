"""A minimal, real planner — enough to drive the engine end-to-end on the
library, deliberately conservative.

It does two safe things, each producing reviewable old -> new actions:

  * **Normalise filenames** — strip download-site junk like ``_spotdown.org`` and
    collapse whitespace, so names are clean and consistent.
  * **Quarantine suspected duplicates** — files whose name ends in a `` (1)``,
    `` (2)`` … copy marker are set aside in ``_quarantine/`` (never deleted),
    leaving the canonical copy in place for review.

Heavier organisation — tag-driven ``Artist - Title`` renaming, BPM/key analysis,
audio-fingerprint dedupe, genre foldering — belongs to the later ``cleanup``
slice. The engine is the same; only the plan gets richer.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .model import MOVE, QUARANTINE, Action, Plan
from .paths import QUARANTINE_DIRNAME, collision_free, is_audio, quarantine_dest

_JUNK_SUBSTRINGS = ("_spotdown.org",)
# A trailing copy marker like " (1)" just before the extension.
_DUP_MARKER = re.compile(r"\s\(\d+\)$")


def normalize_stem(stem: str) -> str:
    """Clean a filename stem: drop junk substrings, collapse whitespace."""
    cleaned = stem
    for junk in _JUNK_SUBSTRINGS:
        cleaned = cleaned.replace(junk, "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def build_plan(library_root: Path, rekordbox_xml: Path | None = None) -> Plan:
    """Produce a reviewable Plan for ``library_root`` (no filesystem changes)."""
    root = library_root.absolute()
    actions: list[Action] = []
    reserved: set[str] = set()

    # Sorted for a deterministic, reviewable plan.
    for path in sorted(p for p in root.rglob("*") if p.is_file() and is_audio(p)):
        # Never re-plan files already sitting in quarantine.
        if QUARANTINE_DIRNAME in path.relative_to(root).parts:
            continue

        stem = path.stem
        # Suspected duplicate -> quarantine (keep the canonical copy untouched).
        if _DUP_MARKER.search(stem):
            dest = quarantine_dest(root, path.name, reserved)
            reserved.add(os.path.abspath(dest).casefold())
            actions.append(
                Action(QUARANTINE, path, dest, reason="suspected duplicate (copy marker)")
            )
            continue

        # Otherwise, normalise the name if it isn't already clean.
        new_stem = normalize_stem(stem)
        if new_stem and new_stem != stem:
            dest = collision_free(path.with_name(new_stem + path.suffix), reserved, ignore=path)
            reserved.add(os.path.abspath(dest).casefold())
            actions.append(
                Action(MOVE, path, dest, reason="normalise filename (strip download junk)")
            )

    return Plan(library_root=root, actions=actions, rekordbox_xml=rekordbox_xml)
