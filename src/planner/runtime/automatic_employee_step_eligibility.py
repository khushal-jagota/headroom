"""The complete Automatic Employee-step eligibility decision."""

from __future__ import annotations

import json
import sqlite3

from planner.core import links as core_links
from planner.core.contracts import EventKind
from planner.runtime.employee_step_repository import SqliteEmployeeStepRepository
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
            "AND t.ticket_status != 'empty' "
            "AND CASE WHEN t.sprint_item_id IS NOT NULL THEN si.project_id "
            "ELSE t.project_id END IS ? LIMIT 1",
            (ticket.id, worker_type, closeout_stage, effective_project_id),
        ).fetchone()
        is not None
    )


def _latest_current_paired_stage_marker_event(
    conn: sqlite3.Connection,
    ticket: Ticket,
) -> tuple[int, str] | None:
    latest: tuple[int, str] | None = None
    rows = conn.execute(
        "SELECT id, kind, payload FROM events WHERE entity_id = ? ORDER BY id",
        (ticket.id,),
    ).fetchall()
    for row in rows:
        kind = str(row["kind"])
        if kind not in {
            EventKind.ticket_created.value,
            EventKind.stage_changed.value,
            EventKind.stage_ownership_changed.value,
        }:
            continue
        payload = json.loads(str(row["payload"]))
        if kind == EventKind.ticket_created.value and payload.get("stage") == ticket.stage:
            latest = (int(row["id"]), kind)
        elif (
            kind == EventKind.stage_changed.value
            and payload.get("to_stage") == ticket.stage
        ):
            latest = (int(row["id"]), kind)
        elif (
            kind == EventKind.stage_ownership_changed.value
            and payload.get("stage") == ticket.stage
            and payload.get("effective_ownership_mode") == StageOwnershipMode.paired.value
            and (
                "previous_effective_ownership_mode" not in payload
                or payload.get("previous_effective_ownership_mode")
                != StageOwnershipMode.paired.value
            )
        ):
            latest = (int(row["id"]), kind)
    return latest


def _has_worker_step_started_in_event_range(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    after_event_id: int | None = None,
) -> bool:
    clauses = ["entity_id = ?", "kind = ?"]
    params: list[object] = [ticket_id, EventKind.employee_step_started.value]
    if after_event_id is not None:
        clauses.append("id > ?")
        params.append(after_event_id)

    where_clause = " AND ".join(clauses)
    row = conn.execute(
        f"SELECT id, payload FROM events WHERE {where_clause} ORDER BY id",
        tuple(params),
    ).fetchone()
    return row is not None


def _paired_status_allows_automatic_opening(
    conn: sqlite3.Connection,
    ticket: Ticket,
) -> bool:
    if ticket.ticket_status is TicketStatus.empty:
        return True
    if ticket.ticket_status is not TicketStatus.paired_work:
        return False
    marker = _latest_current_paired_stage_marker_event(conn, ticket)
    if marker is None:
        return ticket.employee_session_id is None
    marker_event_id, _marker_kind = marker
    if _has_worker_step_started_in_event_range(
        conn,
        ticket.id,
        after_event_id=marker_event_id,
    ):
        return False
    return True


def is_eligible_for_automatic_employee_step(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    """Whether Planner may automatically start this Ticket's next Employee step now."""
    membership = conn.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
        (planning_day_id, ticket.id),
    ).fetchone()
    if membership is None:
        return False
    if SqliteEmployeeStepRepository().running_exists(conn, ticket.id):
        return False
    if worker_type_definition.is_terminal(ticket.stage):
        return False
    ownership_mode = machine.effective_stage_ownership_mode(
        ticket.stage,
        ticket.stage_ownership_overrides,
        worker_type_definition=worker_type_definition,
        default_stage_ownership_mode=ticket.default_stage_ownership_mode,
    )
    if ticket.ticket_status is TicketStatus.needs_user:
        return False
    if ticket.ticket_status is TicketStatus.proposal_discussion:
        return False
    if ownership_mode is StageOwnershipMode.worker:
        if ticket.ticket_status is not TicketStatus.empty:
            return False
    elif ownership_mode is StageOwnershipMode.paired:
        if not _paired_status_allows_automatic_opening(conn, ticket):
            return False
    else:
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
    if core_links.is_blocked(conn, ticket.id):
        return False
    if _closeout_lane_is_occupied(
        conn,
        ticket,
        worker_type_definition=worker_type_definition,
    ):
        return False
    return True
