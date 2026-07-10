"""Truthful request identity and the direct-write authority boundary.

No ``X-Plan-Actor`` header means the request is unattributed. ``chief`` identifies
the Panels Chief. Every other non-empty actor value identifies an attributed
non-Chief agent. Direct product operations permit unattributed and Chief requests;
worker identities remain subject to the existing direct-write restrictions.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Final

from fastapi import Request

from planner.core.errors import ErrorCode, PlannerError

X_PLAN_ACTOR: Final = "X-Plan-Actor"
_UNATTRIBUTED_ACTOR: Final = "unattributed"
_CHIEF_ACTOR: Final = "chief"


@dataclass(frozen=True)
class RequestContext:
    actor: str
    is_attributed: bool
    is_chief: bool


def _normalize(raw: str | None) -> str | None:
    """Strip the header; whitespace-only values are absent."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _classify(actor: str | None) -> RequestContext:
    if actor is None:
        return RequestContext(
            actor=_UNATTRIBUTED_ACTOR,
            is_attributed=False,
            is_chief=False,
        )
    return RequestContext(
        actor=actor,
        is_attributed=True,
        is_chief=actor == _CHIEF_ACTOR,
    )


def request_context(request: Request) -> RequestContext:
    """FastAPI dependency: normalize and classify ``X-Plan-Actor``."""
    return _classify(_normalize(request.headers.get(X_PLAN_ACTOR)))


def require_direct_write(ctx: RequestContext) -> None:
    """Permit unattributed or explicit Chief direct operations."""
    if not ctx.is_attributed or ctx.is_chief:
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "direct operation is not available to this actor",
        {"actor": ctx.actor},
    )


def require_chief(ctx: RequestContext) -> None:
    """Require an explicit ``X-Plan-Actor: chief`` identity."""
    if ctx.is_chief:
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "chief operation requires the chief actor",
        {"actor": ctx.actor},
    )


def reject_agent_fields(
    ctx: RequestContext,
    body_keys: Iterable[str],
    direct_only_keys: Collection[str],
) -> None:
    """Reject direct-only fields for attributed non-Chief agents."""
    if not ctx.is_attributed or ctx.is_chief:
        return
    for key in body_keys:
        if key in direct_only_keys:
            raise PlannerError(
                ErrorCode.agent_forbidden,
                "direct-only field",
                {"field": key, "actor": ctx.actor},
            )
