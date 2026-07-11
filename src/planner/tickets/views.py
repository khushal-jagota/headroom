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
from planner.core.contracts import JsonDict
from planner.sprints.contracts import ItemStatus
from planner.tickets import data as tickets_data
from planner.tickets.contracts import GATING_FIELD, STATE_ORDER, Ticket, TicketState, TicketStatus
from planner.tickets.logic import fields_codec, machine

# §7.2 priority band: P0 first. The board reuses the same triple the dispatcher orders by.
_PRIORITY_RANK = ("P0", "P1", "P2", "P3")
_TICKET_CLOSED = {TicketState.done.value, TicketState.dropped.value}
_ITEM_CLOSED = {ItemStatus.done.value}


def _prio_rank(priority: str) -> int:
    return _PRIORITY_RANK.index(priority)


# --- serializers ---------------------------------------------------------------


def ticket_json(ticket: Ticket, now: int) -> JsonDict:
    """§3.3 ticket serializer."""
    return {
        "id": ticket.id,
        "title": ticket.title,
        "state": ticket.state.value,
        "priority": ticket.priority.value,
        "deadline": ticket.deadline,
        "project_id": ticket.project_id,
        "project": ticket.project_name,
        "sprint_item_id": ticket.sprint_item_id,
        "sprint_id": ticket.sprint_id,
        "recap": ticket.recap,
        "ceiling": ticket.ceiling.value,
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
    state: TicketState | None,
    project_id: str | None,
    sprint_id: str | None,
    sprint_item_id: str | None,
    day_id: str | None = None,
) -> list[JsonDict]:
    clauses: list[str] = []
    params: list[str] = []
    if state is not None:
        clauses.append("state = ?")
        params.append(state.value)
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
    link_rows = conn.execute(
        "SELECT from_id, to_id, kind FROM links WHERE from_id = ? OR to_id = ? "
        "ORDER BY kind, from_id, to_id",
        (ticket_id, ticket_id),
    ).fetchall()
    day_rows = conn.execute(
        "SELECT day_id FROM day_tickets WHERE ticket_id = ? ORDER BY day_id ASC", (ticket_id,)
    ).fetchall()
    detail.update(
        {
            "blocked": core_links.is_blocked(conn, ticket_id),
            "effective_sprint_id": tickets_data.get_effective_sprint_id(conn, ticket_id),
            "links": [
                {"from_id": str(r["from_id"]), "to_id": str(r["to_id"]), "kind": str(r["kind"])}
                for r in link_rows
            ],
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

    def show(value: str | None) -> str:
        return value if value else "(none)"

    link_rows = conn.execute(
        "SELECT from_id, to_id, kind FROM links WHERE from_id = ? OR to_id = ? "
        "ORDER BY kind, from_id, to_id",
        (ticket_id, ticket_id),
    ).fetchall()
    if link_rows:
        links_block = "\n".join(
            f"- {str(r['kind'])}: {str(r['from_id'])} -> {str(r['to_id'])}" for r in link_rows
        )
    else:
        links_block = "(none)"
    return (
        f"{ticket.title}\n"
        f"state: {ticket.state.value}\n"
        f"priority: {ticket.priority.value}\n"
        f"implementer: {ticket.implementer.value if ticket.implementer is not None else '(none)'}\n"
        f"\n"
        f"kickoff:\n{show(fields.kickoff.value)}\n"
        f"kickoff_user_note:\n{show(fields.kickoff.user_note)}\n"
        f"\n"
        f"success:\n{show(fields.success.value)}\n"
        f"success_user_note:\n{show(fields.success.user_note)}\n"
        f"\n"
        f"approach:\n{show(fields.approach.value)}\n"
        f"approach_user_note:\n{show(fields.approach.user_note)}\n"
        f"\n"
        f"plan:\n{show(fields.plan.value)}\n"
        f"plan_user_note:\n{show(fields.plan.user_note)}\n"
        f"\n"
        f"implementation:\n{show(fields.implementation.value)}\n"
        f"implementation_user_note:\n{show(fields.implementation.user_note)}\n"
        f"\n"
        f"closeout:\n{show(fields.closeout.value)}\n"
        f"closeout_user_note:\n{show(fields.closeout.user_note)}\n"
        f"\n"
        f"recap:\n{show(ticket.recap)}\n"
        f"\n"
        f"links:\n{links_block}\n"
    )


# --- board (§10.3) -------------------------------------------------------------


def board_view(conn: sqlite3.Connection, now: int, *, day_id: str) -> JsonDict:
    rows = conn.execute(
        "SELECT tickets.id, tickets.title, tickets.state, tickets.priority, tickets.deadline, "
        "tickets.project_id, ticket_projects.name AS project_name, tickets.sprint_item_id, "
        "sprint_items.project_id AS parent_project_id, "
        "parent_projects.name AS parent_project_name, tickets.fields, tickets.ticket_status, "
        "tickets.created_at, tickets.updated_at FROM tickets "
        "LEFT JOIN projects AS ticket_projects ON ticket_projects.id = tickets.project_id "
        "LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id "
        "LEFT JOIN projects AS parent_projects ON parent_projects.id = sprint_items.project_id "
        "WHERE tickets.state != 'dropped' "
        "AND tickets.id IN (SELECT ticket_id FROM day_tickets WHERE day_id = ?)",
        (day_id,),
    ).fetchall()
    by_state: dict[str, list[tuple[tuple[int, int, str, int], JsonDict]]] = {
        s.value: [] for s in STATE_ORDER
    }
    for row in rows:
        state = str(row["state"])
        priority = str(row["priority"])
        deadline = str(row["deadline"]) if row["deadline"] is not None else None
        fields = fields_codec.fields_from_json(str(row["fields"]))
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
            "has_pending_proposal": machine.has_pending_gating_proposal(TicketState(state), fields),
            "ticket_status": str(row["ticket_status"]),
        }
        sort_key = (
            _prio_rank(priority),
            0 if deadline is not None else 1,
            deadline or "",
            int(row["created_at"]),
        )
        by_state[state].append((sort_key, card))
    columns: list[JsonDict] = []
    for s in STATE_ORDER:
        cards = [card for _, card in sorted(by_state[s.value], key=lambda item: item[0])]
        columns.append({"state": s.value, "cards": cards})
    return {"columns": columns}


# --- queues (§4.5) -------------------------------------------------------------


def _entity_type(entity_id: str) -> str:
    return "ticket" if entity_id.split("_", 1)[0] == "t" else "item"


def _approval_digest(tickets: list[JsonDict], items: list[JsonDict]) -> list[JsonDict]:
    digest: list[JsonDict] = []
    for row in tickets:
        state = str(row["state"])
        if row.get("ticket_status") == TicketStatus.agent_running_step.value:
            continue
        gating = GATING_FIELD.get(TicketState(state))
        if gating is None:
            continue
        fields = row["fields"]
        if not isinstance(fields, dict):
            continue
        slot = fields.get(gating.value)
        if not isinstance(slot, dict):
            continue
        proposal = slot.get("proposal")
        if not isinstance(proposal, dict):
            continue
        digest.append(
            {
                "entity_id": row["id"],
                "kind": gating.value,
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
        state = str(row["state"])
        if deadline is not None and str(deadline) < today_iso and state not in _TICKET_CLOSED:
            result.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "state": state,
                    "priority": row["priority"],
                }
            )
    for row in items:
        deadline = row["deadline"]
        state = str(row["status"])
        if deadline is not None and str(deadline) < today_iso and state not in _ITEM_CLOSED:
            result.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "state": state,
                    "priority": row["priority"],
                }
            )
    return result


