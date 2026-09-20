"""Sprint, Outcome and idea reads, including coherent Sprint tracking snapshots."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from planner.core import ticket_blocks
from planner.core.contracts import JsonDict
from planner.list_reads.contracts import ListPage, ListPageRequest
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints.contracts import (
    OutcomeSummary,
    Sprint,
    SprintItem,
    SprintTicketSummary,
    SprintTrackingBody,
    SprintWireBody,
)
from planner.sprints.logic import DateRange, current_sprint_id
from planner.tickets import derivation
from planner.worker_types.configuration import configured_worker_type_registry

_PRIORITY_RANK = ("P0", "P1", "P2", "P3")


def _prio_rank(priority: str) -> int:
    return _PRIORITY_RANK.index(priority)


# --- serializers ---------------------------------------------------------------


def item_json(item: SprintItem) -> JsonDict:
    return {
        "id": item.id,
        "title": item.title,
        "body": item.body,
        "priority": item.priority.value,
        "deadline": item.deadline,
        "project_id": item.project_id,
        "project": item.project_name,
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
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def sprint_json(sprint: Sprint) -> SprintWireBody:
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


# --- outcome reads ------------------------------------------------------


def item_tickets(
    conn: sqlite3.Connection, item_id: str | None, *, sprint_id: str | None = None
) -> list[JsonDict]:
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
        "SELECT t.*, s.name AS sprint_name FROM tickets t "
        "LEFT JOIN sprints s ON s.id=t.sprint_id WHERE "
        + ("t.sprint_item_id = ?" if item_id is not None else "t.sprint_id = ?")
        + " ORDER BY t.created_at, t.id",
        (item_id if item_id is not None else sprint_id,),
    ).fetchall()
    result: list[JsonDict] = []
    registry = configured_worker_type_registry()
    blocked_ticket_ids = ticket_blocks.blocked_ticket_ids(conn)
    for r in rows:
        stage = str(r["stage"])
        facts = derivation.derive_ticket_facts(
            derivation.stored_facts_from_row(
                r, has_live_blocker=str(r["id"]) in blocked_ticket_ids
            )
        )
        worker_type_definition = registry.require(str(r["worker_type"]))
        gating_field = worker_type_definition.gating_field(stage)
        result.append(
            {
                "id": str(r["id"]),
                "title": str(r["title"]),
                "sprint_id": r["sprint_id"],
                "sprint_name": r["sprint_name"],
                "project_id": r["project_id"],
                "sprint_item_id": r["sprint_item_id"],
                "stage": stage,
                "priority": str(r["priority"]),
                "has_pending_proposal": r["pending_proposal"] is not None,
                "ticket_status": facts.ticket_status.value,
                "waiting_to_closeout": facts.waiting_to_closeout,
                "gating_field": gating_field,
                "blocked": facts.blocked,
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
    id, title, stage, ticket_status, ceiling holder, and Day membership. Finished Tickets
    included, ordered created_at, id.

    This is not item_tickets. That projection carries the board-card signals the Sprint
    Item page colours its rows off; the supervisor reads its answer in full and drills
    into one Ticket at a time through ticket-context, so anything more per Ticket is
    weight it pays for and does not use."""
    rows = conn.execute(
        "SELECT id, title, stage, worker_type, worker_step_claim, pending_proposal, "
        "ceiling_holder FROM tickets WHERE sprint_item_id = ? ORDER BY created_at, id",
        (item_id,),
    ).fetchall()
    facts_by_ticket = derivation.load_ticket_facts(conn, {str(row["id"]) for row in rows})
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
            "ticket_status": facts_by_ticket[str(row["id"])].ticket_status.value,
            # Who holds the ceiling, so the supervisor can tell a proposal parked on
            # itself from one parked on another agent. The shared attention projection
            # answers only for the owner, on this route as on every other.
            "ceiling_holder": json.loads(str(row["ceiling_holder"])),
            "day_ids": day_ids_by_ticket.get(str(row["id"]), []),
        }
        for row in rows
    ]


def _item_row_key(row: sqlite3.Row) -> tuple[int, int, str]:
    return (_prio_rank(str(row["priority"])), int(row["created_at"]), str(row["id"]))


