"""Freeze admission (§3.1, §5). Kickoff fields are inadmissible once
kickoff_frozen_at is set; review fields once review_frozen_at is set; the two flags
are independent. Fields in neither group (e.g. name, weekly_addenda) are always
admissible. Pure: scalar timestamps in, bool out."""

from __future__ import annotations

from planner.sprints.contracts import KICKOFF_FIELDS, REVIEW_FIELDS


def frozen_group(field: str) -> str | None:
    if field in KICKOFF_FIELDS:
        return "kickoff"
    if field in REVIEW_FIELDS:
        return "review"
    return None


def field_write_admissible(
    field: str,
    kickoff_frozen_at: int | None,
    review_frozen_at: int | None,
) -> bool:
    group = frozen_group(field)
    if group == "kickoff":
        return kickoff_frozen_at is None
    if group == "review":
        return review_frozen_at is None
    return True
