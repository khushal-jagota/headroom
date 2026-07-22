"""Ticket read-view assembly: per-entity serializers, Ticket detail, the board,
Ticket-only Review, and copy text. Pure read assembly — no FastAPI, pydantic, writes,
or imports from an API module."""

from __future__ import annotations

import json
import sqlite3

from planner.core import links as core_links
from planner.core.contracts import BlockerSummary, JsonDict
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    FieldSlot,
    Ticket,
    TicketStatus,
    WorkspaceActivityState,
    WorkspaceDotFacts,
)
from planner.tickets.logic import fields_codec, machine
from planner.tickets.logic.workspace_dot import workspace_dot_state
from planner.worker_types.configuration import configured_worker_type_registry

# §7.2 priority band: P0 first. The board reuses the same triple the dispatcher orders by.
_PRIORITY_RANK = ("P0", "P1", "P2", "P3")


def _prio_rank(priority: str) -> int:
    return _PRIORITY_RANK.index(priority)


# --- serializers ---------------------------------------------------------------


def blocker_summary_json(summary: BlockerSummary) -> JsonDict:
    return {
        "blocked": summary.blocked,
        "blocked_by": [
            {
                "ticket_id": row.ticket_id,
                "title": row.title,
                "stage": row.stage,
                "active": row.active,
                "href": row.href,
            }
            for row in summary.blocked_by
        ],
        "blocks": [
            {
                "target_id": row.target_id,
                "target_kind": row.target_kind,
                "title": row.title,
                "active": row.active,
                "href": row.href,
            }
            for row in summary.blocks
        ],
    }


def ticket_json(ticket: Ticket, now: int) -> JsonDict:
    """§3.3 ticket serializer."""
    return {
        "id": ticket.id,
        "title": ticket.title,
        "worker_type": ticket.worker_type,
        "employee_backend": ticket.employee_backend,
        "employee_launch_model": ticket.employee_launch_model,
        "employee_launch_reasoning_effort": ticket.employee_launch_reasoning_effort,
        "stage": str(ticket.stage),
        "priority": ticket.priority.value,
        "deadline": ticket.deadline,
        "project_id": ticket.project_id,
        "project": ticket.project_name,
        "sprint_item_id": ticket.sprint_item_id,
        "sprint_id": ticket.sprint_id,
        "recap": ticket.recap,
        "ceiling": str(ticket.ceiling),
        "at_cap": ticket.at_cap.value,
        "ticket_status": ticket.ticket_status.value,
        "backend_error": ticket.backend_error,
        "stage_ownership_overrides": {
            stage: mode.value for stage, mode in ticket.stage_ownership_overrides.items()
        },
        "default_stage_ownership_mode": (
            ticket.default_stage_ownership_mode.value
            if ticket.default_stage_ownership_mode is not None
            else None
        ),
        "effective_stage_ownership_mode": (
            ticket.effective_stage_ownership_mode.value
            if ticket.effective_stage_ownership_mode is not None
            else None
        ),
        "employee_session_id": ticket.employee_session_id,
        "alias": ticket.alias,
        "fields": json.loads(fields_codec.fields_to_json(ticket.fields)),
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
    }


def event_json(row: sqlite3.Row) -> JsonDict:
    return {
        "id": int(row["id"]),
        "entity_id": str(row["entity_id"]),
        "kind": str(row["kind"]),
        "payload": json.loads(str(row["payload"])),
        "created_at": int(row["created_at"]),
    }


# --- ticket reads --------------------------------------------------------------


def list_tickets(
    conn: sqlite3.Connection,
    now: int,
    *,
    stage: str | None,
    project_id: str | None,
    sprint_id: str | None,
    sprint_item_id: str | None,
    day_id: str | None = None,
) -> list[JsonDict]:
    clauses: list[str] = []
    params: list[str] = []
    if stage is not None:
        clauses.append("stage = ?")
        params.append(str(stage))
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    if sprint_id is not None:
        if sprint_id == "null":
            clauses.append("sprint_id IS NULL")
        else:
            clauses.append("sprint_id = ?")
            params.append(sprint_id)
    if sprint_item_id is not None:
        clauses.append("sprint_item_id = ?")
        params.append(sprint_item_id)
    if day_id is not None:  # scope to one day's board via the day_tickets join
        clauses.append("id IN (SELECT ticket_id FROM day_tickets WHERE day_id = ?)")
        params.append(day_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT id FROM tickets" + where + " ORDER BY created_at ASC, id", tuple(params)
    ).fetchall()
    return [ticket_json(tickets_data.read_ticket(conn, str(row["id"])), now) for row in rows]


