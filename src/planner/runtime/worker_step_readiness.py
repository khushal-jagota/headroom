"""Whether Panels may start this Ticket's next worker step right now.

This is the whole readiness decision and nothing else: it reads, it decides, and it
writes nothing. The loop runs it as a pre-filter and the claim writer runs it again
inside its write transaction, where the answer is final.
"""

from __future__ import annotations

import sqlite3

from planner.tickets.contracts import AtCap, StageOwnershipMode, Ticket, TicketStatus
from planner.tickets.logic import machine
from planner.worker_types.contracts import WorkerTypeDefinition

CloseoutLaneIdentity = tuple[str | None, str]


def closeout_lane_identity(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> CloseoutLaneIdentity | None:
    """Return the effective project-and-Worker-type lane for a Closeout Ticket."""
    if worker_type_definition.gating_field(ticket.stage) != "closeout":
        return None
    effective_project_id = ticket.project_id
    if ticket.sprint_item_id is not None:
        row = conn.execute(
            "SELECT project_id FROM sprint_items WHERE id = ?",
            (ticket.sprint_item_id,),
        ).fetchone()
        effective_project_id = str(row["project_id"]) if row is not None else None
    return effective_project_id, ticket.worker_type


def _closeout_lane_is_occupied(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    lane = closeout_lane_identity(
        conn,
        ticket,
        worker_type_definition=worker_type_definition,
    )
    if lane is None:
        return False
    effective_project_id, worker_type = lane
    closeout_stage = worker_type_definition.stage_gated_by("closeout")
    return (
        conn.execute(
            "SELECT 1 FROM tickets t "
            "LEFT JOIN sprint_items si ON si.id = t.sprint_item_id "
            "WHERE t.id != ? AND t.worker_type = ? AND t.stage = ? "
            # blocked stands in for empty: a blocked Closeout Ticket is resting, so it
            # does not occupy the lane.
            "AND t.ticket_status NOT IN ('empty', 'blocked') "
            "AND CASE WHEN t.sprint_item_id IS NOT NULL THEN si.project_id "
            "ELSE t.project_id END IS ? LIMIT 1",
            (ticket.id, worker_type, closeout_stage, effective_project_id),
        ).fetchone()
        is not None
    )


def is_ready_for_worker_step(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    """Whether Panels may automatically start this Ticket's next worker step now."""
    membership = conn.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
        (planning_day_id, ticket.id),
    ).fetchone()
    if membership is None:
        return False
    if worker_type_definition.is_terminal(ticket.stage):
        return False
    ownership_mode = machine.effective_stage_ownership_mode(
        ticket.stage,
        ticket.stage_ownership_overrides,
        worker_type_definition=worker_type_definition,
        default_stage_ownership_mode=ticket.default_stage_ownership_mode,
    )
    if ownership_mode is StageOwnershipMode.user:
        return False
    # `empty` is the only startable status. It covers blocked, needs_user, paired,
    # awaiting_approval, agent, user, and errored in one gate.
    if ticket.ticket_status is not TicketStatus.empty:
        return False
    if worker_type_definition.gating_field(ticket.stage) is None:
        return False
    if machine.has_pending_parked_proposal(
        ticket,
        worker_type_definition=worker_type_definition,
    ):
        return False
    if (
        machine.at_or_beyond_ceiling(
            ticket.stage,
            ticket.ceiling,
            worker_type_definition=worker_type_definition,
        )
        and ticket.at_cap is AtCap.stop
    ):
        return False
    if _closeout_lane_is_occupied(
        conn,
        ticket,
        worker_type_definition=worker_type_definition,
    ):
        return False
    return True
