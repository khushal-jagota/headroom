"""Coherent manager, child Ticket, and Idea reads."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from planner.core import ticket_blocks
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.list_reads.contracts import ListPage, ListPageRequest
from planner.managers import data as managers_data
from planner.managers.contracts import (
    Manager,
    ManagerSummary,
    ManagerWorkspaceDocument,
)
from planner.runtime import conversation_start
from planner.tickets import derivation
from planner.tickets.logic.recap import recap_preview
from planner.worker_types.configuration import configured_worker_type_registry

# --- serializers ---------------------------------------------------------------


def manager_json(item: Manager) -> JsonDict:
    return {
        "id": item.id,
        "title": item.title,
        "settled": item.settled,
        "goal": item.goal,
        "recap": item.recap,
        "project_id": item.project_id,
        "project": item.project_name,
        "manager": {
            "agent_key": item.manager_agent_key,
            "launch_configuration": {
                "employee_backend": item.manager_launch_configuration.employee_backend.value,
                "employee_launch_model": item.manager_launch_configuration.employee_launch_model,
                "employee_launch_reasoning_effort": (
                    item.manager_launch_configuration.employee_launch_reasoning_effort
                ),
            },
        },
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


# --- manager reads ------------------------------------------------------


def manager_tickets(conn: sqlite3.Connection, item_id: str) -> list[JsonDict]:
    """Per-item ticket rows for the tracking-page disclosure: id/title/stage
    plus the board-card signals the sprint ticket row colours off —
    has_pending_proposal, ticket_status, waiting_to_closeout, gating_field, and
    blocked — ordered created_at, id (matching the loose-ticket ordering). A light
    projection — not full ticket_json — since the disclosure only lists rows that link
    to the ticket.

    gating_field and blocked are the two facts the shared status-group rule needs and
    the condition alone cannot see. Without them the Manager page and the workspace
    rail would sort the same Ticket into different groups."""
    rows = conn.execute(
        "SELECT t.*,"
        "CASE WHEN t.manager_id IS NOT NULL THEN parent.project_id "
        "ELSE t.project_id END AS effective_project_id FROM tickets t "
        "LEFT JOIN managers parent ON parent.id=t.manager_id WHERE "
        + "t.manager_id = ?"
        + " ORDER BY t.created_at, t.id",
        (item_id,),
    ).fetchall()
    result: list[JsonDict] = []
    registry = configured_worker_type_registry()
    blocked_ticket_ids = ticket_blocks.blocked_ticket_ids(conn)
    for r in rows:
        stage = str(r["stage"])
        facts = derivation.derive_ticket_facts(
            derivation.stored_facts_from_row(r, has_live_blocker=str(r["id"]) in blocked_ticket_ids)
        )
        worker_type_definition = registry.require(str(r["worker_type"]))
        gating_field = worker_type_definition.gating_field(stage)
        result.append(
            {
                "id": str(r["id"]),
                "title": str(r["title"]),
                "recap": str(r["recap"]),
                "recap_preview": recap_preview(str(r["recap"])),
                "project_id": r["effective_project_id"],
                "manager_id": r["manager_id"],
                "stage": stage,
                "is_backlogged": bool(r["is_backlogged"]),
                "activity_at": int(r["updated_at"]),
                "conversation_id": r["conversation_id"],
                "has_pending_proposal": r["pending_proposal"] is not None,
                # Who a parked proposal is addressed to. The shared attention projection
                # answers `awaiting_approval` for Khushal and `awaiting_agent_approval`
                # for everyone else, so this is what tells a reader whether the one it is
                # looking at is its own.
                "ceiling_holder": json.loads(str(r["ceiling_holder"])),
                "ticket_status": facts.ticket_status.value,
                "waiting_to_closeout": facts.waiting_to_closeout,
                "gating_field": gating_field,
                "blocked": facts.blocked,
                "employee_backend": str(r["employee_backend"]),
                "worker_type": str(r["worker_type"]),
            }
        )
    return result


def _item_row_key(row: sqlite3.Row) -> tuple[int, str]:
    return (int(row["created_at"]), str(row["id"]))


def list_managers(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[JsonDict]:
    rows = conn.execute(
        "SELECT id, created_at FROM managers"
        + (" WHERE project_id=?" if project_id is not None else ""),
        (project_id,) if project_id is not None else (),
    ).fetchall()
    return [manager_detail(conn, str(row["id"])) for row in sorted(rows, key=_item_row_key)]


def manager_detail(conn: sqlite3.Connection, item_id: str) -> JsonDict:
    read = managers_data.read_manager(conn, item_id)
    result = {
        **manager_json(read.item),
    }
    result["manager"]["conversation_id"] = conversation_start.read_agent_conversation(
        conn, read.item.manager_agent_key
    )
    return result


def manager_workspace(conn: sqlite3.Connection, item_id: str) -> JsonDict:
    """The smallest coherent read for the Manager workspace.

    Ticket writes remain on their canonical routes. This read only assembles the child
    Ticket state and preserves the Manager conversation history.
    """
    with _read_snapshot(conn):
        result = manager_detail(conn, item_id)
        tickets = manager_tickets(conn, item_id)
        result.update(
            {
                "tickets": tickets,
                "conversation_history": (
                    conversation_start.read_manager_conversation_history(conn, item_id)
                ),
            }
        )
        return result


def manager_workspace_document(conn: sqlite3.Connection, item_id: str) -> ManagerWorkspaceDocument:
    """Read only the selected document, without child, catalog, or attention reads."""
    with _read_snapshot(conn):
        row = conn.execute("SELECT goal, recap FROM managers WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise PlannerError(ErrorCode.not_found, "manager not found", {"id": item_id})
        return {
            "goal": str(row[0]),
            "recap": str(row[1]),
        }


def manager_summary(item: Manager) -> ManagerSummary:
    return ManagerSummary(
        id=item.id,
        title=item.title,
        settled=item.settled,
        project_id=item.project_id,
        project=item.project_name,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def list_manager_summaries(
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
    count = int(conn.execute("SELECT count(*) FROM managers" + where, params).fetchone()[0])
    rows = conn.execute(
        "SELECT id FROM managers" + where + " ORDER BY created_at, id LIMIT ? OFFSET ?",
        (*params, page_request.limit, page_request.offset),
    ).fetchall()
    return ListPage(
        rows=tuple(
            dict(manager_summary(managers_data.read_manager(conn, str(row["id"])).item))
            for row in rows
        ),
        match_count=count,
        limit=page_request.limit,
        offset=page_request.offset,
    )


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
