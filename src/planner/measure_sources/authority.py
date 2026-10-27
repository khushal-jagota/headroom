"""Source work follows the existing responsible Manager/Chief assignment chain."""

from __future__ import annotations

import sqlite3

from planner.core import authority
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.measure_sources.contracts import MeasureSource
from planner.tickets import data as tickets_data


def require_owner_or_chief(conn: sqlite3.Connection, principal: Principal) -> None:
    authority.require_above(conn, principal, authority.owner_only("measure_sources"))


def require_assignment(
    conn: sqlite3.Connection, principal: Principal, responsible: Principal
) -> None:
    if responsible.kind is PrincipalKind.manager:
        authority.require_in_chain(conn, principal, authority.manager(responsible.id))
        return
    if principal.kind is PrincipalKind.ticket:
        ticket = tickets_data.read_ticket(conn, principal.id)
        if ticket.manager_kind == "chief":
            return
        raise PlannerError(ErrorCode.agent_forbidden, "source belongs to another assignment")
    require_owner_or_chief(conn, principal)


def require_author(conn: sqlite3.Connection, principal: Principal, source: MeasureSource) -> None:
    require_assignment(conn, principal, source.responsible)
