"""Source control follows its explicit responsible principal and repair designation."""

from __future__ import annotations

import sqlite3

from planner.core import authority
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.measure_sources.contracts import MeasureSource
from planner.tickets import data as tickets_data
from planner.worker_types.store import read_definition


def require_owner_or_chief(conn: sqlite3.Connection, principal: Principal) -> None:
    authority.require_above(conn, principal, authority.owner_only("measure_sources"))


def require_responsible(
    conn: sqlite3.Connection, principal: Principal, source: MeasureSource
) -> None:
    target = (
        authority.manager(source.responsible.id)
        if source.responsible.kind is PrincipalKind.manager
        else authority.owner_only("measure_sources")
    )
    authority.require_above_or_self(conn, principal, target)


def require_repair_ticket(conn: sqlite3.Connection, source: MeasureSource, ticket_id: str) -> None:
    ticket = tickets_data.read_ticket(conn, ticket_id)
    definition = read_definition(conn, ticket.worker_type)
    if definition.is_terminal(ticket.stage):
        raise PlannerError(ErrorCode.agent_forbidden, "repair Ticket is terminal")
    if (source.responsible.kind.value, source.responsible.id) != (
        ticket.manager_kind,
        ticket.manager_id if ticket.manager_kind == "manager" else "chief",
    ):
        raise PlannerError(
            ErrorCode.agent_forbidden, "repair Ticket has a different responsible principal"
        )


def require_author(conn: sqlite3.Connection, principal: Principal, source: MeasureSource) -> None:
    if principal.kind is PrincipalKind.ticket:
        if principal.id != source.repair_ticket_id:
            raise PlannerError(
                ErrorCode.agent_forbidden, "Ticket is not the designated source author"
            )
        require_repair_ticket(conn, source, principal.id)
        return
    require_responsible(conn, principal, source)
