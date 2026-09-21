"""What a write must be given to be valid. Who may perform it is planner.core.authority."""

from __future__ import annotations

from datetime import date
from typing import Final

from planner.core.contracts import ErrorCode, PlannerError
from planner.worker_types.contracts import WorkerTypeDefinition

REVISION_GUIDANCE_MAX_CHARACTERS: Final = 10_000

# The TicketEdit keys that only somebody standing above the Ticket may set. A Worker
# drives its own Ticket's priority, deadline, recap and guidance, so those are the
# Ticket's own record and are absent here. These are not: renaming a Ticket, moving it
# between projects or sprints, rewriting a settled field, re-addressing a parked
# proposal, and above all raising its own ceiling. A ceiling is a Ticket's leash, and a
# leash a Ticket can lengthen is not one, which is the one thing "your own record" must
# not be read to grant.
TICKET_FIELDS_ONLY_FROM_ABOVE: Final[frozenset[str]] = frozenset(
    {
        "title",
        "project_id",
        "sprint_id",
        "sprint_item_id",
        "field_values",
        "ceiling",
        "ceiling_holder",
    }
)

# The one field nobody may set about a thing they only reach through it.
# authority.refuse_outcome_re_parenting says why.
TICKET_FIELD_THAT_MOVES_THE_PARENT: Final = "sprint_item_id"


def check_agent_proposal(
    stage: str,
    field: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> None:
    """Whether this Ticket can take a worker submission on this field at all.

    The ceiling is not asked here. A ceiling names the last thing a worker is allowed to
    do, so reaching it decides whether the answer parks for approval, never whether the
    worker is allowed to answer.
    """
    if worker_type_definition.is_terminal(stage):
        raise PlannerError(
            ErrorCode.validation, "no proposals on a terminal ticket", {"stage": stage}
        )
    gating = worker_type_definition.gating_field(stage)
    if gating is None:
        raise PlannerError(
            ErrorCode.validation,
            "ticket stage has no proposal field",
            {"stage": stage},
        )
    if field != gating:
        raise PlannerError(
            ErrorCode.validation,
            "agents may propose only the current gating field",
            {"field": field, "gating_field": gating, "stage": stage},
        )


def validate_title(title: str, max_chars: int) -> None:
    if not title:
        raise PlannerError(ErrorCode.validation, "title must be non-empty")
    if len(title) > max_chars:
        raise PlannerError(
            ErrorCode.title_too_long,
            f"title exceeds {max_chars} characters",
            {"length": len(title), "max": max_chars},
        )


def validate_deadline(deadline: str | None) -> None:
    if deadline is None:
        return
    try:
        date.fromisoformat(deadline)
    except ValueError as exc:
        raise PlannerError(
            ErrorCode.validation,
            "deadline must be an ISO date",
            {"deadline": deadline},
        ) from exc


def validate_body(body: str, what: str) -> None:
    if not body:
        raise PlannerError(ErrorCode.validation, f"{what} must be non-empty")


def validate_revision_guidance(message: str) -> None:
    validate_body(message, "revision guidance")
    if len(message) > REVISION_GUIDANCE_MAX_CHARACTERS:
        raise PlannerError(
            ErrorCode.validation,
            f"revision guidance must be at most {REVISION_GUIDANCE_MAX_CHARACTERS} characters",
            {"maximum_characters": REVISION_GUIDANCE_MAX_CHARACTERS},
        )
