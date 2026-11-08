"""Ticket read-view assembly: per-entity serializers, Ticket detail,
Ticket-only Review, and copy text. Pure read assembly — no FastAPI, pydantic, writes,
or imports from an API module."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import cast

from planner.core import ticket_blocks
from planner.core.contracts import BlockerSummary, JsonDict, PrincipalKind
from planner.files.lifecycle import database_path
from planner.files.logic.listing import entries_as_json, fold_directory
from planner.files.logic.paths import ticket_files_root
from planner.list_reads.contracts import ListPage, ListPageRequest
from planner.navigation import data as navigation_data
from planner.navigation.review import review_message_items
from planner.navigation.review_contracts import ReviewMessageItem, ReviewProposalItem
from planner.navigation.review_logic import group_review_items
from planner.tickets import data as tickets_data
from planner.tickets import derivation
from planner.tickets.contracts import (
    Ticket,
    TicketListFilters,
    TicketStatus,
)
from planner.tickets.integrity import (
    diagnostic_projection,
    issue_for_ticket,
)
from planner.tickets.logic import fields_codec, machine, proposal_routing
from planner.tickets.logic.creation_receipt import supervision_receipt
from planner.tickets.logic.review import eligible_for_owner_review
from planner.worker_types.contracts import CONSEQUENCES_FIELD_ID
from planner.worker_types.registry import workflow_manifest
from planner.worker_types.store import workflow_from_row


def _broken(conn: sqlite3.Connection, ticket_id: str) -> JsonDict | None:
    issue = issue_for_ticket(conn, ticket_id)
    return cast(JsonDict, diagnostic_projection(conn, ticket_id, issue)) if issue else None


# --- serializers ---------------------------------------------------------------


def blocker_summary_json(summary: BlockerSummary) -> JsonDict:
    return {
        "blocked_by": [
            {
                "ticket_id": row.ticket_id,
                "title": row.title,
                "stage": row.stage,
                "href": row.href,
            }
            for row in summary.blocked_by
            if row.active
        ],
    }


def ticket_json(ticket: Ticket, now: int) -> JsonDict:
    """§3.3 ticket serializer."""
    return {
        "is_backlogged": ticket.is_backlogged,
        "id": ticket.id,
        "title": ticket.title,
        "worker_type": ticket.worker_type,
        "workflow": workflow_manifest(
            ticket.workflow,
            worker_type=ticket.worker_type,
            specialist_skill=ticket.workflow.specialist_skill,
        ),
        "locale": ticket.locale.value,
        "employee_backend": ticket.employee_backend,
        "employee_launch_model": ticket.employee_launch_model,
        "employee_launch_reasoning_effort": ticket.employee_launch_reasoning_effort,
        "stage": str(ticket.stage),
        "deadline": ticket.deadline,
        "project_id": ticket.project_id,
        "project": ticket.project_name,
        "manager_id": ticket.manager_id,
        "manager": {
            "kind": ticket.manager_kind,
            "id": ticket.manager_id if ticket.manager_kind == "manager" else "chief",
        },
        "manager_title": ticket.manager_title,
        "recap": ticket.recap,
        "ceiling": str(ticket.ceiling),
        "ceiling_holder": {
            "kind": ticket.ceiling_holder.kind.value,
            "id": ticket.ceiling_holder.id,
        },
        "ticket_status": ticket.ticket_status.value,
        "conversation_id": ticket.conversation_id,
        "field_values": dict(ticket.field_values),
        "field_approvals": {key: asdict(value) for key, value in ticket.field_approvals.items()},
        "pending_proposal": (
            {
                **asdict(ticket.pending_proposal),
                "revision": ticket.pending_proposal_revision,
            }
            if ticket.pending_proposal is not None
            else None
        ),
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
    }


def ticket_creation_json(conn: sqlite3.Connection, ticket: Ticket, now: int) -> JsonDict:
    definition = ticket.workflow
    destination = proposal_routing.parked_destination(
        proposal_routing.route_to_holder(ticket.ceiling_holder)
    )
    recipient = "the owner"
    if destination is not None and destination.kind is PrincipalKind.chief:
        recipient = "Chief"
    elif destination is not None and destination.kind is PrincipalKind.manager:
        row = conn.execute("SELECT title FROM managers WHERE id = ?", (destination.id,)).fetchone()
        assert row is not None
        recipient = f"the manager {row['title']}"
    return {
        **ticket_json(ticket, now),
        **supervision_receipt(
            ticket, worker_type_definition=definition, review_recipient=recipient
        ),
    }


# --- ticket reads --------------------------------------------------------------


def list_tickets(
    conn: sqlite3.Connection,
    now: int,
    *,
    stage: str | None,
    project_id: str | None,
    manager_id: str | None,
) -> list[JsonDict]:
    clauses: list[str] = []
    params: list[str] = []
    if stage is not None:
        clauses.append("tickets.stage = ?")
        params.append(str(stage))
    if project_id is not None:
        clauses.append(
            "CASE WHEN tickets.manager_id IS NOT NULL THEN managers.project_id "
            "ELSE tickets.project_id END = ?"
        )
        params.append(project_id)
    if manager_id is not None:
        clauses.append("tickets.manager_id = ?")
        params.append(manager_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT tickets.id FROM tickets "
        "LEFT JOIN managers ON managers.id = tickets.manager_id"
        + where
        + " ORDER BY tickets.created_at ASC, tickets.id",
        tuple(params),
    ).fetchall()
    return [
        _broken(conn, str(row["id"]))
        or ticket_json(tickets_data.read_ticket(conn, str(row["id"])), now)
        for row in rows
    ]


def _ticket_search_text(row: sqlite3.Row) -> str:
    definition = workflow_from_row(row)
    values = fields_codec.values_from_json(str(row["field_values"]), definition.field_ids())
    proposal = fields_codec.proposal_from_json(row["searchable_pending_proposal"])
    return "\n".join(
        (
            str(row["title"]),
            str(row["recap"]),
            *values.values(),
            proposal.body if proposal is not None else "",
        )
    )


def _ticket_summary_json(row: sqlite3.Row) -> JsonDict:
    ticket_status = str(row["ticket_status"])
    return {
        "is_backlogged": bool(row["is_backlogged"]),
        "waiting_to_closeout": (
            row["gating_field"] == CONSEQUENCES_FIELD_ID
            and ticket_status == TicketStatus.empty.value
        ),
        "id": str(row["id"]),
        "title": str(row["title"]),
        "locale": str(row["locale"]),
        "worker_type": row["worker_type"],
        "stage": str(row["stage"]),
        "ticket_status": ticket_status,
        "project_id": (str(row["project_id"]) if row["project_id"] is not None else None),
        "project": (str(row["project_name"]) if row["project_name"] is not None else None),
        "manager_id": (str(row["manager_id"]) if row["manager_id"] is not None else None),
        "manager": {
            "kind": str(row["manager_kind"]),
            "id": (str(row["manager_id"]) if row["manager_id"] is not None else "chief"),
        },
        "manager_title": (str(row["manager_title"]) if row["manager_title"] is not None else None),
        "recap_preview": str(row["recap_preview"]),
    }


# The summary list reads each Ticket's stored navigation record, which every write keeps
# current in its own transaction. Ticket text is read only when the caller searches.
_SUMMARY_RECORD_COLUMNS = ", ".join(
    f"json_extract(r.value, '$.{name}') AS {name}"
    for name in (
        "title",
        "locale",
        "worker_type",
        "stage",
        "is_backlogged",
        "ticket_status",
        "terminal",
        "gating_field",
        "project_id",
        "manager_id",
        "manager_kind",
        "recap_preview",
        "created_at",
    )
)


def list_ticket_summaries(
    conn: sqlite3.Connection,
    *,
    page_request: ListPageRequest,
    filters: TicketListFilters,
    project_id: str | None,
    manager_id: str | None,
) -> ListPage[JsonDict]:
    navigation_data.require_clean_read(conn)
    clauses = ["r.kind = 'ticket'"]
    params: list[str] = []
    if filters.manager is not None:
        clauses.append("json_extract(r.value, '$.manager_kind') = ?")
        params.append(filters.manager.kind.value)
        if filters.manager.kind.value == "manager":
            clauses.append("json_extract(r.value, '$.manager_id') = ?")
            params.append(filters.manager.id)
    if project_id is not None:
        clauses.append("json_extract(r.value, '$.project_id') = ?")
        params.append(project_id)
    if manager_id is not None:
        clauses.append("json_extract(r.value, '$.manager_id') = ?")
        params.append(manager_id)
    search_columns = (
        ", t.recap, t.field_values, t.workflow_json, "
        "t.pending_proposal AS searchable_pending_proposal"
        if filters.search
        else ""
    )
    rows = conn.execute(
        "SELECT r.id, "
        + _SUMMARY_RECORD_COLUMNS
        + ", json_type(r.value, '$.integrity_error') IS NOT NULL AS broken, "
        "projects.name AS project_name, managers.title AS manager_title"
        + search_columns
        + " FROM navigation_records r "
        "LEFT JOIN managers ON managers.id = json_extract(r.value, '$.manager_id') "
        "LEFT JOIN projects ON projects.id = json_extract(r.value, '$.project_id') "
        + ("JOIN tickets t ON t.id = r.id " if filters.search else "")
        + "WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at ASC, r.id",
        tuple(params),
    ).fetchall()
    search = filters.search.casefold() if filters.search else None
    statuses = {status.value for status in filters.ticket_statuses}
    excluded_statuses = {status.value for status in filters.excluded_ticket_statuses}
    matches: list[JsonDict] = []
    for row in rows:
        status = str(row["ticket_status"])
        if statuses and status not in statuses:
            continue
        if status in excluded_statuses:
            continue
        if row["broken"]:
            broken = _broken(conn, str(row["id"]))
            assert broken is not None
            if search is not None and search not in str(broken).casefold():
                continue
            matches.append(broken)
            continue
        stage = str(row["stage"])
        if (
            filters.is_backlogged is not None
            and bool(row["is_backlogged"]) != filters.is_backlogged
        ):
            continue
        if filters.worker_types and str(row["worker_type"]) not in filters.worker_types:
            continue
        if not filters.include_terminal and row["terminal"]:
            continue
        if filters.stages and stage not in filters.stages:
            continue
        if stage in filters.excluded_stages:
            continue
        if search is not None and search not in _ticket_search_text(row).casefold():
            continue
        matches.append(_ticket_summary_json(row))
    selected = matches[page_request.offset : page_request.offset + page_request.limit]
    return ListPage(
        rows=tuple(selected),
        match_count=len(matches),
        limit=page_request.limit,
        offset=page_request.offset,
    )


@contextmanager
def _coherent_read(conn: sqlite3.Connection) -> Iterator[None]:
    """Hold one read result together against writes landing under it.

    A deferred ``BEGIN`` fixes one WAL snapshot and does not reserve the writer.
    ``ROLLBACK`` closes an owned read on every exit without emitting the process change
    signal. An existing transaction owner keeps ownership and supplies its own boundary.

    This detail is assembled from several queries, so without it a reader could see a
    Ticket's row from before a move and its assignment from after.
    """
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if owns_transaction and conn.in_transaction:
            conn.execute("ROLLBACK")


def ticket_detail(conn: sqlite3.Connection, ticket_id: str, now: int) -> JsonDict:
    with _coherent_read(conn):
        # This read is also what fixes the snapshot. A deferred BEGIN reserves nothing
        # until a statement actually reads, so the Ticket's own row has to be read here
        # rather than further in, or the rows around it could come from after a write.
        broken = _broken(conn, ticket_id)
        if broken is not None:
            return broken
        ticket = tickets_data.read_ticket(conn, ticket_id)
        return _ticket_detail_rows(conn, ticket, now)


def _ticket_detail_rows(conn: sqlite3.Connection, ticket: Ticket, now: int) -> JsonDict:
    ticket_id = ticket.id
    detail = ticket_json(ticket, now)
    detail["record_sha256"] = tickets_data.ticket_record_sha256(conn, ticket_id)
    blocker_summary = ticket_blocks.blocker_summary(conn, ticket_id)
    conversation_rows = conn.execute(
        "SELECT ticket_conversations.conversation_id, conversations.created_at "
        "FROM ticket_conversations JOIN conversations "
        "ON conversations.conversation_id = ticket_conversations.conversation_id "
        "WHERE ticket_conversations.ticket_id = ? "
        "ORDER BY conversations.created_at, ticket_conversations.conversation_id",
        (ticket_id,),
    ).fetchall()
    detail.update(
        {
            "artifacts": _ticket_artifacts(conn, ticket_id),
            "blocked": blocker_summary.blocked,
            "employee_configuration_editable": tickets_data.employee_configuration_editable(ticket),
            "conversation_history": [
                {
                    "conversation_id": str(row["conversation_id"]),
                    "created_at": int(row["created_at"]),
                }
                for row in conversation_rows
            ],
        }
    )
    if blocker_summary.blocked:
        detail["blocker_summary"] = blocker_summary_json(blocker_summary)
    return detail


def _ticket_artifacts(conn: sqlite3.Connection, ticket_id: str) -> list[dict[str, object]]:
    try:
        root = ticket_files_root(database_path(conn)) / ticket_id
    except RuntimeError:
        # In-memory read fixtures have no managed-file directory.
        return []
    if any(path.is_symlink() for path in (root, *root.parents)):
        return []
    return entries_as_json(fold_directory(root))


def copy_text(conn: sqlite3.Connection, ticket_id: str) -> str:
    broken = _broken(conn, ticket_id)
    if broken is not None:
        return str(broken)
    ticket = tickets_data.read_ticket(conn, ticket_id)
    worker_type_definition = ticket.workflow
    ownership = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )

    def show(value: str | None) -> str:
        return value if value else "(none)"

    blocker_summary = ticket_blocks.blocker_summary(conn, ticket_id)
    blocked_by_rows = tuple(row for row in blocker_summary.blocked_by if row.active)
    blocked_by_block = (
        "\n".join(f"- {row.title} ({row.ticket_id}, {row.stage})" for row in blocked_by_rows)
        if blocked_by_rows
        else "(none)"
    )
    field_blocks = "".join(
        f"{field_id}:\n{show(ticket.field_values.get(field_id))}\n\n"
        for field_id in worker_type_definition.field_ids()
    )
    return (
        f"{ticket.title}\n"
        f"stage: {str(ticket.stage)}\n"
        f"employee_backend: {ticket.employee_backend}\n"
        "owner: "
        f"{ownership.value if ownership is not None else '(none)'}\n"
        f"\n"
        f"{field_blocks}"
        f"pending proposal:\n"
        f"{show(ticket.pending_proposal.body if ticket.pending_proposal else None)}\n"
        f"recap:\n{show(ticket.recap)}\n"
        f"\n"
        f"blocked_by:\n{blocked_by_block}\n"
    )


# --- Review --------------------------------------------------------------------


def _review_items(conn: sqlite3.Connection) -> list[JsonDict]:
    rows = conn.execute(
        "SELECT id, title, stage, worker_type, workflow_json, worker_step_claim, "
        "pending_proposal, ceiling_holder, is_backlogged FROM tickets "
        "ORDER BY id",
    ).fetchall()
    blocked_ticket_ids = ticket_blocks.blocked_ticket_ids(conn)
    items: list[JsonDict] = []
    for row in rows:
        if _broken(conn, str(row["id"])) is not None:
            continue
        facts = derivation.derive_ticket_facts(
            derivation.stored_facts_from_row(
                row, has_live_blocker=str(row["id"]) in blocked_ticket_ids
            )
        )
        if facts.ticket_status is not TicketStatus.awaiting_approval:
            continue
        if bool(row["is_backlogged"]):
            continue
        holder = json.loads(str(row["ceiling_holder"]))
        if holder != {"kind": "owner", "id": "owner"}:
            continue
        worker_type_definition = workflow_from_row(row)
        stage = str(row["stage"])
        field = worker_type_definition.gating_field(stage)
        if field is None:
            continue
        proposal = fields_codec.proposal_from_json(row["pending_proposal"])
        if not eligible_for_owner_review(
            ticket_status=facts.ticket_status,
            is_backlogged=bool(row["is_backlogged"]),
            holder_is_owner=holder == {"kind": "owner", "id": "owner"},
            has_gating_field=field is not None,
            has_proposal=proposal is not None,
        ):
            continue
        assert proposal is not None
        items.append(
            {
                "review_item_type": "proposal",
                "ticket_id": str(row["id"]),
                "field": field,
                "title": str(row["title"]),
                "waiting_since": proposal.created_at,
            }
        )
    items.extend(review_message_items(conn))
    return cast(
        list[JsonDict],
        group_review_items(cast(list[ReviewProposalItem | ReviewMessageItem], items)),
    )


def review_view(
    conn: sqlite3.Connection,
) -> JsonDict:
    return {"items": _review_items(conn)}
