"""Ticket read-view assembly: the per-entity serializers (ticket/run/event JSON),
the ticket-detail composite, the board (§10.3), the queues (§4.5), and the copy-text
block (§10.4). Pure read assembly — no FastAPI, no pydantic, no writes, and no import
of any api module or of sprints/views. The api layer wires sprint item rows into the
queue functions as parameters, which keeps the import graph acyclic (sprints/views ->
tickets/views is the only cross-views edge)."""

from __future__ import annotations

import json
import sqlite3

from planner.core import links as core_links
from planner.core.contracts import BlockerSummary, JsonDict
from planner.sprints.contracts import ItemStatus
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    CodingStage,
    FieldSlot,
    Ticket,
    TicketStatus,
)
from planner.tickets.logic import coding_bridge, fields_codec, machine

# §7.2 priority band: P0 first. The board reuses the same triple the dispatcher orders by.
_PRIORITY_RANK = ("P0", "P1", "P2", "P3")
_TICKET_CLOSED = {CodingStage.done.value, CodingStage.dropped.value}
_ITEM_CLOSED = {ItemStatus.done.value}


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
        "implementer": ticket.implementer.value if ticket.implementer is not None else None,
        "chat_session_key": ticket.chat_session_key,
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
    defn = coding_bridge.require(ticket.worker_type)

    def show(value: str | None) -> str:
        return value if value else "(none)"

    def slot(field_id: str) -> FieldSlot:
        return fields_codec.get_slot(fields, field_id)

    blocker_summary = core_links.blocker_summary(conn, ticket_id)
    blocked_by_rows = blocker_summary.blocked_by
    blocks_rows = blocker_summary.blocks
    blocked_by_block = (
        "\n".join(
            f"- {'active' if row.active else 'cleared'}: {row.title} "
            f"({row.ticket_id}, {row.stage})"
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
        for field_id in coding_bridge.views.field_ids(defn)
    )
    return (
        f"{ticket.title}\n"
        f"stage: {str(ticket.stage)}\n"
        f"priority: {ticket.priority.value}\n"
        f"implementer: {ticket.implementer.value if ticket.implementer is not None else '(none)'}\n"
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
        "tickets.ticket_status, "
        "tickets.created_at, tickets.updated_at FROM tickets "
        "LEFT JOIN projects AS ticket_projects ON ticket_projects.id = tickets.project_id "
        "LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id "
        "LEFT JOIN projects AS parent_projects ON parent_projects.id = sprint_items.project_id "
        "WHERE tickets.stage != 'dropped' "
        "AND tickets.id IN (SELECT ticket_id FROM day_tickets WHERE day_id = ?)",
        (day_id,),
    ).fetchall()
    coding_order = coding_bridge.views.stage_ids(coding_bridge.coding_definition())
    column_order: list[str] = list(coding_order)
    by_stage: dict[str, list[tuple[tuple[int, int, str, int], JsonDict]]] = {
        sid: [] for sid in column_order
    }
    for row in rows:
        stage = str(row["stage"])
        priority = str(row["priority"])
        deadline = str(row["deadline"]) if row["deadline"] is not None else None
        worker_type = str(row["worker_type"])
        defn = coding_bridge.require(worker_type)
        fields = fields_codec.fields_from_json(str(row["fields"]), defn)
        gating_field_id = coding_bridge.views.gating_field(defn, stage)
        gating_field_label = next(
            (f.label for f in defn.fields if f.id == gating_field_id), None
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
                stage, fields, definition=defn
            ),
            "ticket_status": str(row["ticket_status"]),
            "worker_type": worker_type,
            "stage": stage,
            "stage_label": coding_bridge.views.require_stage(defn, stage).label,
            "gating_field": gating_field_id,
            "gating_field_label": gating_field_label,
            "is_done": stage == coding_bridge.views.linear_terminal_stage_id(defn),
            "is_dropped": stage == defn.dropped_stage.id,
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


# --- queues (§4.5) -------------------------------------------------------------


def _entity_type(entity_id: str) -> str:
    return "ticket" if entity_id.split("_", 1)[0] == "t" else "item"


def _approval_digest(tickets: list[JsonDict], items: list[JsonDict]) -> list[JsonDict]:
    digest: list[JsonDict] = []
    for row in tickets:
        stage = str(row["stage"])
        if row.get("ticket_status") == TicketStatus.agent_running_step.value:
            continue
        defn = coding_bridge.require(str(row.get("worker_type")))
        gating_field_id = coding_bridge.views.gating_field(defn, stage)
        if gating_field_id is None:
            continue
        fields = row["fields"]
        if not isinstance(fields, dict):
            continue
        slot = fields.get(gating_field_id)
        if not isinstance(slot, dict):
            continue
        proposal = slot.get("proposal")
        if not isinstance(proposal, dict):
            continue
        digest.append(
            {
                "entity_id": row["id"],
                "kind": gating_field_id,
                "waiting_since": proposal["created_at"],
            }
        )
    digest.sort(key=lambda entry: entry["waiting_since"])
    return digest


def _overdue_digest(
    tickets: list[JsonDict], items: list[JsonDict], today_iso: str
) -> list[JsonDict]:
    result: list[JsonDict] = []
    for row in tickets:
        deadline = row["deadline"]
        stage = str(row["stage"])
        if deadline is not None and str(deadline) < today_iso and stage not in _TICKET_CLOSED:
            result.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "stage": stage,
                    "priority": row["priority"],
                }
            )
    for row in items:
        deadline = row["deadline"]
        status = str(row["status"])
        if deadline is not None and str(deadline) < today_iso and status not in _ITEM_CLOSED:
            result.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": status,
                    "priority": row["priority"],
                }
            )
    return result