def list_items(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[JsonDict]:
    rows = conn.execute(
        "SELECT id, priority, created_at FROM sprint_items"
        + (" WHERE project_id=?" if project_id is not None else ""),
        (project_id,) if project_id is not None else (),
    ).fetchall()
    return [item_detail(conn, str(row["id"])) for row in sorted(rows, key=_item_row_key)]


def item_detail(conn: sqlite3.Connection, item_id: str) -> JsonDict:
    read = sprints_data.read_item(conn, item_id)
    result = {
        **item_json(read.item),
        "committed_sprints": [
            dict(row)
            for row in conn.execute(
                "SELECT s.id, s.name, s.date_start, s.date_end FROM sprints s "
                "JOIN sprint_outcomes c ON c.sprint_id=s.id WHERE c.outcome_id=? "
                "ORDER BY s.date_start, s.id",
                (item_id,),
            )
        ],
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
    with _read_snapshot(conn):
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
                    conversation_start.read_sprint_item_supervisor_conversation_history(
                        conn, item_id
                    )
                ),
            }
        )
        return result


def list_sprints(conn: sqlite3.Connection) -> list[JsonDict]:
    rows = conn.execute("SELECT id FROM sprints ORDER BY date_start DESC, id").fetchall()
    return [dict(sprint_json(sprints_data.read_sprint(conn, str(r["id"])))) for r in rows]


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


def outcome_summary(item: SprintItem) -> OutcomeSummary:
    return OutcomeSummary(
        id=item.id,
        title=item.title,
        priority=item.priority.value,
        deadline=item.deadline,
        project_id=item.project_id,
        project=item.project_name,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def list_item_summaries(
    conn: sqlite3.Connection,
    *,
    page_request: ListPageRequest,
    project_id: str | None = None,
    search: str | None = None,
) -> ListPage[JsonDict]:
    clauses: list[str] = []
    params: list[str] = []
    if project_id is not None:
        clauses.append("project_id=?")
        params.append(project_id)
    if search:
        clauses.append("instr(lower(title), lower(?)) > 0")
        params.append(search)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    count = int(conn.execute("SELECT count(*) FROM sprint_items" + where, params).fetchone()[0])
    rows = conn.execute(
        "SELECT id FROM sprint_items"
        + where
        + " ORDER BY priority, created_at, id LIMIT ? OFFSET ?",
        (*params, page_request.limit, page_request.offset),
    ).fetchall()
    return ListPage(
        rows=tuple(
            dict(outcome_summary(sprints_data.read_item(conn, str(row["id"])).item)) for row in rows
        ),
        match_count=count,
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


@contextmanager
def _read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def sprint_tracking_view(
    conn: sqlite3.Connection,
    sprint_id: str | None,
    planning_date_iso: str,
) -> SprintTrackingBody:
    if sprint_id is None:
        return SprintTrackingBody(
            planning_date=planning_date_iso, sprint=None, outcome_groups=[], unclassified_tickets=[]
        )
    with _read_snapshot(conn):
        sprint = sprints_data.read_sprint(conn, sprint_id)
        committed = {
            str(row[0])
            for row in conn.execute(
                "SELECT outcome_id FROM sprint_outcomes WHERE sprint_id=?", (sprint_id,)
            )
        }
        tickets = item_tickets(conn, None, sprint_id=sprint_id)
        outcome_ids = committed | {str(t["sprint_item_id"]) for t in tickets if t["sprint_item_id"]}
        outcomes = [sprints_data.read_item(conn, oid).item for oid in outcome_ids]
        outcomes.sort(key=lambda item: (_prio_rank(item.priority.value), item.created_at, item.id))
        summaries = [
            SprintTicketSummary(
                id=str(t["id"]),
                title=str(t["title"]),
                stage=str(t["stage"]),
                priority=str(t["priority"]),
                ticket_status=str(t["ticket_status"]),
                project_id=t["project_id"],
                sprint_item_id=t["sprint_item_id"],
                waiting_to_closeout=bool(t["waiting_to_closeout"]),
            )
            for t in tickets
        ]
        return SprintTrackingBody(
            planning_date=planning_date_iso,
            sprint=sprint_json(sprint),
            outcome_groups=[
                {
                    "outcome": outcome_summary(item),
                    "committed": item.id in committed,
                    "tickets": [t for t in summaries if t["sprint_item_id"] == item.id],
                }
                for item in outcomes
            ],
            unclassified_tickets=[t for t in summaries if t["sprint_item_id"] is None],
        )


def sprint_current_view(conn: sqlite3.Connection, planning_date_iso: str) -> SprintTrackingBody:
    with _read_snapshot(conn):
        ranges = [
            DateRange(id=str(r["id"]), date_start=str(r["date_start"]), date_end=str(r["date_end"]))
            for r in conn.execute("SELECT id,date_start,date_end FROM sprints")
        ]
        return sprint_tracking_view(
            conn, current_sprint_id(planning_date_iso, ranges), planning_date_iso
        )
