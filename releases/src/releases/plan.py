"""Generate a release calendar from the closest-to-done projects.

``plan --cadence "single/3w" --target "EP by Aug 31"`` slots one single every
three weeks (on Fridays — the convention release day) drawing from the top of
the closeness ranking, then caps the run with the EP on the target date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .score import Scored, rank

_CADENCE_RE = re.compile(r"^\s*([A-Za-z]+)\s*/\s*(\d+)\s*([wdm])\s*$", re.IGNORECASE)
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30}

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"], start=1)
}
_ISO_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_MONTH_DAY_RE = re.compile(
    r"([A-Za-z]{3,9})\s+(\d{1,2})(?:[a-z]{0,2})?(?:,?\s*(\d{4}))?", re.IGNORECASE)
_DAY_MONTH_RE = re.compile(
    r"(\d{1,2})(?:[a-z]{0,2})?\s+([A-Za-z]{3,9})(?:,?\s*(\d{4}))?", re.IGNORECASE)


class PlanError(ValueError):
    pass


@dataclass
class Cadence:
    kind: str          # e.g. "single"
    interval_days: int


@dataclass
class Target:
    label: str         # e.g. "EP"
    deadline: Optional[date]


@dataclass
class Slot:
    slot_date: date
    slot_type: str             # "single" | "ep"
    scored: Optional[Scored]   # the project filling a single (None for the EP)
    label: str = ""            # used for the EP slot


def parse_cadence(text: str) -> Cadence:
    m = _CADENCE_RE.match(text)
    if not m:
        raise PlanError(
            f'cadence {text!r} not understood — use e.g. "single/3w", "track/10d", "ep/1m"'
        )
    kind, n, unit = m.group(1).lower(), int(m.group(2)), m.group(3).lower()
    if n <= 0:
        raise PlanError("cadence interval must be positive")
    return Cadence(kind=kind, interval_days=n * _UNIT_DAYS[unit])


def _make_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as e:
        raise PlanError(f"not a real date: {year:04d}-{month:02d}-{day:02d} ({e})")


def _resolve_year(month: int, day: int, year: Optional[int], today: date) -> date:
    if year:
        return _make_date(year, month, day)
    candidate = _make_date(today.year, month, day)
    if candidate < today:
        candidate = _make_date(today.year + 1, month, day)
    return candidate


def parse_target(text: Optional[str], today: date) -> Target:
    """Parse "EP by Aug 31" / "album by 2026-08-31" → label + deadline date.
    A bare year-less date resolves to its next future occurrence."""
    if not text:
        return Target(label="EP", deadline=None)
    label = re.split(r"\bby\b", text, maxsplit=1, flags=re.IGNORECASE)[0].strip() or "EP"

    iso = _ISO_RE.search(text)
    if iso:
        y, mo, d = (int(x) for x in iso.groups())
        return Target(label=label, deadline=_make_date(y, mo, d))

    for rx, order in ((_MONTH_DAY_RE, "md"), (_DAY_MONTH_RE, "dm")):
        m = rx.search(text)
        if not m:
            continue
        if order == "md":
            mon_s, day_s, yr_s = m.group(1), m.group(2), m.group(3)
        else:
            day_s, mon_s, yr_s = m.group(1), m.group(2), m.group(3)
        mon = _MONTHS.get(mon_s[:3].lower())
        if not mon:
            continue
        deadline = _resolve_year(mon, int(day_s), int(yr_s) if yr_s else None, today)
        return Target(label=label, deadline=deadline)

    raise PlanError(
        f"could not read a date out of target {text!r} — try 'EP by Aug 31' or 'EP by 2026-08-31'"
    )


def _next_friday(d: date) -> date:
    return d + timedelta(days=(4 - d.weekday()) % 7)


def build_calendar(
    ranked: list[Scored],
    cadence: Cadence,
    target: Target,
    today: date,
    lead_days: int = 7,
    count: Optional[int] = None,
) -> list[Slot]:
    """Lay singles on Fridays at the cadence interval, drawing from ``ranked``
    (closest-to-done first). With a deadline, fill until it and cap with the EP;
    without one, schedule ``count`` singles (default min(6, available))."""
    slots: list[Slot] = []
    available = list(ranked)
    slot_date = _next_friday(today + timedelta(days=lead_days))

    if target.deadline is not None:
        while available and slot_date < target.deadline:
            slots.append(Slot(slot_date, "single", available.pop(0)))
            slot_date = _next_friday(slot_date + timedelta(days=cadence.interval_days))
        slots.append(Slot(target.deadline, "ep", None, label=target.label))
    else:
        n = count if count is not None else min(6, len(available))
        for _ in range(min(n, len(available))):
            slots.append(Slot(slot_date, "single", available.pop(0)))
            slot_date = _next_friday(slot_date + timedelta(days=cadence.interval_days))

    return slots


def plan_releases(
    projects,
    cadence_text: str,
    target_text: Optional[str],
    today: date,
    count: Optional[int] = None,
) -> tuple[list[Slot], Cadence, Target]:
    cadence = parse_cadence(cadence_text)
    target = parse_target(target_text, today)
    if target.deadline is not None and target.deadline <= today:
        raise PlanError(
            f"target deadline {target.deadline.isoformat()} is not in the future"
        )
    # Already-released tracks aren't candidates for a *future* single.
    candidates = [p for p in projects if p.effective_stage != "released"]
    ranked = rank(candidates)
    slots = build_calendar(ranked, cadence, target, today, count=count)
    return slots, cadence, target
