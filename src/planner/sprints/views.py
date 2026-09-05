"""Sprint / sprint-item / idea read-view assembly: per-entity serializers, item
rollups, and the sprint-current view (§5/§6.1). Pure read assembly — no FastAPI or
writes."""

from __future__ import annotations

import sqlite3

from planner.core import links as core_links
from planner.core.contracts import JsonDict
from planner.list_reads.contracts import ListPage, ListPageRequest
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints.contracts import ItemStatus, Sprint, SprintItem
from planner.sprints.logic import DateRange, current_sprint_id
from planner.tickets.contracts import AtCap, TicketStatus
from planner.tickets.logic import machine
from planner.worker_types.configuration import configured_worker_type_registry

_PRIORITY_RANK = ("P0", "P1", "P2", "P3")


def _prio_rank(priority: str) -> int:
    return _PRIORITY_RANK.index(priority)


# --- serializers ---------------------------------------------------------------


def item_json(
    item: SprintItem,
    *,
    status: ItemStatus,
    blocking_ticket_ids: list[str] | None = None,
) -> JsonDict:
    return {
        "id": item.id,
        "title": item.title,
        "body": item.body,
        "status": status.value,
        "priority": item.priority.value,
        "deadline": item.deadline,
        "project_id": item.project_id,
        "project": item.project_name,
        "sprint_id": item.sprint_id,
        "kind": item.kind.value,
        "supervisor": {
            "agent_key": item.supervisor_agent_key,
            "launch_configuration": {
                "employee_backend": item.supervisor_launch_configuration.employee_backend.value,
                "employee_launch_model": item.supervisor_launch_configuration.employee_launch_model,
                "employee_launch_reasoning_effort": (
                    item.supervisor_launch_configuration.employee_launch_reasoning_effort
                ),
            },
        },
        "blocked_by": list(blocking_ticket_ids or []),
        "status_proposal": None,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def sprint_json(sprint: Sprint) -> JsonDict:
    return {
        "id": sprint.id,
        "name": sprint.name,
        "date_start": sprint.date_start,
        "date_end": sprint.date_end,
        "primary_bet": sprint.primary_bet,
        "kickoff": sprint.kickoff,
        "checkpoint": sprint.checkpoint,
        "review": sprint.review,
        "created_at": sprint.created_at,
        "updated_at": sprint.updated_at,
    }


def idea_json(row: sqlite3.Row) -> JsonDict:
    return {
        "id": str(row["id"]),
        "title": str(row["title"]),
        "body": str(row["body"]),
        "project_id": str(row["project_id"]) if row["project_id"] is not None else None,
        "project": (str(row["project_name"]) if row["project_name"] is not None else None),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


# --- rollups + item reads ------------------------------------------------------


def item_rollup(conn: sqlite3.Connection, item_id: str) -> dict[str, int]:
    coding_worker_type_definition = configured_worker_type_registry().require("coding")
    rollup: dict[str, int] = {
        stage: 0
        for stage in (
            *coding_worker_type_definition.stage_ids(),
            coding_worker_type_definition.dropped_stage.id,
        )
    }
    rows = conn.execute(
        "SELECT stage, COUNT(*) AS n FROM tickets WHERE sprint_item_id = ? GROUP BY stage",
        (item_id,),
    ).fetchall()
    for r in rows:
        rollup[str(r["stage"])] = int(r["n"])
    return rollup


def item_tickets(conn: sqlite3.Connection, item_id: str) -> list[JsonDict]:
    """Per-item ticket rows for the tracking-page disclosure: id/title/stage/priority
    plus the board-card signals the sprint ticket row colours off —
    has_pending_proposal, ticket_status, waiting_to_closeout, gating_field, and
    blocked — ordered created_at, id (matching the loose-ticket ordering). A light
    projection — not full ticket_json — since the disclosure only lists rows that link
    to the ticket.

    gating_field and blocked are the two facts the shared status-group rule needs and
    the condition alone cannot see. Without them the Sprint Item page and the workspace
    rail would sort the same Ticket into different groups."""
    rows = conn.execute(
        "SELECT id, title, stage, priority, ticket_status, pending_proposal, worker_type, at_cap, "
        "ceiling, employee_backend FROM tickets "
        "WHERE sprint_item_id = ? ORDER BY created_at, id",
        (item_id,),
    ).fetchall()
    result: list[JsonDict] = []
    registry = configured_worker_type_registry()
    blocked_target_ids = core_links.blocked_target_ids(conn)
    for r in rows:
        stage = str(r["stage"])
        ticket_status = str(r["ticket_status"])
        worker_type_definition = registry.require(str(r["worker_type"]))
        gating_field = worker_type_definition.gating_field(stage)
        stopped_at_current_stage = str(
            r["at_cap"]
        ) == AtCap.stop.value and machine.at_or_beyond_ceiling(
            stage,
            str(r["ceiling"]),
            worker_type_definition=worker_type_definition,
        )
        waiting_to_closeout = (
            gating_field == "closeout"
            and ticket_status == TicketStatus.empty.value
            and not stopped_at_current_stage
        )
        result.append(
            {
                "id": str(r["id"]),
                "title": str(r["title"]),
                "stage": stage,
                "priority": str(r["priority"]),
                "has_pending_proposal": r["pending_proposal"] is not None,
                "ticket_status": ticket_status,
                "waiting_to_closeout": waiting_to_closeout,
                "gating_field": gating_field,
                "blocked": str(r["id"]) in blocked_target_ids,
                "review_route": str(r["at_cap"]),
                "employee_backend": str(r["employee_backend"]),
                "worker_type": str(r["worker_type"]),
                "day_ids": [
                    str(day_row["day_id"])
                    for day_row in conn.execute(
                        "SELECT day_id FROM day_tickets WHERE ticket_id = ? ORDER BY day_id",
                        (str(r["id"]),),
                    ).fetchall()
                ],
            }
        )
    return result


def item_ticket_overview(conn: sqlite3.Connection, item_id: str) -> list[JsonDict]:
    """One line per Ticket on the Item, for the Sprint Item supervisor's own-Item read:
    id, title, stage, ticket_status, and Day membership. Finished Tickets included,
    ordered created_at, id.

    This is not item_tickets. That projection carries the board-card signals the Sprint
    Item page colours its rows off; the supervisor reads its answer in full and drills
    into one Ticket at a time through ticket-context, so anything more per Ticket is
    weight it pays for and does not use."""
    rows = conn.execute(
        "SELECT id, title, stage, ticket_status FROM tickets "
        "WHERE sprint_item_id = ? ORDER BY created_at, id",
        (item_id,),
    ).fetchall()
    day_ids_by_ticket: dict[str, list[str]] = {}
    for day_row in conn.execute(
        "SELECT day_tickets.ticket_id AS ticket_id, day_tickets.day_id AS day_id "
        "FROM day_tickets JOIN tickets ON tickets.id = day_tickets.ticket_id "
        "WHERE tickets.sprint_item_id = ? ORDER BY day_tickets.day_id",
        (item_id,),
    ).fetchall():
        day_ids_by_ticket.setdefault(str(day_row["ticket_id"]), []).append(str(day_row["day_id"]))
    return [
        {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "stage": str(row["stage"]),
            "ticket_status": str(row["ticket_status"]),
            "day_ids": day_ids_by_ticket.get(str(row["id"]), []),
        }
        for row in rows
    ]


def unclassified_sprint_tickets(conn: sqlite3.Connection, sprint_id: str) -> list[JsonDict]:
    rows = conn.execute(
        "SELECT id, title, stage, priority, ticket_status, pending_proposal, worker_type, "
        "employee_backend FROM tickets WHERE sprint_id = ? AND sprint_item_id IS NULL "
        "ORDER BY created_at, id",
        (sprint_id,),
    ).fetchall()
    result: list[JsonDict] = []
    for row in rows:
        stage = str(row["stage"])
        result.append(
            {
                "id": str(row["id"]),
                "title": str(row["title"]),
                "stage": stage,
                "priority": str(row["priority"]),
                "has_pending_proposal": row["pending_proposal"] is not None,
                "ticket_status": str(row["ticket_status"]),
                "employee_backend": str(row["employee_backend"]),
            }
        )
    return result


def blocked_by_titles(conn: sqlite3.Connection, blocked_by: list[str]) -> list[str]:
    """Resolve active/read blocker ids to titles, preserving order."""
    titles: list[str] = []
    for ticket_id in blocked_by:
        row = conn.execute("SELECT title FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is not None:
            titles.append(str(row["title"]))
    return titles


def _item_row_key(row: sqlite3.Row) -> tuple[int, int, str]:
    return (_prio_rank(str(row["priority"])), int(row["created_at"]), str(row["id"]))


def list_items(
    conn: sqlite3.Connection,
    *,
    status: ItemStatus | None,
    project_id: str | None,
    sprint_id_filter: str | None,
) -> list[JsonDict]:
    clauses: list[str] = []
    params: list[str] = []
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    if sprint_id_filter is not None:
        if sprint_id_filter == "null":
            clauses.append("sprint_id IS NULL")
        else:
            clauses.append("sprint_id = ?")
            params.append(sprint_id_filter)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT id, priority, created_at FROM sprint_items" + where, tuple(params)
    ).fetchall()
    result: list[JsonDict] = []
    for row in sorted(rows, key=_item_row_key):
        read = sprints_data.read_item(conn, str(row["id"]))
        if status is not None and read.status is not status:
            continue
        result.append(
            {
                **item_json(
                    read.item,
                    status=read.status,
                    blocking_ticket_ids=read.blocking_ticket_ids,
                ),
                "blockers_cleared": read.blockers_cleared,
            }
        )
    return result


def item_detail(conn: sqlite3.Connection, item_id: str) -> JsonDict:
    read = sprints_data.read_item(conn, item_id)
    result = {
        **item_json(
            read.item,
            status=read.status,
            blocking_ticket_ids=read.blocking_ticket_ids,
        ),
        "blockers_cleared": read.blockers_cleared,
        "rollup": item_rollup(conn, item_id),
    }
    result["supervisor"]["conversation_id"] = conversation_start.read_agent_conversation(
        conn, read.item.supervisor_agent_key
    )
    return result


def item_workspace(conn: sqlite3.Connection, item_id: str, planning_day_id: str) -> JsonDict:
    """The smallest coherent read for the Sprint Item workspace.

    Ticket writes remain on their canonical routes. This read only assembles the child
    state and identifies which rows belong to the current planning day.
    """
    result = item_detail(conn, item_id)
    tickets = item_tickets(conn, item_id)
    result.update(
        {
            "planning_day_id": planning_day_id,
            "tickets": tickets,
            "today_ticket_ids": [
                str(ticket["id"]) for ticket in tickets if planning_day_id in ticket["day_ids"]
            ],
            "conversation_history": (
                conversation_start.read_sprint_item_supervisor_conversation_history(conn, item_id)
            ),
        }
    )
    return result


def list_sprints(conn: sqlite3.Connection) -> list[JsonDict]:
    rows = conn.execute("SELECT id FROM sprints ORDER BY date_start DESC, id").fetchall()
    return [sprint_json(sprints_data.read_sprint(conn, str(r["id"]))) for r in rows]


def list_sprint_summaries(
    conn: sqlite3.Connection, *, page_request: ListPageRequest
) -> ListPage[JsonDict]:
    rows = conn.execute(
        "SELECT id, name, date_start, date_end FROM sprints ORDER BY date_start DESC, id"
    ).fetchall()
    summaries = [
        {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "date_start": str(row["date_start"]),
            "date_end": str(row["date_end"]),
        }
        for row in rows[page_request.offset : page_request.offset + page_request.limit]
    ]
    return ListPage(
        rows=tuple(summaries),
        match_count=len(rows),
        limit=page_request.limit,
        offset=page_request.offset,
    )


def list_item_summaries(
    conn: sqlite3.Connection,
    *,
    page_request: ListPageRequest,
    status: ItemStatus | None,
    project_id: str | None,
    sprint_id_filter: str | None,
) -> ListPage[JsonDict]:
    clauses: list[str] = []
    params: list[str] = []
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    if sprint_id_filter is not None:
        if sprint_id_filter == "null":
            clauses.append("sprint_id IS NULL")
        else:
            clauses.append("sprint_id = ?")
            params.append(sprint_id_filter)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT id, priority, created_at FROM sprint_items" + where,
        tuple(params),
    ).fetchall()
    matches: list[sprints_data.ItemRead] = []
    for row in sorted(rows, key=_item_row_key):
        read = sprints_data.read_item(conn, str(row["id"]))
        if status is not None and read.status is not status:
            continue
        matches.append(read)
    selected = matches[page_request.offset : page_request.offset + page_request.limit]
    return ListPage(
        rows=tuple(
            {
                "id": read.item.id,
                "title": read.item.title,
                "status": read.status.value,
                "priority": read.item.priority.value,
                "deadline": read.item.deadline,
                "project_id": read.item.project_id,
                "project": read.item.project_name,
                "sprint_id": read.item.sprint_id,
            }
            for read in selected
        ),
        match_count=len(matches),
        limit=page_request.limit,
        offset=page_request.offset,
    )


def list_ideas(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[JsonDict]:
    where = "WHERE ideas.project_id = ?" if project_id is not None else ""
    params = (project_id,) if project_id is not None else ()
    sql = (
        "SELECT ideas.*, projects.name AS project_name "
        "FROM ideas LEFT JOIN projects ON projects.id = ideas.project_id "
        f"{where} "
        "ORDER BY ideas.created_at DESC, ideas.id"
    )
    rows = conn.execute(sql, params).fetchall()
    return [idea_json(r) for r in rows]


# --- sprint-current view (§5) --------------------------------------------------


def sprint_current_view(conn: sqlite3.Connection, planning_date_iso: str) -> JsonDict:
    ranges = [
        DateRange(
            id=str(r["id"]),
            date_start=str(r["date_start"]),
            date_end=str(r["date_end"]),
        )
        for r in conn.execute("SELECT id, date_start, date_end FROM sprints").fetchall()
    ]
    sid = current_sprint_id(planning_date_iso, ranges)
    if sid is None:
        return {
            "planning_date": planning_date_iso,
            "sprint": None,
            "groups": {s.value: [] for s in ItemStatus},
            "other_tickets": [],
        }
    item_rows = conn.execute(
        "SELECT id, priority, created_at FROM sprint_items WHERE sprint_id = ?", (sid,)
    ).fetchall()
    groups: dict[str, list[JsonDict]] = {s.value: [] for s in ItemStatus}
    for row in sorted(item_rows, key=_item_row_key):
        read = sprints_data.read_item(conn, str(row["id"]))
        groups[read.status.value].append(
            {
                **item_json(
                    read.item,
                    status=read.status,
                    blocking_ticket_ids=read.blocking_ticket_ids,
                ),
                "blockers_cleared": read.blockers_cleared,
                "rollup": item_rollup(conn, str(row["id"])),
                "tickets": item_tickets(conn, str(row["id"])),
                "blocked_by_titles": blocked_by_titles(conn, read.blocking_ticket_ids),
            }
        )
    return {
        "planning_date": planning_date_iso,
        "sprint": sprint_json(sprints_data.read_sprint(conn, sid)),
        "groups": groups,
        "other_tickets": unclassified_sprint_tickets(conn, sid),
    }
