"""Ticket read-view assembly: per-entity serializers, Ticket detail, the board,
Ticket-only Review, and copy text. Pure read assembly — no FastAPI, pydantic, writes,
or imports from an API module."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict

from planner.core import links as core_links
from planner.core.contracts import BlockerSummary, JsonDict
from planner.judgments import data as judgments_data
from planner.list_reads.configuration import TICKET_RECAP_PREVIEW_CHARS
from planner.list_reads.contracts import ListPage, ListPageRequest
from planner.runtime import conversation_start
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    AtCap,
    BoardCard,
    BoardSprintItem,
    Ticket,
    TicketListFilters,
    TicketStatus,
)
from planner.tickets.logic import fields_codec, machine
from planner.worker_types.configuration import configured_worker_type_registry

# §7.2 priority band: P0 first. The board reuses the same triple the dispatcher orders by.
_PRIORITY_RANK = ("P0", "P1", "P2", "P3")


def _prio_rank(priority: str) -> int:
    return _PRIORITY_RANK.index(priority)


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
        "sprint_id": ticket.sprint_id,
        "sprint_item_id": ticket.sprint_item_id,
        "effective_sprint_id": ticket.effective_sprint_id,
        "resolved_priority_anchors": {
            "sprint_item": (
                {
                    "id": ticket.resolved_priority_anchors.sprint_item.id,
                    "title": ticket.resolved_priority_anchors.sprint_item.title,
                    "priority": ticket.resolved_priority_anchors.sprint_item.priority.value,
                }
                if ticket.resolved_priority_anchors.sprint_item is not None
                else None
            ),
            "project": (
                {
                    "id": ticket.resolved_priority_anchors.project.id,
                    "name": ticket.resolved_priority_anchors.project.name,
                    "priority": (
                        ticket.resolved_priority_anchors.project.priority.value
                        if ticket.resolved_priority_anchors.project.priority is not None
                        else None
                    ),
                }
                if ticket.resolved_priority_anchors.project is not None
                else None
            ),
        },
        "recap": ticket.recap,
        "guidance": ticket.guidance,
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
        "conversation_id": ticket.conversation_id,
        "alias": ticket.alias,
        "field_values": dict(ticket.field_values),
        "pending_proposal": asdict(ticket.pending_proposal)
        if ticket.pending_proposal is not None
        else None,
        "archived_field_content": ticket.archived_field_content,
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
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
        clauses.append("tickets.stage = ?")
        params.append(str(stage))
    if project_id is not None:
        clauses.append("tickets.project_id = ?")
        params.append(project_id)
    if sprint_id is not None:
        if sprint_id == "null":
            clauses.append("tickets.sprint_id IS NULL")
        else:
            clauses.append("tickets.sprint_id = ?")
            params.append(sprint_id)
    if sprint_item_id is not None:
        clauses.append("tickets.sprint_item_id = ?")
        params.append(sprint_item_id)
    if day_id is not None:  # scope to one day's board via the day_tickets join
        clauses.append("tickets.id IN (SELECT ticket_id FROM day_tickets WHERE day_id = ?)")
        params.append(day_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT tickets.id FROM tickets "
        "LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id"
        + where
        + " ORDER BY tickets.created_at ASC, tickets.id",
        tuple(params),
    ).fetchall()
    return [ticket_json(tickets_data.read_ticket(conn, str(row["id"])), now) for row in rows]


def _ticket_search_text(row: sqlite3.Row) -> str:
    registry = configured_worker_type_registry()
    definition = registry.require(str(row["worker_type"]))
    values = fields_codec.values_from_json(str(row["field_values"]), definition.field_ids())
    proposal = fields_codec.proposal_from_json(row["pending_proposal"])
    return "\n".join(
        (
            str(row["title"]),
            str(row["recap"]),
            str(row["guidance"]),
            str(row["archived_field_content"]),
            *values.values(),
            proposal.body if proposal is not None else "",
        )
    )


def _recap_preview(recap: str) -> str:
    compact = " ".join(recap.split())
    if len(compact) <= TICKET_RECAP_PREVIEW_CHARS:
        return compact
    return compact[: TICKET_RECAP_PREVIEW_CHARS - 1].rstrip() + "…"


def _ticket_summary_json(row: sqlite3.Row) -> JsonDict:
    return {
        "id": str(row["id"]),
        "title": str(row["title"]),
        "worker_type": str(row["worker_type"]),
        "stage": str(row["stage"]),
        "ticket_status": str(row["ticket_status"]),
        "priority": str(row["priority"]),
        "project_id": (
            str(row["effective_project_id"]) if row["effective_project_id"] is not None else None
        ),
        "project": (str(row["project_name"]) if row["project_name"] is not None else None),
        "sprint_item_id": (
            str(row["sprint_item_id"]) if row["sprint_item_id"] is not None else None
        ),
        "sprint_item": (
            str(row["sprint_item_title"]) if row["sprint_item_title"] is not None else None
        ),
        "sprint_id": str(row["sprint_id"]) if row["sprint_id"] is not None else None,
        "effective_sprint_id": str(row["sprint_id"]) if row["sprint_id"] is not None else None,
        "recap_preview": _recap_preview(str(row["recap"])),
    }


def list_ticket_summaries(
    conn: sqlite3.Connection,
    *,
    page_request: ListPageRequest,
    filters: TicketListFilters,
    project_id: str | None,
    sprint_id: str | None,
    sprint_item_id: str | None,
    day_id: str | None = None,
    day_order: bool = False,
) -> ListPage[JsonDict]:
    clauses: list[str] = []
    params: list[str] = []
    if project_id is not None:
        clauses.append("tickets.project_id = ?")
        params.append(project_id)
    if sprint_id is not None:
        if sprint_id == "null":
            clauses.append("tickets.sprint_id IS NULL")
        else:
            clauses.append("tickets.sprint_id = ?")
            params.append(sprint_id)
    if sprint_item_id is not None:
        clauses.append("tickets.sprint_item_id = ?")
        params.append(sprint_item_id)
    join_day = ""
    if day_id is not None:
        join_day = " JOIN day_tickets ON day_tickets.ticket_id = tickets.id"
        clauses.append("day_tickets.day_id = ?")
        params.append(day_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = (
        "day_tickets.position ASC, tickets.id"
        if day_order
        else "tickets.created_at ASC, tickets.id"
    )
    searchable_fields = "tickets.field_values" if filters.search else "NULL"
    searchable_proposal = "tickets.pending_proposal" if filters.search else "NULL"
    searchable_archive = "tickets.archived_field_content" if filters.search else "NULL"
    searchable_guidance = "tickets.guidance" if filters.search else "NULL"
    rows = conn.execute(
        "SELECT tickets.id, tickets.title, tickets.worker_type, tickets.stage, "
        "tickets.ticket_status, tickets.priority, tickets.recap, "
        + searchable_guidance
        + " AS guidance, "
        + searchable_fields
        + " AS field_values, "
        + searchable_proposal
        + " AS pending_proposal, "
        + searchable_archive
        + " AS archived_field_content, "
        "tickets.project_id AS effective_project_id, "
        "projects.name AS project_name, tickets.sprint_item_id, "
        "sprint_items.title AS sprint_item_title, tickets.sprint_id "
        "FROM tickets "
        "LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id "
        "LEFT JOIN projects ON projects.id = tickets.project_id"
        + join_day
        + where
        + " ORDER BY "
        + order,
        tuple(params),
    ).fetchall()
    registry = configured_worker_type_registry()
    search = filters.search.casefold() if filters.search else None
    matches: list[sqlite3.Row] = []
    for row in rows:
        stage = str(row["stage"])
        status = TicketStatus(str(row["ticket_status"]))
        definition = registry.require(str(row["worker_type"]))
        if not filters.include_terminal and definition.is_terminal(stage):
            continue
        if filters.stages and stage not in filters.stages:
            continue
        if stage in filters.excluded_stages:
            continue
        if filters.ticket_statuses and status not in filters.ticket_statuses:
            continue
        if status in filters.excluded_ticket_statuses:
            continue
        if search is not None and search not in _ticket_search_text(row).casefold():
            continue
        matches.append(row)
    selected = matches[page_request.offset : page_request.offset + page_request.limit]
    return ListPage(
        rows=tuple(_ticket_summary_json(row) for row in selected),
        match_count=len(matches),
        limit=page_request.limit,
        offset=page_request.offset,
    )


def ticket_detail(conn: sqlite3.Connection, ticket_id: str, now: int) -> JsonDict:
    ticket = tickets_data.read_ticket(conn, ticket_id)
    detail = ticket_json(ticket, now)
    day_rows = conn.execute(
        "SELECT day_id FROM day_tickets WHERE ticket_id = ? ORDER BY day_id ASC",
        (ticket_id,),
    ).fetchall()
    blocker_summary = core_links.blocker_summary(conn, ticket_id)
    conversation_rows = conn.execute(
        "SELECT ticket_conversations.conversation_id, conversations.created_at "
        "FROM ticket_conversations JOIN conversations "
        "ON conversations.conversation_id = ticket_conversations.conversation_id "
        "WHERE ticket_conversations.ticket_id = ? "
        "ORDER BY conversations.created_at, ticket_conversations.conversation_id",
        (ticket_id,),
    ).fetchall()
    judgment = judgments_data.read_ticket_judgment(conn, ticket_id)
    detail.update(
        {
            "blocked": blocker_summary.blocked,
            "day_ids": [str(r["day_id"]) for r in day_rows],
            "employee_configuration_editable": tickets_data.employee_configuration_editable(ticket),
            "conversation_history": [
                {
                    "conversation_id": str(row["conversation_id"]),
                    "created_at": int(row["created_at"]),
                }
                for row in conversation_rows
            ],
            "verdict": (
                {
                    "rating": judgment.verdict_rating,
                    "text": judgment.verdict_text,
                }
                if judgment is not None
                and (judgment.verdict_rating is not None or judgment.verdict_text is not None)
                else None
            ),
            "trouble_notes": [
                {
                    "sequence": note.sequence,
                    "body": note.body,
                    "created_at": note.created_at,
                }
                for note in (judgment.trouble_notes if judgment is not None else ())
            ],
        }
    )
    if blocker_summary.blocked:
        detail["blocker_summary"] = blocker_summary_json(blocker_summary)
    return detail


def copy_text(conn: sqlite3.Connection, ticket_id: str) -> str:
    ticket = tickets_data.read_ticket(conn, ticket_id)
    worker_type_definition = configured_worker_type_registry().require(ticket.worker_type)

    def show(value: str | None) -> str:
        return value if value else "(none)"

    blocker_summary = core_links.blocker_summary(conn, ticket_id)
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
        f"priority: {ticket.priority.value}\n"
        f"employee_backend: {ticket.employee_backend}\n"
        "owner: "
        f"{ticket.effective_stage_ownership_mode.value if ticket.effective_stage_ownership_mode is not None else '(none)'}\n"  # noqa: E501
        f"\n"
        f"{field_blocks}"
        f"pending proposal:\n"
        f"{show(ticket.pending_proposal.body if ticket.pending_proposal else None)}\n"
        f"\nhistorical record:\n{show(ticket.archived_field_content)}\n"
        f"recap:\n{show(ticket.recap)}\n"
        f"\nguidance:\n{show(ticket.guidance)}\n"
        f"\n"
        f"blocked_by:\n{blocked_by_block}\n"
    )


# --- board (§10.3) -------------------------------------------------------------


def board_view(conn: sqlite3.Connection, *, day_id: str) -> JsonDict:
    """The current planning day's roster of Ticket cards, read straight from the database.

    Ticket detail remains a separate resource, so narrowing this projection does not
    constrain direct Ticket routes or an already-open inspector.

    Each card carries ``conversation_id``: the Ticket's conversation link, which under
    the new conversation system is the caller-owned conversation id stored in the
    ``conversation_id`` column. It is what the board route asks the conversation
    system about. The async route uses that link to add the owner's server-side read
    position alongside the conversation signals.

    None of the three row signals is a database fact of the tickets domain, so none is
    answered here: whether the worker is running (``agent_working``), whether it is
    waiting on a permission ask (``needs_me``), and where its conversation last had a
    turn end (``latest_turn_ended_sequence``), and the owner's durable read position
    (``owner_read_through_sequence``) all belong to the conversation system and are added
    by the async board route, which can await it.

    Beside the columns, ``sprint_items`` carries each represented Sprint Item's own
    supervisor conversation. A card answers for its Ticket's worker, and no card answers
    for the Item's own worker, so the rail cannot mark an Item's title without this.
    """
    rows = conn.execute(
        "SELECT tickets.id, tickets.title, tickets.stage, tickets.priority, tickets.deadline, "
        "tickets.project_id, ticket_projects.name AS project_name, tickets.sprint_item_id, "
        "sprint_items.project_id AS parent_project_id, "
        "sprint_items.title AS sprint_item_title, "
        "sprint_items.priority AS sprint_item_priority, "
        "parent_projects.name AS parent_project_name, "
        "tickets.pending_proposal, tickets.worker_type, "
        "tickets.employee_backend, "
        "tickets.conversation_id, "
        "tickets.ticket_status, "
        "tickets.ceiling, tickets.at_cap, "
        "tickets.backend_error, "
        "tickets.created_at, tickets.updated_at FROM tickets "
        "LEFT JOIN projects AS ticket_projects ON ticket_projects.id = tickets.project_id "
        "LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id "
        "LEFT JOIN projects AS parent_projects ON parent_projects.id = sprint_items.project_id "
        "JOIN day_tickets ON day_tickets.ticket_id = tickets.id "
        "WHERE day_tickets.day_id = ? AND tickets.stage != 'dropped'",
        (day_id,),
    ).fetchall()
    registry = configured_worker_type_registry()
    blocked_target_ids = core_links.blocked_target_ids(conn)
    coding_order = registry.require("coding").stage_ids()
    column_order: list[str] = list(coding_order)
    by_stage: dict[str, list[tuple[tuple[int, int, str, int], BoardCard]]] = {
        sid: [] for sid in column_order
    }
    for row in rows:
        stage = str(row["stage"])
        priority = str(row["priority"])
        deadline = str(row["deadline"]) if row["deadline"] is not None else None
        worker_type = str(row["worker_type"])
        worker_type_definition = registry.require(worker_type)
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
        ticket_status = str(row["ticket_status"])
        stopped_at_current_stage = str(
            row["at_cap"]
        ) == AtCap.stop.value and machine.at_or_beyond_ceiling(
            stage,
            str(row["ceiling"]),
            worker_type_definition=worker_type_definition,
        )
        card: BoardCard = {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "priority": priority,
            "deadline": deadline,
            "project_id": group_project_id,
            "project": group_project_name,
            "group_project_id": group_project_id,
            "group_project": group_project_name,
            "activity_at": int(row["updated_at"]),
            "has_pending_proposal": row["pending_proposal"] is not None,
            "ticket_status": ticket_status,
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
            "blocked": str(row["id"]) in blocked_target_ids,
            "conversation_id": (
                str(row["conversation_id"]) if row["conversation_id"] is not None else None
            ),
            "waiting_to_closeout": (
                gating_field_id == "closeout"
                and ticket_status == TicketStatus.empty.value
                and not stopped_at_current_stage
            ),
            "sprint_item_id": (
                str(row["sprint_item_id"]) if row["sprint_item_id"] is not None else None
            ),
            "sprint_item_title": (
                str(row["sprint_item_title"]) if row["sprint_item_title"] is not None else None
            ),
            "sprint_item_priority": (
                str(row["sprint_item_priority"])
                if row["sprint_item_priority"] is not None
                else None
            ),
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
    return {
        "columns": columns,
        "sprint_items": _board_sprint_items(
            conn,
            item_ids=sorted(
                {str(row["sprint_item_id"]) for row in rows if row["sprint_item_id"] is not None}
            ),
        ),
    }


def _board_sprint_items(
    conn: sqlite3.Connection,
    *,
    item_ids: list[str],
) -> list[BoardSprintItem]:
    """Each Sprint Item's creation stamp and its supervisor's conversation.

    The supervisor is an ordinary non-Ticket agent, so its conversation is the one the
    ``agents`` roster holds under the Item's ``supervisor_agent_key``. An Item nobody
    has spoken to has none, and reads as ``None``.

    The Item's half of the row mark is its conversation's last turn end, the same fact a
    card uses. That is not a database fact this read owns, so it arrives later, beside
    the two live conversation signals.
    """
    if not item_ids:
        return []
    placeholders = ",".join("?" * len(item_ids))
    rows = conn.execute(
        "SELECT id, created_at, supervisor_agent_key "
        f"FROM sprint_items WHERE id IN ({placeholders})",
        item_ids,
    ).fetchall()
    conversations = conversation_start.read_agent_conversations(
        conn, [str(row["supervisor_agent_key"]) for row in rows]
    )
    return [
        BoardSprintItem(
            id=str(row["id"]),
            created_at=int(row["created_at"]),
            conversation_id=conversations.get(str(row["supervisor_agent_key"])),
        )
        for row in sorted(rows, key=lambda row: str(row["id"]))
    ]


# --- Review --------------------------------------------------------------------


def _review_items(conn: sqlite3.Connection, *, day_id: str) -> list[JsonDict]:
    rows = conn.execute(
        "SELECT id, title, stage, worker_type, ticket_status, ticket_status_changed_at, "
        "pending_proposal FROM tickets "
        "WHERE id IN (SELECT ticket_id FROM day_tickets WHERE day_id = ?) ORDER BY id",
        (day_id,),
    ).fetchall()
    registry = configured_worker_type_registry()
    items: list[JsonDict] = []
    for row in rows:
        ticket_status = str(row["ticket_status"])
        if ticket_status == TicketStatus.needs_user.value:
            items.append(
                {
                    "review_item_type": TicketStatus.needs_user.value,
                    "ticket_id": str(row["id"]),
                    "title": str(row["title"]),
                    "waiting_since": int(row["ticket_status_changed_at"]),
                }
            )
            continue
        if ticket_status != TicketStatus.awaiting_approval.value:
            continue
        worker_type_definition = registry.require(str(row["worker_type"]))
        stage = str(row["stage"])
        field = worker_type_definition.gating_field(stage)
        if field is None:
            continue
        proposal = fields_codec.proposal_from_json(row["pending_proposal"])
        if proposal is None:
            continue
        items.append(
            {
                "review_item_type": "proposal",
                "ticket_id": str(row["id"]),
                "field": field,
                "title": str(row["title"]),
                "waiting_since": proposal.created_at,
            }
        )
    items.sort(key=lambda item: (item["waiting_since"], item["ticket_id"]))
    return items


def review_view(
    conn: sqlite3.Connection,
    *,
    day_id: str,
) -> JsonDict:
    running_workers = conn.execute(
        "SELECT COUNT(*) AS count FROM tickets WHERE ticket_status = 'agent'"
    ).fetchone()
    return {
        "items": _review_items(conn, day_id=day_id),
        "running_worker_count": int(running_workers["count"] if running_workers is not None else 0),
    }
