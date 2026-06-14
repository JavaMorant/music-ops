"""Filtering a Plan down to a reviewed subset of its actions.

The web app's review table lets the user tick/untick rows, so Apply runs only
*some* of a plan's actions. A Plan's ``rekordbox_additions`` and
``location_redirects`` are tied to specific actions — so dropping an action must
also drop anything that would otherwise make rekordbox reference a path no kept
action reaches. ``select_actions`` is the one place that pruning happens; it is
pure (no filesystem access) so it's trivially testable and shared by the app and
any future ``apply --only`` CLI flag.
"""

from __future__ import annotations

from collections.abc import Iterable

from .model import Plan
from .paths import norm_key


def select_actions(plan: Plan, keep_ids: Iterable[int]) -> Plan:
    """Return a NEW Plan with only the actions at the given 0-based indices into
    ``plan.actions``, pruning ``location_redirects`` and ``rekordbox_additions``
    so that after apply the rekordbox XML never references a path no kept action
    reaches. Pure: no I/O, ``plan`` is not mutated.

    Consistency rule: a surviving redirect *value* or addition *location* must be
    a path some kept action moves a file **to** (``kept_dests``), or a path that
    is untouched by the run (neither the src nor the dest of any *dropped*
    action — i.e. a real file still sitting where it was). Anything else is
    dropped; when in doubt we drop the rekordbox side-effect, never emit a dead
    pointer. The engine overlays redirects on top of the literal src→dest of the
    kept actions, so a dropped redirect for a kept action degrades safely.

    Raises ValueError if any index is out of range.
    """
    n = len(plan.actions)
    keep = sorted(set(keep_ids))
    for i in keep:
        if not (0 <= i < n):
            raise ValueError(f"keep_id {i} out of range 0..{n - 1}")

    kept_set = set(keep)
    kept = [plan.actions[i] for i in keep]  # preserves plan order
    dropped = [a for i, a in enumerate(plan.actions) if i not in kept_set]

    kept_srcs = {norm_key(a.src) for a in kept}
    kept_dests = {norm_key(a.dest) for a in kept}
    moved_away = {norm_key(a.src) for a in dropped}
    not_arrived = {norm_key(a.dest) for a in dropped}

    def target_ok(target) -> bool:
        t = norm_key(target)
        if t in kept_dests:  # a path some kept action lands a file on
            return True
        if t in kept_srcs:  # a path a kept action empties — would be dead
            return False
        # otherwise valid only if untouched by this run (no dropped action's path)
        return t not in moved_away and t not in not_arrived

    redirects = None
    if plan.location_redirects:
        redirects = {
            k: v
            for k, v in plan.location_redirects.items()
            if norm_key(k) in kept_srcs and target_ok(v)
        } or None

    additions = None
    if plan.rekordbox_additions:
        additions = [
            a for a in plan.rekordbox_additions if norm_key(a.location) in kept_dests
        ] or None

    return Plan(
        library_root=plan.library_root,
        actions=kept,
        rekordbox_xml=plan.rekordbox_xml,
        location_redirects=redirects,
        rekordbox_additions=additions,
        rekordbox_playlist=plan.rekordbox_playlist if additions else None,
    )
