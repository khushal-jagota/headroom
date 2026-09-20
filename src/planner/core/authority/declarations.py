"""Which Worker types stand above part of the plan, and over exactly what.

Most Worker types stand above nothing: their work is their own Ticket. Three planning
types are different, because the thing they were created to produce is the shared plan
itself. A ``planning-day`` Ticket writes a Day's morning overview. That is not authority
over somebody else's object by position, so the chain cannot derive it — it has to be
declared.

It is declared here, in code, and not on the stored Worker type. A Worker type is one
JSON document in a database row, so a field on the definition would mean rewriting those
documents on deploy, and this Ticket adds no migration. Panels already hard-codes these
type names at the point of the check, so naming them once here is strictly less scattered
than the three branches it replaces.

The cost of the choice, stated: renaming a planning Worker type in the database without
changing this table silently removes its reach. That is the same failure the string
literals in ``days/api.py`` have today, and no worse. ``test_authority_declarations``
pins every name in this table against the registered types.
"""

from __future__ import annotations

from typing import Final

from planner.core.authority.contracts import ANY_ID, Target, outcome, plan

PLANNING_DAY_WORKER_TYPE: Final = "planning-day"
PLANNING_MIDDAY_CHECK_WORKER_TYPE: Final = "planning-midday-check"
PLANNING_SPRINT_WORKER_TYPE: Final = "planning-sprint"

DAY_MORNING_FIELDS: Final = ("focus", "brief_take", "watchout", "if_today_lands")
DAY_MIDDAY_FIELD: Final = "midday_reconciliation"

STANDS_ABOVE_BY_WORKER_TYPE: Final[dict[str, tuple[Target, ...]]] = {
    # The morning overview, and nothing else on a Day. A Day's notes stay Khushal's.
    PLANNING_DAY_WORKER_TYPE: (plan("day", *DAY_MORNING_FIELDS),),
    # One field, written once in the afternoon.
    PLANNING_MIDDAY_CHECK_WORKER_TYPE: (plan("day", DAY_MIDDAY_FIELD),),
    # Sprint planning shapes the sprint itself, its membership, and every Outcome in it.
    PLANNING_SPRINT_WORKER_TYPE: (
        plan("sprint"),
        plan("sprint_outcomes"),
        outcome(ANY_ID),
    ),
}


def stands_above_for_worker_type(worker_type: str) -> tuple[Target, ...]:
    """What a Ticket of this Worker type stands above. Empty for almost every type."""
    return STANDS_ABOVE_BY_WORKER_TYPE.get(worker_type, ())


__all__ = [
    "DAY_MIDDAY_FIELD",
    "DAY_MORNING_FIELDS",
    "PLANNING_DAY_WORKER_TYPE",
    "PLANNING_MIDDAY_CHECK_WORKER_TYPE",
    "PLANNING_SPRINT_WORKER_TYPE",
    "STANDS_ABOVE_BY_WORKER_TYPE",
    "stands_above_for_worker_type",
]