def _approvals(conn: sqlite3.Connection, item_approval_rows: list[JsonDict]) -> list[JsonDict]:
    ticket_rows = conn.execute(
        "SELECT id, title, state, ticket_status, fields, updated_at FROM tickets "
        "WHERE state NOT IN ('done','dropped') ORDER BY id"
    ).fetchall()
    ticket_digest: list[dict[str, object]] = []
    ticket_title: dict[str, str] = {}
    for r in ticket_rows:
        tid = str(r["id"])
        ticket_digest.append(
            {
                "id": tid,
                "state": str(r["state"]),
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
        "SELECT id, title, state, priority, deadline FROM tickets WHERE deadline IS NOT NULL "
        "ORDER BY deadline ASC, id"
    ).fetchall()
    ticket_dicts: list[dict[str, object]] = [
        {
            "id": str(r["id"]),
            "title": str(r["title"]),
            "state": str(r["state"]),
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
                "state": entry["state"],
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
) -> JsonDict:
    running_agents = conn.execute(
        "SELECT COUNT(*) AS count FROM tickets WHERE ticket_status = 'agent_running_step'"
    ).fetchone()
    return {
        "approvals": _approvals(conn, item_approval_rows),
        "overdue": _overdue(conn, today_iso, item_overdue_rows),
        "running_agents": int(running_agents["count"] if running_agents is not None else 0),
    }
