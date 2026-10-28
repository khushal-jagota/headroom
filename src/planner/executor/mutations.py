"""Explicit CLI mutation registry. These names call canonical domain writers."""

from __future__ import annotations

import re
import sqlite3
from functools import partial
from typing import Any

from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.executor.contracts import CliWrite, JsonObject
from planner.tickets import actions, views
from planner.tickets.contracts import ReviewTransferNotice


def cli_mutation(method: str, path: str, body: Any) -> tuple[str, JsonObject]:
    """Translate a supported CLI route into a registry operation, never a proxy route."""
    if method == "POST" and path == "/api/feedback/use":
        return "feedback.use", {"body": body}
    blocker = re.fullmatch(r"/api/collections/blockers/([^/]+)/([^/]+)", path)
    if blocker and method in ("PUT", "DELETE"):
        return "ticket.block" if method == "PUT" else "ticket.unblock", {
            "ticket_id": blocker[1],
            "blocker_id": blocker[2],
            "body": {},
        }
    if method == "POST" and path == "/api/tickets":
        return "ticket.create", {"body": body}
    proposal = re.fullmatch(r"/api/tickets/([^/]+)/propose", path)
    patch = re.fullmatch(r"/api/tickets/([^/]+)", path)
    if method == "POST" and proposal:
        return "ticket.propose", {"ticket_id": proposal[1], "body": body}
    if method == "PATCH" and patch:
        return "ticket.edit", {"ticket_id": patch[1], "body": body}
    if method == "POST" and path == "/api/messages/send":
        return "message.send", {"body": body}
    help_match = re.fullmatch(r"/api/tickets/([^/]+)/request-help", path)
    if method == "POST" and help_match:
        return "ticket.request_help", {"ticket_id": help_match[1], "body": body}
    raise ValueError(f"This executor does not support {method} {path}")


def write_ticket_mutation(
    connection: sqlite3.Connection,
    write: CliWrite,
    principal: Principal,
    clock: Clock,
    *,
    review_transfer_notices: list[ReviewTransferNotice] | None = None,
) -> JsonObject:
    """Effect and receipt use the caller's open transaction; no native I/O here."""
    from planner.tickets.api import write_create_ticket, write_patch_ticket

    args = write.arguments
    body = args.get("body")
    if not isinstance(body, dict):
        raise PlannerError(ErrorCode.validation, "ticket mutation needs a body", {})
    if write.operation == "feedback.use":
        from planner.feedback.api import write_use_feedback

        return write_use_feedback(body, connection, clock, RequestContext(principal))
    if write.operation in ("ticket.block", "ticket.unblock"):
        from planner.membership.collections import ENTRIES, MembershipWrite
        from planner.membership.contracts import Collection

        ticket_id = args.get("ticket_id")
        blocker_id = args.get("blocker_id")
        if not isinstance(ticket_id, str) or not isinstance(blocker_id, str):
            raise PlannerError(ErrorCode.validation, "blocker IDs must be text", {})
        entry = ENTRIES[Collection.blockers]
        membership = MembershipWrite(
            conn=connection,
            principal=principal,
            container_id=ticket_id,
            member_id=blocker_id,
            now=clock.now_unix(),
            admit=partial(entry.rule, connection, RequestContext(principal), ticket_id, blocker_id),
        )
        (entry.add if write.operation == "ticket.block" else entry.remove)(membership)
        return {
            "collection": "blockers",
            "container_id": ticket_id,
            "member_id": blocker_id,
            "ok": True,
        }
    if write.operation == "ticket.create":
        return write_create_ticket(body, connection, RequestContext(principal), clock)
    ticket_id = args.get("ticket_id")
    if not isinstance(ticket_id, str):
        raise PlannerError(ErrorCode.validation, "ticket mutation needs ticket_id", {})
    if write.operation == "ticket.propose":
        if principal != Principal(PrincipalKind.ticket, ticket_id):
            raise PlannerError(
                ErrorCode.agent_forbidden, "only the Ticket's Worker can propose", {}
            )
        text = body.get("body")
        if not isinstance(text, str):
            raise PlannerError(ErrorCode.validation, "proposal body must be text", {})
        ticket = actions.file_current_proposal(
            connection, ticket_id, body=text, ctx=RequestContext(principal), clock=clock
        )
        return views.ticket_json(ticket, clock.now_unix())
    if write.operation == "ticket.edit":
        return write_patch_ticket(
            ticket_id,
            body,
            connection,
            RequestContext(principal),
            clock,
            review_transfer_notices=review_transfer_notices,
        )
    raise PlannerError(ErrorCode.validation, "unsupported transactional executor mutation", {})
