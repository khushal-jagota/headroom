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
from planner.core.contracts import JsonDict, Project
from planner.days.logic.carryover import approvals_digest, overdue_list
from planner.dispatch import data as dispatch_data
from planner.dispatch.logic import (
    gating_field_pending,
    has_active_claim,
    is_eligible,
    ordering_key,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import STATE_ORDER, Ticket, TicketState
from planner.tickets.logic import fields_codec

# §7.2 priority band: P0 first. The board reuses the same triple the dispatcher orders by.
_PRIORITY_RANK = ("P0", "P1", "P2", "P3")


def _prio_rank(priority: str) -> int:
    return _PRIORITY_RANK.index(priority)


# --- serializers ---------------------------------------------------------------


def ticket_json(ticket: Ticket, now: int) -> JsonDict:
    """§3.3 ticket. claim_lock is deliberately omitted (never echo the stored token);
    the derived claim_active carries lease liveness instead."""
    return {
        "id": ticket.id,
        "title": ticket.title,
        "state": ticket.state.value,
        "priority": ticket.priority.value,
        "deadline": ticket.deadline,
        "project": ticket.project.value if ticket.project is not None else None,
        "sprint_item_id": ticket.sprint_item_id,
        "sprint_id": ticket.sprint_id,
        "recap": ticket.recap,
        "ceiling": ticket.ceiling.value,
        "at_cap": ticket.at_cap.value,
        "auto_blocked": ticket.auto_blocked,
        "consecutive_failures": ticket.consecutive_failures,
        "chat_session_key": ticket.chat_session_key,
        "alias": ticket.alias,
        "fields": json.loads(fields_codec.fields_to_json(ticket.fields)),
        "claim_expires": ticket.claim_expires,
        "claim_active": has_active_claim(ticket.claim_lock, ticket.claim_expires, now),
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
    }


def run_json(row: sqlite3.Row) -> JsonDict:
    return {
        "id": str(row["id"]),
        "ticket_id": str(row["ticket_id"]),
        "status": str(row["status"]),
        "started_at": int(row["started_at"]),
        "ended_at": int(row["ended_at"]) if row["ended_at"] is not None else None,
        "summary": str(row["summary"]) if row["summary"] is not None else None,
        "error": str(row["error"]) if row["error"] is not None else None,
        "pid": int(row["pid"]) if row["pid"] is not None else None,
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
    project: Project | None,
    sprint_id: str | None,
    sprint_item_id: str | None,
) -> list[JsonDict]:
    clauses: list[str] = []
    params: list[str] = []
    if state is not None:
        clauses.append("state = ?")
        params.append(state.value)
    if project is not None:
        clauses.append("project = ?")
        params.append(project.value)
    if sprint_id is not None:
        clauses.append("sprint_id = ?")
        params.append(sprint_id)
    if sprint_item_id is not None:
        clauses.append("sprint_item_id = ?")
        params.append(sprint_item_id)
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
    total = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()["n"]
    )
    running = conn.execute(
        "SELECT 1 FROM runs WHERE ticket_id = ? AND status = 'running' LIMIT 1", (ticket_id,)
    ).fetchone()
    latest = conn.execute(
        "SELECT * FROM runs WHERE ticket_id = ? ORDER BY started_at DESC, id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    detail.update(
        {
            "blocked": core_links.is_blocked(conn, ticket_id),
            "effective_sprint_id": tickets_data.get_effective_sprint_id(conn, ticket_id),
            "links": [
                {"from_id": str(r["from_id"]), "to_id": str(r["to_id"]), "kind": str(r["kind"])}
                for r in link_rows
            ],
            "day_ids": [str(r["day_id"]) for r in day_rows],
            "run_summary": {
                "total": total,
                "running": running is not None,
                "latest": run_json(latest) if latest is not None else None,
            },
        }
    )
    return detail


def runs_for_ticket(conn: sqlite3.Connection, ticket_id: str) -> list[JsonDict]:
    rows = conn.execute(
        "SELECT * FROM runs WHERE ticket_id = ? ORDER BY started_at DESC, id DESC", (ticket_id,)
    ).fetchall()
    return [run_json(r) for r in rows]


