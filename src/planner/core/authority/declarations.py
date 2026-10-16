"""Historical planning type names. Retired types have no shared-plan authority."""

from __future__ import annotations

from typing import Final

from planner.core.authority.contracts import Target

PLANNING_DAY_WORKER_TYPE: Final = "planning-day"
PLANNING_MIDDAY_CHECK_WORKER_TYPE: Final = "planning-midday-check"
PLANNING_SPRINT_WORKER_TYPE: Final = "planning-sprint"

DAY_MORNING_FIELDS: Final = ("focus", "brief_take", "watchout", "if_today_lands")
DAY_MIDDAY_FIELD: Final = "midday_reconciliation"

# What an Outcome is, as sprint planning shapes it: its words and its placement. Not its
# existence, not its agent, and not its files. Sprint planning writes briefs; it does not
# delete Outcomes or reach into a manager's conversation, and the declaration says so.
OUTCOME_BRIEF_FIELDS: Final = ("title", "body", "priority", "project_id")

# Retired planning types retain their records but no longer write shared planning objects.
# Retired planning types retain their records but no longer write shared planning objects.
STANDS_ABOVE_BY_WORKER_TYPE: Final[dict[str, tuple[Target, ...]]] = {}


def stands_above_for_worker_type(worker_type: str) -> tuple[Target, ...]:
    """What a Ticket of this Worker type stands above. Empty for almost every type."""
    return STANDS_ABOVE_BY_WORKER_TYPE.get(worker_type, ())


__all__ = [
    "DAY_MIDDAY_FIELD",
    "DAY_MORNING_FIELDS",
    "OUTCOME_BRIEF_FIELDS",
    "PLANNING_DAY_WORKER_TYPE",
    "PLANNING_MIDDAY_CHECK_WORKER_TYPE",
    "PLANNING_SPRINT_WORKER_TYPE",
    "STANDS_ABOVE_BY_WORKER_TYPE",
    "stands_above_for_worker_type",
]
