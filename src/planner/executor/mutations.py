"""Explicit CLI mutation registry. These names call canonical domain writers."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.executor.contracts import CliWrite, JsonObject
from planner.tickets import actions, views


def cli_mutation(method: str, path: str, body: Any) -> tuple[str, JsonObject]:
    """Translate a supported CLI route into a registry operation, never a proxy route."""
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
    connection: sqlite3.Connection, write: CliWrite, principal: Principal, clock: Clock
) -> JsonObject:
    """Effect and receipt use the caller's open transaction; no native I/O here."""
    from planner.tickets.api import write_create_ticket, write_patch_ticket

    args = write.arguments
    body = args.get("body")
    if not isinstance(body, dict):
        raise PlannerError(ErrorCode.validation, "ticket mutation needs a body", {})
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
        return write_patch_ticket(ticket_id, body, connection, RequestContext(principal), clock)
    raise PlannerError(ErrorCode.validation, "unsupported transactional executor mutation", {})