def ticket_detail(conn: sqlite3.Connection, ticket_id: str, now: int) -> JsonDict:
    ticket = tickets_data.read_ticket(conn, ticket_id)
    detail = ticket_json(ticket, now)
    day_rows = conn.execute(
        "SELECT day_id FROM day_tickets WHERE ticket_id = ? ORDER BY day_id ASC", (ticket_id,)
    ).fetchall()
    blocker_summary = core_links.blocker_summary(conn, ticket_id)
    detail.update(
        {
            "blocked": blocker_summary.blocked,
            "blocker_summary": blocker_summary_json(blocker_summary),
            "effective_sprint_id": tickets_data.get_effective_sprint_id(conn, ticket_id),
            "day_ids": [str(r["day_id"]) for r in day_rows],
            "employee_configuration_editable": tickets_data.employee_configuration_editable(
                conn, ticket
            ),
        }
    )
    return detail


def list_events_for_entity(conn: sqlite3.Connection, entity_id: str, limit: int) -> list[JsonDict]:
    rows = conn.execute(
        "SELECT id, entity_id, kind, payload, created_at FROM events WHERE entity_id = ? "
        "ORDER BY id ASC LIMIT ?",
        (entity_id, limit),
    ).fetchall()
    return [event_json(r) for r in rows]


def copy_text(conn: sqlite3.Connection, ticket_id: str) -> str:
    ticket = tickets_data.read_ticket(conn, ticket_id)
    fields = ticket.fields
    worker_type_definition = configured_worker_type_registry().require(ticket.worker_type)

    def show(value: str | None) -> str:
        return value if value else "(none)"

    def slot(field_id: str) -> FieldSlot:
        return fields_codec.get_slot(fields, field_id)

    blocker_summary = core_links.blocker_summary(conn, ticket_id)
    blocked_by_rows = blocker_summary.blocked_by
    blocks_rows = blocker_summary.blocks
    blocked_by_block = (
        "\n".join(
            f"- {'active' if row.active else 'cleared'}: {row.title} ({row.ticket_id}, {row.stage})"
            for row in blocked_by_rows
        )
        if blocked_by_rows
        else "(none)"
    )
    blocks_block = (
        "\n".join(
            f"- {'active' if row.active else 'cleared'}: {row.title} "
            f"({row.target_id}, {row.target_kind})"
            for row in blocks_rows
        )
        if blocks_rows
        else "(none)"
    )
    field_blocks = "".join(
        f"{field_id}:\n{show(slot(field_id).value)}\n"
        f"{field_id}_user_note:\n{show(slot(field_id).user_note)}\n"
        f"\n"
        for field_id in worker_type_definition.field_ids()
    )
    return (
        f"{ticket.title}\n"
        f"stage: {str(ticket.stage)}\n"
        f"priority: {ticket.priority.value}\n"
        f"employee_backend: {ticket.employee_backend}\n"
        "owner: "
        f"{ticket.effective_stage_ownership_mode.value if ticket.effective_stage_ownership_mode is not None else '(none)'}\n"  # noqa: E501
        f"\n"
        f"{field_blocks}"
        f"recap:\n{show(ticket.recap)}\n"
        f"\n"
        f"blocked_by:\n{blocked_by_block}\n"
        f"blocks:\n{blocks_block}\n"
    )


# --- board (§10.3) -------------------------------------------------------------


