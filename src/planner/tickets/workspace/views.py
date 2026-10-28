"""Coherent selected controls and individual field reads."""

from __future__ import annotations

import sqlite3
from typing import cast

from planner.core import ticket_blocks
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import data
from planner.tickets.views import (
    _coherent_read,
    blocker_summary_json,
    launch_validation_json,
    ticket_json,
)
from planner.tickets.workspace.contracts import WorkspaceFieldBody, WorkspaceTicket
from planner.worker_types.configuration import configured_worker_type_registry


def workspace_ticket(conn: sqlite3.Connection, ticket_id: str) -> WorkspaceTicket:
    with _coherent_read(conn):
        ticket = data.read_ticket(conn, ticket_id)
        definition = configured_worker_type_registry().require(ticket.worker_type)
        gate = definition.gating_field(ticket.stage)
        detail = ticket_json(ticket, 0)
        detail["launch_validation"] = launch_validation_json(conn, ticket_id)
        detail["field_values"] = {gate: ticket.field_values.get(gate, "")} if gate else {}
        detail["field_summaries"] = {
            str(row["field"]): {
                "revision": str(row["revision"]),
                "byte_count": int(row["byte_count"]),
            }
            for row in conn.execute(
                "SELECT field,revision,byte_count FROM ticket_workspace_fields WHERE ticket_id=?",
                (ticket_id,),
            )
        }
        blockers = ticket_blocks.blocker_summary(conn, ticket_id)
        detail.update(
            record_sha256=data.ticket_record_sha256(conn, ticket_id),
            blocked=blockers.blocked,
            blocker_summary=blocker_summary_json(blockers),
            employee_configuration_editable=data.employee_configuration_editable(ticket),
        )
        return cast(WorkspaceTicket, detail)


def field_revision(conn: sqlite3.Connection, ticket_id: str, field: str) -> str:
    row = conn.execute("SELECT worker_type FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})
    definition = configured_worker_type_registry().require(str(row[0]))
    if field not in definition.field_ids():
        raise PlannerError(ErrorCode.not_found, "Ticket field not found", {"field": field})
    row = conn.execute(
        "SELECT revision FROM ticket_workspace_fields WHERE ticket_id=? AND field=?",
        (ticket_id, field),
    ).fetchone()
    return str(row[0] if row else 0)


def workspace_field(conn: sqlite3.Connection, ticket_id: str, field: str) -> WorkspaceFieldBody:
    with _coherent_read(conn):
        revision = field_revision(conn, ticket_id, field)
        row = conn.execute(
            "SELECT value FROM tickets,json_each(tickets.field_values) "
            "WHERE tickets.id=? AND key=?",
            (ticket_id, field),
        ).fetchone()
        return {
            "ticket_id": ticket_id,
            "field": field,
            "revision": revision,
            "value": str(row[0]) if row else "",
        }
