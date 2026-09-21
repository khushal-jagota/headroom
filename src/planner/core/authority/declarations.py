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

# What an Outcome is, as sprint planning shapes it: its words and its placement. Not its
# existence, not its agent, and not its files. Sprint planning writes briefs; it does not
# delete Outcomes or reach into a manager's conversation, and the declaration says so.
OUTCOME_BRIEF_FIELDS: Final = ("title", "body", "priority", "deadline", "project_id")

STANDS_ABOVE_BY_WORKER_TYPE: Final[dict[str, tuple[Target, ...]]] = {
    # The morning overview, and which Tickets are on the Day. A Day's notes stay
    # Khushal's, and a Ticket's own fields stay the Ticket's: putting work on a Day is
    # composing the Day, not reaching into the work.
    PLANNING_DAY_WORKER_TYPE: (
        plan("day", *DAY_MORNING_FIELDS),
        plan("day_tickets"),
    ),
    # One field, written once in the afternoon, and the same power to move work on and
    # off today, which is the whole point of checking at midday.
    PLANNING_MIDDAY_CHECK_WORKER_TYPE: (
        plan("day", DAY_MIDDAY_FIELD),
        plan("day_tickets"),
    ),
    # Sprint planning shapes the sprint, its membership, and the brief of any Outcome it
    # commits. Naming those fields is what keeps it out of deleting an Outcome, resetting
    # its manager's conversation, or writing its files — operations on the whole object,
    # which a declaration over named fields does not reach.
    PLANNING_SPRINT_WORKER_TYPE: (
        plan("sprint"),
        plan("sprint_outcomes"),
        outcome(ANY_ID, *OUTCOME_BRIEF_FIELDS),
    ),
}


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
