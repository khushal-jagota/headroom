"""Write admission: who may write what, when."""

from __future__ import annotations

from datetime import date
from typing import Final

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import AtCap
from planner.tickets.logic import machine
from planner.worker_types.contracts import WorkerTypeDefinition

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


def require_worker_actor(actor: str, action: str) -> None:
    """Require an attributed non-Chief Worker identity for Worker-only writes."""
    if is_direct_actor(actor):
        raise PlannerError(
            ErrorCode.agent_forbidden,
            f"{action} is only available to a Worker",
            {"action": action, "actor": actor},
        )


def check_agent_proposal(
    stage: str,
    ceiling: str,
    at_cap: AtCap,
    field: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> None:
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
    if not machine.at_or_beyond_ceiling(
        stage,
        ceiling,
        worker_type_definition=worker_type_definition,
    ):
        return
    if at_cap == AtCap.stop:
        raise PlannerError(
            ErrorCode.at_cap_stop,
            "ticket is at its ceiling with at_cap=stop",
            {
                "gating_field": gating,
                "stage": stage,
                "ceiling": ceiling,
                "at_cap": "stop",
            },
        )
    if field != gating:
        raise PlannerError(
            ErrorCode.validation,
            "at the ceiling agents may propose only the current gating field",
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