def get_run(conn: sqlite3.Connection, run_id: str) -> JsonDict | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return run_json(row) if row is not None else None


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
        f"\n"
        f"success:\n{show(fields.success.value)}\n"
        f"\n"
        f"approach:\n{show(fields.approach.value)}\n"
        f"\n"
        f"plan:\n{show(fields.plan.value)}\n"
        f"\n"
        f"result:\n{show(fields.result.value)}\n"
        f"\n"
        f"recap:\n{show(ticket.recap)}\n"
        f"\n"
        f"links:\n{links_block}\n"
    )


# --- board (§10.3) -------------------------------------------------------------


def board_view(conn: sqlite3.Connection, now: int) -> JsonDict:
    rows = conn.execute(
        "SELECT id, title, state, priority, deadline, project, fields, claim_lock, "
        "claim_expires, created_at FROM tickets WHERE state != 'dropped'"
    ).fetchall()
    by_state: dict[str, list[tuple[tuple[int, int, str, int], JsonDict]]] = {
        s.value: [] for s in STATE_ORDER
    }
    for row in rows:
        state = str(row["state"])
        priority = str(row["priority"])
        deadline = str(row["deadline"]) if row["deadline"] is not None else None
        fields = json.loads(str(row["fields"]))
        claim_lock = str(row["claim_lock"]) if row["claim_lock"] is not None else None
        claim_expires = int(row["claim_expires"]) if row["claim_expires"] is not None else None
        card: JsonDict = {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "priority": priority,
            "deadline": deadline,
            "project": str(row["project"]) if row["project"] is not None else None,
            "has_pending_proposal": gating_field_pending(TicketState(state), fields),
            "has_running_claim": has_active_claim(claim_lock, claim_expires, now),
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


def _approvals(conn: sqlite3.Connection, item_approval_rows: list[JsonDict]) -> list[JsonDict]:
    ticket_rows = conn.execute(
        "SELECT id, title, state, fields, updated_at FROM tickets "
        "WHERE state NOT IN ('done','dropped') ORDER BY id"
    ).fetchall()
    ticket_digest: list[dict[str, object]] = []
    ticket_title: dict[str, str] = {}
    ticket_updated: dict[str, int] = {}
    for r in ticket_rows:
        tid = str(r["id"])
        ticket_digest.append(
            {
                "id": tid,
                "state": str(r["state"]),
                "fields": json.loads(str(r["fields"])),
                "updated_at": int(r["updated_at"]),
            }
        )
        ticket_title[tid] = str(r["title"])
        ticket_updated[tid] = int(r["updated_at"])
    item_digest: list[dict[str, object]] = []
    item_title: dict[str, str] = {}
    for r in item_approval_rows:
        iid = str(r["id"])
        item_digest.append({"id": iid, "status_proposal": r["status_proposal"]})
        item_title[iid] = str(r["title"])
    digest = approvals_digest(ticket_digest, item_digest)
    # A5: review entries use the last state_changed->needs_review event time, not the
    # updated_at proxy; the boundary digest keeps its documented proxy.
    for entry in digest:
        if entry["kind"] == "review":
            tid = str(entry["entity_id"])
            row = conn.execute(
                "SELECT created_at FROM events WHERE entity_id = ? AND kind = 'state_changed' "
                "AND json_extract(payload, '$.to') = 'needs_review' ORDER BY id DESC LIMIT 1",
                (tid,),
            ).fetchone()
            entry["waiting_since"] = (
                int(row["created_at"]) if row is not None
                else ticket_updated.get(tid, entry["waiting_since"])
            )
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


def _pickup(conn: sqlite3.Connection, now: int) -> list[JsonDict]:
    candidates = dispatch_data.load_candidates(conn, now)
    eligible = [c for c in candidates if is_eligible(c)]
    eligible.sort(key=ordering_key)
    if not eligible:
        return []
    ids = [c.ticket_id for c in eligible]
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, title FROM tickets WHERE id IN ({placeholders})", tuple(ids)
    ).fetchall()
    title_by_id = {str(r["id"]): str(r["title"]) for r in rows}
    return [
        {
            "ticket_id": c.ticket_id,
            "title": title_by_id.get(c.ticket_id, ""),
            "state": c.state.value,
            "priority": c.priority.value,
            "deadline": c.deadline,
        }
        for c in eligible
    ]


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
    digest = overdue_list(ticket_dicts, item_dicts, today_iso)
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
    return {
        "approvals": _approvals(conn, item_approval_rows),
        "pickup": _pickup(conn, now),
        "overdue": _overdue(conn, today_iso, item_overdue_rows),
    }
