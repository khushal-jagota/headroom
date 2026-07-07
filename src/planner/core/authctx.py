"""Request context and the agent/human write boundary. Reads the X-Plan-Actor
header, classifies each request as an agent (any actor header) or the human (no
header), and gates human-only actions/fields.

The old claim mechanism (X-Plan-Run-Id / X-Plan-Claim + per-request claim
validation against a ticket lease) is gone. Agents are trusted on this single-user
local planner; overlapping ticket sends are rejected by Hermes's per-session 4009
inside the shared worker child. What survives is the invisible-approval boundary —
agents still may not perform human-only actions (accept/approve/scope/state/drop,
takeover/release, human-only fields).

FastAPI appears here because this is part of the server shell (the server.py
family), not a logic or data module."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Final

from fastapi import Request

from planner.core.errors import ErrorCode, PlannerError

X_PLAN_ACTOR: Final = "X-Plan-Actor"
_DEFAULT_AGENT_ACTOR: Final = "agent"    # PLAN_ACTOR default "agent"
_HUMAN_ACTOR: Final = "human"


@dataclass(frozen=True)
class RequestContext:
    actor: str
    is_human: bool


def _normalize(raw: str | None) -> str | None:
    """strip(); an empty or whitespace-only header is an absent header."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _classify(actor: str | None) -> RequestContext:
    """An X-Plan-Actor header ⇒ an agent request; no header ⇒ the human (UI requests
    carry no agent header)."""
    if actor is not None:
        return RequestContext(actor=actor, is_human=False)
    return RequestContext(actor=_HUMAN_ACTOR, is_human=True)


def request_context(request: Request) -> RequestContext:
    """FastAPI dependency: reads X-Plan-Actor, normalizes, classifies. No DB, no clock."""
    return _classify(_normalize(request.headers.get(X_PLAN_ACTOR)))


def reject_agents(ctx: RequestContext) -> None:
    """(H) routes: any agent-classified request is agent_forbidden."""
    if ctx.is_human:
        return None
    raise PlannerError(
        ErrorCode.agent_forbidden, "human-only action", {"actor": ctx.actor}
    )


def reject_agent_fields(
    ctx: RequestContext, body_keys: Iterable[str], human_only_keys: Collection[str]
) -> None:
    """Field-level agent boundary for PATCH routes that expose both agent-permitted and
    human-only keys (§8, §14): an agent-classified request that touches any human-only
    field is agent_forbidden, naming the first offending field. Human requests pass
    through untouched."""
    if ctx.is_human:
        return None
    for key in body_keys:
        if key in human_only_keys:
            raise PlannerError(
                ErrorCode.agent_forbidden,
                "human-only field",
                {"field": key, "actor": ctx.actor},
            )
    return None
