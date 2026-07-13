"""Write admission: who may write what, when. The proposal matrix, direct-write
gate, and ticket body/title/deadline validators. Pure domain rules only."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Final

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import AtCap, FieldName
from planner.tickets.logic import coding_bridge, machine

if TYPE_CHECKING:
    from planner.tickets.logic.coding_bridge import WorkflowDefinition

# Compatibility for direct domain callers and historical fixtures. Request
# classification never synthesizes this value; live callers are unattributed or Chief.
_LEGACY_DIRECT_ACTOR: Final[str] = "human"
DIRECT_ACTORS: Final[frozenset[str]] = frozenset(
    {"unattributed", "chief", _LEGACY_DIRECT_ACTOR}
)


def is_direct_actor(actor: str) -> bool:
    return actor in DIRECT_ACTORS


def require_direct_actor(actor: str, action: str) -> None:
    if not is_direct_actor(actor):
        raise PlannerError(
            ErrorCode.agent_forbidden,
            f"{action} is a direct-only action",
            {"action": action, "actor": actor},
        )


def check_agent_proposal(
    state: str,
    ceiling: str,
    at_cap: AtCap,
    field: FieldName | str,
    *,
    definition: WorkflowDefinition | None = None,
) -> None:
    defn = definition or coding_bridge.coding_definition()
    if machine.is_terminal(state, definition=defn):
        raise PlannerError(
            ErrorCode.validation, "no proposals on a terminal ticket", {"state": str(state)}
        )
    gating = machine.gating_field(state, definition=defn)
    if gating is None:
        raise PlannerError(
            ErrorCode.validation,
            "ticket state has no proposal field",
            {"state": str(state)},
        )
    if not machine.at_or_beyond_ceiling(state, ceiling, definition=defn):
        return
    if at_cap == AtCap.stop:
        raise PlannerError(
            ErrorCode.at_cap_stop,
            "ticket is at its ceiling with at_cap=stop",
            {
                "gating_field": str(gating),
                "state": str(state),
                "ceiling": str(ceiling),
                "at_cap": "stop",
            },
        )
    if str(field) != str(gating):
        raise PlannerError(
            ErrorCode.validation,
            "at the ceiling agents may propose only the current gating field",
            {
                "field": str(field),
                "gating_field": str(gating),
                "state": str(state),
            },
        )


def check_recap_writable(
    state: str, *, definition: WorkflowDefinition | None = None
) -> None:
    defn = definition or coding_bridge.coding_definition()
    # The first real-work stage (needs_success for coding, needs_stages for new_worker) —
    # NOT default_ceiling, which is now the leading needs_kickoff. Recap stays writable
    # exactly as before: only strictly past the first worker stage.
    first_worker = coding_bridge.views.first_worker_stage(defn)
    if str(state) == defn.dropped_stage.id or not (
        machine.state_index(state, definition=defn)
        > machine.state_index(first_worker, definition=defn)
    ):
        raise PlannerError(
            ErrorCode.recap_too_early,
            f"recap is writable only past the first worker stage ({first_worker})",
            {"state": str(state), "first_worker_stage": first_worker},
        )


def check_sprint_assignable(ticket_id: str, sprint_item_id: str | None) -> None:
    if sprint_item_id is not None:
        raise PlannerError(
            ErrorCode.sprint_derived,
            "sprint_id is derived from the parent item",
            {"ticket_id": ticket_id, "sprint_item_id": sprint_item_id},
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
            ErrorCode.validation, "deadline must be an ISO date", {"deadline": deadline}
        ) from exc


def validate_body(body: str, what: str) -> None:
    if not body:
        raise PlannerError(ErrorCode.validation, f"{what} must be non-empty")
