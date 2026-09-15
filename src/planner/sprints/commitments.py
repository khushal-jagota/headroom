"""Sprint commitments and one atomic, explicitly selected carry-forward operation."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.sprints import data
from planner.sprints.contracts import CarryOutcomeResult, SprintOutcomeCommitment
from planner.sprints.logic.commitments import validate_carry_selection
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.worker_types.configuration import configured_worker_type_registry


def set_commitment(
    conn: sqlite3.Connection,
    sprint_id: str,
    outcome_id: str,
    *,
    committed: bool,
    admit: Callable[[], None],
) -> SprintOutcomeCommitment:
    with data._tx(conn):
        admit()
        data.read_sprint(conn, sprint_id)
        data.read_item(conn, outcome_id)
        if committed:
            conn.execute(
                "INSERT OR IGNORE INTO sprint_outcomes(sprint_id,outcome_id) VALUES (?,?)",
                (sprint_id, outcome_id),
            )
        else:
            conn.execute(
                "DELETE FROM sprint_outcomes WHERE sprint_id=? AND outcome_id=?",
                (sprint_id, outcome_id),
            )
    return SprintOutcomeCommitment(sprint_id=sprint_id, outcome_id=outcome_id)


def carry_outcome(
    conn: sqlite3.Connection,
    source_sprint_id: str,
    target_sprint_id: str,
    outcome_id: str,
    ticket_ids: list[str],
    *,
    principal: Principal,
    now: int,
    admit: Callable[[], None],
) -> CarryOutcomeResult:
    with data._tx(conn):
        admit()
        data.read_sprint(conn, source_sprint_id)
        data.read_sprint(conn, target_sprint_id)
        data.read_item(conn, outcome_id)
        if len(ticket_ids) != len(set(ticket_ids)):
            raise PlannerError(ErrorCode.validation, "carry selection contains duplicate Tickets")
        if (
            conn.execute(
                "SELECT 1 FROM sprint_outcomes WHERE sprint_id=? AND outcome_id=?",
                (source_sprint_id, outcome_id),
            ).fetchone()
            is None
        ):
            raise PlannerError(
                ErrorCode.validation, "Outcome is not committed to the source Sprint"
            )
        tickets = [tickets_data.read_ticket(conn, ticket_id) for ticket_id in ticket_ids]
        registry = configured_worker_type_registry()
        terminal_ids = frozenset(
            t.id for t in tickets if registry.require(t.worker_type).is_terminal(t.stage)
        )
        validate_carry_selection(
            source_sprint_id, target_sprint_id, outcome_id, tickets, terminal_ids
        )
        conn.execute(
            "INSERT OR IGNORE INTO sprint_outcomes(sprint_id,outcome_id) VALUES (?,?)",
            (target_sprint_id, outcome_id),
        )
        for ticket in tickets:
            if ticket.sprint_id == source_sprint_id:
                tickets_data.edit_ticket(
                    conn,
                    ticket.id,
                    edit={"sprint_id": target_sprint_id},
                    title_max_chars=TITLE_MAX_CHARS,
                    principal=principal,
                    now=now,
                )
        return CarryOutcomeResult(
            source_sprint_id=source_sprint_id,
            target_sprint_id=target_sprint_id,
            outcome_id=outcome_id,
            ticket_ids=ticket_ids,
        )
