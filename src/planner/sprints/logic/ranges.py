"""Sprint-range overlap (§3.1) and current-sprint selection (§6.1). Inclusive ISO
date intervals compared lexicographically. Pure: a minimal DateRange projection in,
ids/bools out."""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple


class DateRange(NamedTuple):
    id: str
    date_start: str            # ISO date, inclusive
    date_end: str              # ISO date, inclusive


def ranges_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start <= b_end and b_start <= a_end


def find_overlap(
    date_start: str, date_end: str, existing: Iterable[DateRange]
) -> str | None:
    for r in existing:
        if ranges_overlap(date_start, date_end, r.date_start, r.date_end):
            return r.id
    return None


def current_sprint_id(
    planning_date: str, sprints: Iterable[DateRange]
) -> str | None:
    for r in sprints:
        if r.date_start <= planning_date <= r.date_end:
            return r.id
    return None