def board_view(conn: sqlite3.Connection, now: int, *, day_id: str) -> JsonDict:
    rows = conn.execute(
        "SELECT tickets.id, tickets.title, tickets.stage, tickets.priority, tickets.deadline, "
        "tickets.project_id, ticket_projects.name AS project_name, tickets.sprint_item_id, "
        "sprint_items.project_id AS parent_project_id, "
        "parent_projects.name AS parent_project_name, tickets.fields, tickets.worker_type, "
        "tickets.employee_backend, "
        "tickets.ticket_status, "
        "tickets.backend_error, "
        "ticket_conversation_projections.latest_activity_state, "
        "ticket_conversation_projections.has_completed_response_awaiting_user, "
        "ticket_conversation_projections.has_pending_permission, "
        "tickets.created_at, tickets.updated_at FROM tickets "
        "LEFT JOIN projects AS ticket_projects ON ticket_projects.id = tickets.project_id "
        "LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id "
        "LEFT JOIN projects AS parent_projects ON parent_projects.id = sprint_items.project_id "
        "LEFT JOIN ticket_conversation_projections "
        "ON ticket_conversation_projections.ticket_id = tickets.id "
        "WHERE tickets.stage != 'dropped' "
        "AND tickets.id IN (SELECT ticket_id FROM day_tickets WHERE day_id = ?)",
        (day_id,),
    ).fetchall()
    registry = configured_worker_type_registry()
    coding_order = registry.require("coding").stage_ids()
    column_order: list[str] = list(coding_order)
    by_stage: dict[str, list[tuple[tuple[int, int, str, int], JsonDict]]] = {
        sid: [] for sid in column_order
    }
    for row in rows:
        stage = str(row["stage"])
        priority = str(row["priority"])
        deadline = str(row["deadline"]) if row["deadline"] is not None else None
        worker_type = str(row["worker_type"])
        worker_type_definition = registry.require(worker_type)
        fields = fields_codec.declared_fields_from_json(
            str(row["fields"]), worker_type_definition.field_ids()
        )
        gating_field_id = worker_type_definition.gating_field(stage)
        gating_field_label = (
            worker_type_definition.field_definition(gating_field_id).label
            if gating_field_id is not None
            else None
        )
        parent_project_id = (
            str(row["parent_project_id"]) if row["parent_project_id"] is not None else None
        )
        parent_project_name = (
            str(row["parent_project_name"]) if row["parent_project_name"] is not None else None
        )
        ticket_project_id = str(row["project_id"]) if row["project_id"] is not None else None
        ticket_project_name = str(row["project_name"]) if row["project_name"] is not None else None
        is_parented = row["sprint_item_id"] is not None
        group_project_id = parent_project_id if is_parented else ticket_project_id
        group_project_name = parent_project_name if is_parented else ticket_project_name
        card: JsonDict = {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "priority": priority,
            "deadline": deadline,
            "project_id": ticket_project_id,
            "project": ticket_project_name,
            "group_project_id": group_project_id,
            "group_project": group_project_name,
            "activity_at": int(row["updated_at"]),
            "has_pending_proposal": machine.has_pending_gating_proposal(
                stage,
                fields,
                worker_type_definition=worker_type_definition,
            ),
            "ticket_status": str(row["ticket_status"]),
            "backend_error": (
                str(row["backend_error"]) if row["backend_error"] is not None else None
            ),
            "worker_type": worker_type,
            "employee_backend": str(row["employee_backend"]),
            "stage": stage,
            "stage_label": worker_type_definition.stage_definition(stage).label,
            "gating_field": gating_field_id,
            "gating_field_label": gating_field_label,
            "is_done": stage == worker_type_definition.completed_stage(),
            "is_dropped": stage == worker_type_definition.dropped_stage.id,
            "workspace_dot_state": workspace_dot_state(
                WorkspaceDotFacts(
                    ticket_status=TicketStatus(str(row["ticket_status"])),
                    backend_error=(
                        str(row["backend_error"])
                        if row["backend_error"] is not None
                        else None
                    ),
                    has_pending_proposal=machine.has_pending_gating_proposal(
                        stage,
                        fields,
                        worker_type_definition=worker_type_definition,
                    ),
                    latest_activity_state=(
                        WorkspaceActivityState(str(row["latest_activity_state"]))
                        if row["latest_activity_state"] is not None
                        else None
                    ),
                    has_completed_response_awaiting_user=bool(
                        row["has_completed_response_awaiting_user"] or 0
                    ),
                    has_pending_permission=bool(row["has_pending_permission"] or 0),
                )
            ).value,
        }
        sort_key = (
            _prio_rank(priority),
            0 if deadline is not None else 1,
            deadline or "",
            int(row["created_at"]),
        )
        if stage not in by_stage:
            by_stage[stage] = []
            column_order.append(stage)
        by_stage[stage].append((sort_key, card))
    columns: list[JsonDict] = [
        {
            "stage": sid,
            "cards": [card for _, card in sorted(by_stage[sid], key=lambda item: item[0])],
        }
        for sid in column_order
    ]
    return {"columns": columns}


# --- Review --------------------------------------------------------------------


def _ticket_decisions(conn: sqlite3.Connection, *, day_id: str) -> list[JsonDict]:
    rows = conn.execute(
        "SELECT id, title, stage, worker_type, ticket_status, fields FROM tickets "
        "WHERE id IN (SELECT ticket_id FROM day_tickets WHERE day_id = ?) ORDER BY id",
        (day_id,),
    ).fetchall()
    registry = configured_worker_type_registry()
    decisions: list[JsonDict] = []
    for row in rows:
        worker_type_definition = registry.require(str(row["worker_type"]))
        stage = str(row["stage"])
        if worker_type_definition.is_terminal(stage):
            continue
        if str(row["ticket_status"]) == TicketStatus.agent_running_step.value:
            continue
        field = worker_type_definition.gating_field(stage)
        if field is None:
            continue
        fields = json.loads(str(row["fields"]))
        if not isinstance(fields, dict):
            continue
        slot = fields.get(field)
        if not isinstance(slot, dict):
            continue
        proposal = slot.get("proposal")
        if not isinstance(proposal, dict):
            continue
        decisions.append(
            {
                "ticket_id": str(row["id"]),
                "field": field,
                "title": str(row["title"]),
                "waiting_since": proposal["created_at"],
            }
        )
    decisions.sort(key=lambda decision: (decision["waiting_since"], decision["ticket_id"]))
    return decisions


def review_view(
    conn: sqlite3.Connection,
    *,
    day_id: str,
) -> JsonDict:
    running_workers = conn.execute(
        "SELECT COUNT(*) AS count FROM tickets WHERE ticket_status = 'agent_running_step'"
    ).fetchone()
    return {
        "ticket_decisions": _ticket_decisions(conn, day_id=day_id),
        "running_worker_count": int(running_workers["count"] if running_workers is not None else 0),
    }