def _approvals(
    conn: sqlite3.Connection,
    item_approval_rows: list[JsonDict],
    *,
    day_id: str,
) -> list[JsonDict]:
    ticket_rows = conn.execute(
        "SELECT id, title, stage, worker_type, ticket_status, fields, updated_at FROM tickets "
        "WHERE stage NOT IN ('done','dropped') "
        "AND id IN (SELECT ticket_id FROM day_tickets WHERE day_id = ?) ORDER BY id",
        (day_id,),
    ).fetchall()
    ticket_digest: list[dict[str, object]] = []
    ticket_title: dict[str, str] = {}
    for r in ticket_rows:
        tid = str(r["id"])
        ticket_digest.append(
            {
                "id": tid,
                "stage": str(r["stage"]),
                "worker_type": str(r["worker_type"]),
                "ticket_status": str(r["ticket_status"]),
                "fields": json.loads(str(r["fields"])),
                "updated_at": int(r["updated_at"]),
            }
        )
        ticket_title[tid] = str(r["title"])
    item_digest: list[dict[str, object]] = []
    item_title: dict[str, str] = {}
    for r in item_approval_rows:
        iid = str(r["id"])
        item_digest.append({"id": iid})
        item_title[iid] = str(r["title"])
    digest = _approval_digest(ticket_digest, item_digest)
    digest.sort(key=lambda e: e["waiting_since"])
    result: list[JsonDict] = []
    for entry in digest:
        eid = str(entry["entity_id"])
        etype = _entity_type(eid)
        title = ticket_title.get(eid) if etype == "ticket" else item_title.get(eid)
        result.append(
            {
                "entity_id": eid,
                "entity_type": etype,
                "kind": entry["kind"],
                "title": title,
                "waiting_since": entry["waiting_since"],
            }
        )
    return result


def _overdue(
    conn: sqlite3.Connection, today_iso: str, item_overdue_rows: list[JsonDict]
) -> list[JsonDict]:
    ticket_rows = conn.execute(
        "SELECT id, title, stage, priority, deadline FROM tickets WHERE deadline IS NOT NULL "
        "ORDER BY deadline ASC, id"
    ).fetchall()
    ticket_dicts: list[dict[str, object]] = [
        {
            "id": str(r["id"]),
            "title": str(r["title"]),
            "stage": str(r["stage"]),
            "priority": str(r["priority"]),
            "deadline": str(r["deadline"]) if r["deadline"] is not None else None,
        }
        for r in ticket_rows
    ]
    item_dicts: list[dict[str, object]] = [dict(r) for r in item_overdue_rows]
    digest = _overdue_digest(ticket_dicts, item_dicts, today_iso)
    ticket_deadline = {str(d["id"]): d["deadline"] for d in ticket_dicts}
    item_deadline = {str(d["id"]): d["deadline"] for d in item_dicts}
    result: list[JsonDict] = []
    for entry in digest:
        eid = str(entry["id"])
        etype = _entity_type(eid)
        deadline = ticket_deadline.get(eid) if etype == "ticket" else item_deadline.get(eid)
        result.append(
            {
                "id": eid,
                "entity_type": etype,
                "title": entry["title"],
                **(
                    {"stage": entry["stage"]}
                    if etype == "ticket"
                    else {"status": entry["status"]}
                ),
                "priority": entry["priority"],
                "deadline": deadline,
            }
        )
    return result


def queues_view(
    conn: sqlite3.Connection,
    now: int,
    today_iso: str,
    item_approval_rows: list[JsonDict],
    item_overdue_rows: list[JsonDict],
    *,
    day_id: str,
) -> JsonDict:
    running_agents = conn.execute(
        "SELECT COUNT(*) AS count FROM tickets WHERE ticket_status = 'agent_running_step'"
    ).fetchone()
    return {
        "approvals": _approvals(conn, item_approval_rows, day_id=day_id),
        "overdue": _overdue(conn, today_iso, item_overdue_rows),
        "running_agents": int(running_agents["count"] if running_agents is not None else 0),
    }
