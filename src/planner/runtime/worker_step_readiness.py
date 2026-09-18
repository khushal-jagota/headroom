"""Whether Panels may start this Ticket's next worker step right now.

This is the whole readiness decision and nothing else: it reads, it decides, and it
writes nothing. The loop runs it as a pre-filter and the claim writer runs it again
inside its write transaction, where the answer is final.
"""

from __future__ import annotations

import sqlite3

from planner.tickets.contracts import StageOwnershipMode, Ticket, TicketStatus
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


def worker_step_blocker(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str,
    worker_type_definition: WorkerTypeDefinition,
) -> str | None:
    """Why Panels may not start this Ticket's next worker step, or nothing when it may.

    This is the readiness decision itself, and ``is_ready_for_worker_step`` is this
    answer read as a yes or a no. The sentence exists because a caller that asked for a
    start and got none has to be told what to do about it — a supervisor restart is the
    first such caller. The first refusal wins, so the order below is the order of the
    checks, not a ranking.
    """
    membership = conn.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
        (planning_day_id, ticket.id),
    ).fetchone()
    if membership is None:
        return "the Ticket is not on today's Day"
    if worker_type_definition.is_terminal(ticket.stage):
        return f"the Stage {ticket.stage} is terminal"
    ownership_mode = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    # `empty` is the only startable control state.
    if ticket.ticket_status is not TicketStatus.empty:
        return f"the Ticket is at {ticket.ticket_status.value}, so no worker step is due"
    if ownership_mode is StageOwnershipMode.user:
        opened = conn.execute(
            "SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id = ? AND stage = ?",
            (ticket.id, ticket.stage),
        ).fetchone()
        if opened is not None:
            return "the user-owned Stage opener already ran for this Stage entry"
    if worker_type_definition.gating_field(ticket.stage) is None:
        return f"the Stage {ticket.stage} has no field for a worker to fill"
    if ticket.pending_proposal is not None:
        return "a proposal is parked for the user"
    if _closeout_lane_is_occupied(
        conn,
        ticket,
        worker_type_definition=worker_type_definition,
    ):
        return "another Closeout Ticket holds this lane"
    return None


def is_ready_for_worker_step(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    """Whether Panels may automatically start this Ticket's next worker step now."""
    return (
        worker_step_blocker(
            conn,
            ticket,
            planning_day_id=planning_day_id,
            worker_type_definition=worker_type_definition,
        )
        is None
    )
