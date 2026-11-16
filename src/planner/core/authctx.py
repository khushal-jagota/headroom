"""Resolve one caller from a launch credential or the trusted owner ingress.

Headers naming a role, Ticket, Manager or conversation never establish authority.
The executor's already-verified child uses a typed in-process request context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from fastapi import Request

from planner.agent_identity.data import authenticate_launch_token
from planner.core.contracts import (
    OWNER_PRINCIPAL,
    UNIDENTIFIED_PRINCIPAL,
    Principal,
    PrincipalKind,
    principal_legacy_actor,
)
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError

IN_PROCESS_REQUEST_CONTEXT_SCOPE_KEY: Final = "planner.in_process_request_context"
# Set by trusted ingress when Tailscale Serve vouches for the allowed login.
AUTHENTICATED_OWNER_SCOPE_KEY: Final = "planner.authenticated_owner"


@dataclass(frozen=True)
class RequestContext:
    principal: Principal
    conversation_id: str | None = None


def _behind_trusted_ingress(request: Request) -> bool:
    config = getattr(getattr(request.scope.get("app"), "state", None), "config", None)
    return getattr(config, "trusted_ingress_provider", None) is not None


def request_context(request: Request) -> RequestContext:
    """Resolve issued agent credentials before considering the owner's ingress."""
    internal = request.scope.get(IN_PROCESS_REQUEST_CONTEXT_SCOPE_KEY)
    if isinstance(internal, RequestContext):
        return internal
    authorizations = request.headers.getlist("Authorization")
    if len(authorizations) > 1:
        return RequestContext(UNIDENTIFIED_PRINCIPAL)
    authorization = authorizations[0] if authorizations else None
    if authorization is not None:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return RequestContext(UNIDENTIFIED_PRINCIPAL)
        config = getattr(getattr(request.scope.get("app"), "state", None), "config", None)
        db_path = getattr(config, "db_path", None)
        if db_path is None:
            return RequestContext(UNIDENTIFIED_PRINCIPAL)
        conn = connect(db_path)
        try:
            identity = authenticate_launch_token(conn, parts[1])
        finally:
            conn.close()
        if identity is None:
            return RequestContext(UNIDENTIFIED_PRINCIPAL)
        return RequestContext(identity.principal, identity.conversation_id)
    if request.scope.get(AUTHENTICATED_OWNER_SCOPE_KEY) is True:
        return RequestContext(OWNER_PRINCIPAL)
    # Legacy claims establish no identity. They also cannot turn a failed agent
    # authentication into the identity-free local server's owner default.
    legacy_claim = any(name.lower().startswith("x-plan-") for name in request.headers)
    if not legacy_claim and not _behind_trusted_ingress(request):
        return RequestContext(OWNER_PRINCIPAL)
    return RequestContext(UNIDENTIFIED_PRINCIPAL)


def require_owner_or_chief(ctx: RequestContext) -> None:
    """Permit the owner or the Chief itself.

    The Chief's recap is the Chief's own report of its work, the way a Ticket's recap is
    its Worker's. Owner-only on that write makes the field unwritable by the one principal
    whose work it describes.
    """
    if ctx.principal.kind in (PrincipalKind.owner, PrincipalKind.chief):
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "this operation is available only to the owner or the Chief",
        {"actor": principal_legacy_actor(ctx.principal)},
    )


def require_owner(ctx: RequestContext) -> None:
    """Permit only Khushal's direct principal.

    Not authority over anything, which is why it is not the one rule. It marks the two
    places that record that *the owner* did something: the owner's own conversation read,
    and the owner's own send. Nobody stands above those by being above a Ticket.
    """
    if ctx.principal.kind is PrincipalKind.owner:
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "this operation is available only to the owner",
        {"actor": principal_legacy_actor(ctx.principal)},
    )


def require_authenticated_owner(request: Request) -> None:
    """Device registration needs the deployed access boundary's owner identity."""
    require_owner(request_context(request))
    if request.scope.get(AUTHENTICATED_OWNER_SCOPE_KEY) is not True:
        raise PlannerError(ErrorCode.agent_forbidden, "authenticated owner access is required", {})
