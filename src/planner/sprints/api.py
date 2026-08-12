"""Sprint-item, sprint, current-sprint view, and idea routes (§9). Thin HTTP shells
over the stage-3 sprint writers and the sprint read views. Ideas are homed here
because the Idea shape lives in this domain's contracts. Sprint writers take a
`clock: Clock`, not `now: int` — do not mix them up."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter

from planner.conversation.api import OwnerSendBody, conversation_message_content, delivery_fate_json
from planner.conversation.contracts import PromptDeliveryStarted, require_conversation_backend_key
from planner.core.authctx import (
    reject_agent_fields,
    require_direct_write,
    require_planning_write,
    require_sprint_item_supervisor_read,
    require_sprint_item_supervisor_ticket_write,
    require_ticket_worker_write,
)
from planner.core.contracts import JsonDict, LinkKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days import actions as days_actions
from planner.days.logic.dates import planning_date, resolve_day_id
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.list_reads.contracts import ListPageRequest
from planner.projects import data as projects_data
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.sprints import supervisor_service
from planner.sprints import views as sprints_views
from planner.sprints.contracts import (
    KICKOFF_FIELDS,
    MID_SPRINT_FIELDS,
    REVIEW_FIELDS,
    CreateIdeaBody,
    CreateItemBody,
    CreateSprintBody,
    ItemStatus,
    MoveItemTicketBody,
    SprintItemSupervisorLaunchConfiguration,
)
from planner.supervisor_obligations import data as supervisor_obligations_data
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets.api import (
    Cfg,
    Clk,
    Conversations,
    Ctx,
    DbConn,
    MessageFiles,
    WorkerContext,
    _marshal_accept,
    _parse_next_ceiling,
    _parse_scope_at_cap,
    body_opt_str,
    body_str,
    parse_enum,
)
from planner.tickets.contracts import TITLE_MAX_CHARS, AtCap, TicketEdit
from planner.worker_types.configuration import configured_worker_type_registry

router = APIRouter()

_SPRINT_TEXT_FIELDS = ("name",) + KICKOFF_FIELDS + MID_SPRINT_FIELDS + REVIEW_FIELDS
_ITEM_PLAIN_FIELDS = ("title", "body", "priority", "deadline", "project_id")


# --- request-body marshallers (contract shapes in sprints/contracts.py) ---------


def _marshal_create_item(raw: JsonDict) -> CreateItemBody:
    return CreateItemBody(
        title=body_str(raw, "title"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
        body=body_str(raw, "body"),
        priority=body_opt_str(raw, "priority"),
        deadline=body_opt_str(raw, "deadline"),
        sprint_id=body_opt_str(raw, "sprint_id"),
    )


def _marshal_create_sprint(raw: JsonDict) -> CreateSprintBody:
    return CreateSprintBody(
        name=body_str(raw, "name"),
        date_start=body_str(raw, "date_start"),
        date_end=body_str(raw, "date_end"),
        limiting_factor=body_str(raw, "limiting_factor"),
        primary_bet=body_str(raw, "primary_bet"),
        supports=body_str(raw, "supports"),
        premortem=body_str(raw, "premortem"),
    )


def _marshal_create_idea(raw: JsonDict) -> CreateIdeaBody:
    return CreateIdeaBody(
        title=body_str(raw, "title"),
        body=body_str(raw, "body"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
    )


def _marshal_item_deadline(raw: object) -> None:
    if raw is None:
        return
    if not isinstance(raw, str):
        raise PlannerError(ErrorCode.validation, "invalid deadline", {"deadline": raw})
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise PlannerError(ErrorCode.validation, "invalid deadline", {"deadline": raw}) from exc


# --- item routes ---------------------------------------------------------------


@router.post("/items")
async def create_item(raw: dict[str, Any], conn: DbConn, clk: Clk) -> JsonDict:
    body = _marshal_create_item(raw)
    project = projects_data.resolve_project(
        conn, project_id=body["project_id"], project_name=body["project"], required=True
    )
    assert project is not None
    priority = (
        parse_enum(Priority, body["priority"], "priority")
        if body["priority"] is not None
        else Priority.P3
    )
    _marshal_item_deadline(body["deadline"])
    item = sprints_data.create_item(
        conn,
        title=body["title"],
        project_id=project.id,
        body=body["body"],
        priority=priority,
        deadline=body["deadline"],
        sprint_id=body["sprint_id"],
        clock=clk,
    )
    return sprints_views.item_detail(conn, item.id)


@router.get("/items")
async def list_items(
    conn: DbConn,
    status: str | None = None,
    project: str | None = None,
    project_id: str | None = None,
    sprint_id: str | None = None,
) -> JsonDict:
    status_enum = parse_enum(ItemStatus, status, "status") if status is not None else None
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    return {
        "items": sprints_views.list_items(
            conn,
            status=status_enum,
            project_id=resolved_project.id if resolved_project is not None else None,
            sprint_id_filter=sprint_id,
        )
    }


@router.get("/sprint-item-summaries")
async def list_item_summaries(
    conn: DbConn,
    status: str | None = None,
    project: str | None = None,
    project_id: str | None = None,
    sprint_id: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> JsonDict:
    status_enum = parse_enum(ItemStatus, status, "status") if status is not None else None
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    page = sprints_views.list_item_summaries(
        conn,
        page_request=ListPageRequest(limit=limit, offset=offset),
        status=status_enum,
        project_id=resolved_project.id if resolved_project is not None else None,
        sprint_id_filter=sprint_id,
    )
    return page.response("items")


@router.get("/items/{item_id}")
async def get_item(item_id: str, conn: DbConn) -> JsonDict:
    return sprints_views.item_detail(conn, item_id)


def _supervisor_json(conn: DbConn, item_id: str) -> JsonDict:
    item = sprints_data.read_item(conn, item_id).item
    launch = item.supervisor_launch_configuration
    return {
        "sprint_item_id": item.id,
        "agent_key": item.supervisor_agent_key,
        "conversation_id": conversation_start.read_agent_conversation(
            conn, item.supervisor_agent_key
        ),
        "launch_configuration": {
            "employee_backend": launch.employee_backend.value,
            "employee_launch_model": launch.employee_launch_model,
            "employee_launch_reasoning_effort": launch.employee_launch_reasoning_effort,
        },
    }


@router.get("/items/{item_id}/supervisor")
async def get_item_supervisor(item_id: str, conn: DbConn, ctx: Ctx) -> JsonDict:
    require_sprint_item_supervisor_read(conn, ctx, item_id)
    return _supervisor_json(conn, item_id)


def _obligation_json(obligation) -> JsonDict:
    return {
        "id": obligation.id,
        "sprint_item_id": obligation.sprint_item_id,
        "ticket_id": obligation.ticket_id,
        "kind": obligation.kind.value,
        "source_identity": obligation.source_identity,
        "lifecycle": obligation.lifecycle.value,
        "delivery_id": obligation.delivery_id,
        "attempt_count": obligation.attempt_count,
        "retry_at": obligation.retry_at,
        "last_error": obligation.last_error,
        "created_at": obligation.created_at,
        "updated_at": obligation.updated_at,
        "acknowledged_at": obligation.acknowledged_at,
    }


@router.get("/items/{item_id}/supervisor/obligations")
async def get_supervisor_obligations(
    item_id: str, conn: DbConn, ctx: Ctx, limit: int = 50, open_only: bool = True
) -> JsonDict:
    require_sprint_item_supervisor_read(conn, ctx, item_id)
    return {
        "sprint_item_id": item_id,
        "obligations": [
            _obligation_json(obligation)
            for obligation in supervisor_obligations_data.list_for_item(
                conn, item_id, limit=limit, open_only=open_only
            )
        ],
    }


@router.post("/items/{item_id}/supervisor/obligations/acknowledge")
async def acknowledge_supervisor_obligations(
    item_id: str, body: JsonDict, conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    require_sprint_item_supervisor_read(conn, ctx, item_id)
    raw_ids = body.get("obligation_ids")
    if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
        raise PlannerError(ErrorCode.validation, "obligation_ids must be a list of IDs", {})
    count = supervisor_obligations_data.acknowledge(conn, item_id, raw_ids, clk.now_unix())
    return {"sprint_item_id": item_id, "acknowledged": count}


@router.get("/items/{item_id}/supervisor/context")
async def get_item_supervisor_context(item_id: str, conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    require_sprint_item_supervisor_read(conn, ctx, item_id)
    item = sprints_data.read_item(conn, item_id).item
    return {
        "sprint_item": {
            "id": item.id,
            "title": item.title,
            "body": item.body,
            "project_id": item.project_id,
            "sprint_id": item.sprint_id,
        },
        "supervisor": _supervisor_json(conn, item_id),
        "tickets": [
            tickets_views.ticket_detail(conn, str(ticket["id"]), clk.now_unix())
            for ticket in sprints_views.item_tickets(conn, item_id)
        ],
    }


@router.get("/items/{item_id}/supervisor/tickets/{ticket_id}/context")
async def get_supervisor_ticket_context(
    item_id: str,
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    triggering_message_sequence: int | None = None,
) -> JsonDict:
    return supervisor_service.ticket_context(
        conn,
        ctx,
        item_id,
        ticket_id,
        now=clk.now_unix(),
        triggering_message_sequence=triggering_message_sequence,
    )


@router.get("/items/{item_id}/supervisor/tickets/{ticket_id}/history")
async def get_supervisor_ticket_history(
    item_id: str,
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    limit: int = 30,
    before_sequence: int | None = None,
) -> JsonDict:
    return supervisor_service.conversation_history(
        conn,
        ctx,
        item_id,
        ticket_id,
        limit=limit,
        before_sequence=before_sequence,
    )


@router.post("/items/{item_id}/supervisor/tickets/{ticket_id}/message")
async def supervisor_message_worker(
    item_id: str,
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    return await supervisor_service.message_current_worker(
        conversations,
        conn,
        ctx,
        item_id,
        ticket_id,
        conversation_id=body_str(raw, "conversation_id"),
        message=body_str(raw, "message"),
        now=clk.now_unix(),
    )


@router.patch("/items/{item_id}/supervisor/item")
async def supervisor_update_item(
    item_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_sprint_item_supervisor_read(conn, ctx, item_id)
    if len(raw) != 1:
        raise PlannerError(ErrorCode.validation, "set exactly one Sprint Item field", {})
    field, raw_value = next(iter(raw.items()))
    if field not in _ITEM_PLAIN_FIELDS:
        raise PlannerError(ErrorCode.validation, "unknown item field", {"field": field})
    if raw_value is not None and not isinstance(raw_value, str):
        raise PlannerError(ErrorCode.validation, "invalid item field value", {"field": field})
    if field in {"title", "body", "priority", "project_id"} and raw_value is None:
        raise PlannerError(
            ErrorCode.validation, "Sprint Item field cannot be cleared", {"field": field}
        )
    if field == "title" and raw_value == "":
        raise PlannerError(ErrorCode.validation, "item title is required", {})
    if field == "deadline":
        _marshal_item_deadline(raw_value)
    item = sprints_data.update_item_field(conn, item_id, field, raw_value, clock=clk)
    return sprints_views.item_detail(conn, item.id)


@router.patch("/items/{item_id}/supervisor/tickets/{ticket_id}")
async def supervisor_update_ticket(
    item_id: str,
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    supervisor_service.require_current_child(conn, ctx, item_id, ticket_id)
    if len(raw) != 1:
        raise PlannerError(ErrorCode.validation, "set exactly one Ticket field", {})
    field, value = next(iter(raw.items()))
    edit = TicketEdit()
    if field == "title":
        edit["title"] = body_str(raw, field)
    elif field == "priority":
        edit["priority"] = parse_enum(Priority, body_str(raw, field), field)
    elif field == "deadline":
        edit["deadline"] = body_opt_str(raw, field)
    else:
        raise PlannerError(ErrorCode.validation, "unknown Ticket field", {"field": field})
    ticket = tickets_data.edit_ticket(
        conn,
        ticket_id,
        edit=edit,
        title_max_chars=TITLE_MAX_CHARS,
        actor=ctx.actor,
        now=clk.now_unix(),
        supervisor_sprint_item_id=item_id,
    )
    return tickets_views.ticket_json(ticket, clk.now_unix())


@router.post("/items/{item_id}/supervisor/tickets/{ticket_id}/scope")
async def supervisor_change_ticket_scope(
    item_id: str,
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    supervisor_service.require_current_child(conn, ctx, item_id, ticket_id)
    ceiling = body_str(raw, "ceiling")
    at_cap_raw = body_str(raw, "at_cap")
    try:
        at_cap = AtCap(at_cap_raw)
    except ValueError:
        raise PlannerError(
            ErrorCode.scope_invalid, "unknown at_cap", {"at_cap": at_cap_raw}
        ) from None
    ticket = tickets_data.change_scope(
        conn,
        ticket_id,
        ceiling=ceiling,
        at_cap=at_cap,
        actor=ctx.actor,
        now=clk.now_unix(),
        supervisor_sprint_item_id=item_id,
    )
    return tickets_views.ticket_json(ticket, clk.now_unix())


@router.post("/items/{item_id}/supervisor/days/{date}/tickets/{ticket_id}")
async def supervisor_add_ticket_to_day(
    item_id: str,
    date: str,
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
) -> JsonDict:
    day_id = resolve_day_id(date, clk.now(), cfg.boundary_hour)
    days_actions.add_ticket_to_day(
        conn,
        day_id,
        ticket_id,
        now=clk.now_unix(),
        admit=lambda: require_sprint_item_supervisor_ticket_write(conn, ctx, item_id, ticket_id),
    )
    return {"sprint_item_id": item_id, "ticket_id": ticket_id, "day_id": day_id}


@router.delete("/items/{item_id}/supervisor/days/{date}/tickets/{ticket_id}")
async def supervisor_remove_ticket_from_day(
    item_id: str,
    date: str,
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
) -> JsonDict:
    day_id = resolve_day_id(date, clk.now(), cfg.boundary_hour)
    days_actions.remove_ticket_from_day(
        conn,
        day_id,
        ticket_id,
        now=clk.now_unix(),
        admit=lambda: require_sprint_item_supervisor_ticket_write(conn, ctx, item_id, ticket_id),
    )
    return {"sprint_item_id": item_id, "ticket_id": ticket_id, "day_id": day_id}


def _require_supervisor_block_scope(
    conn: DbConn, ctx: Ctx, item_id: str, from_id: str, to_id: str
) -> None:
    require_sprint_item_supervisor_ticket_write(conn, ctx, item_id, from_id)
    if to_id == item_id:
        require_sprint_item_supervisor_read(conn, ctx, item_id)
    else:
        require_sprint_item_supervisor_ticket_write(conn, ctx, item_id, to_id)


@router.post("/items/{item_id}/supervisor/blocks")
async def supervisor_add_block(
    item_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    from_id = body_str(raw, "from_id")
    to_id = body_str(raw, "to_id")
    tickets_actions.add_link(
        conn,
        from_id,
        to_id,
        LinkKind.blocks,
        now=clk.now_unix(),
        admit=lambda: _require_supervisor_block_scope(conn, ctx, item_id, from_id, to_id),
    )
    return {"from_id": from_id, "to_id": to_id, "kind": "blocks"}


@router.delete("/items/{item_id}/supervisor/blocks")
async def supervisor_remove_block(
    item_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    from_id: str,
    to_id: str,
) -> JsonDict:
    tickets_actions.remove_link(
        conn,
        from_id,
        to_id,
        LinkKind.blocks,
        now=clk.now_unix(),
        admit=lambda: _require_supervisor_block_scope(conn, ctx, item_id, from_id, to_id),
    )
    return {"ok": True}


@router.get("/items/{item_id}/supervisor/artifacts")
async def supervisor_list_artifacts(item_id: str, conn: DbConn, ctx: Ctx, cfg: Cfg) -> JsonDict:
    return supervisor_service.list_artifacts(conn, ctx, item_id, cfg.db_path)


@router.put("/items/{item_id}/supervisor/artifacts/{artifact_path:path}")
async def supervisor_write_artifact(
    item_id: str,
    artifact_path: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
) -> JsonDict:
    return supervisor_service.write_artifact(
        conn, ctx, item_id, cfg.db_path, artifact_path, body_str(raw, "content")
    )


@router.delete("/items/{item_id}/supervisor/artifacts/{artifact_path:path}")
async def supervisor_delete_artifact(
    item_id: str,
    artifact_path: str,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
) -> JsonDict:
    return supervisor_service.delete_artifact(conn, ctx, item_id, cfg.db_path, artifact_path)


@router.get("/items/{item_id}/supervisor/conversation/start-values")
async def get_item_supervisor_start_values(item_id: str, conn: DbConn, ctx: Ctx) -> JsonDict:
    require_sprint_item_supervisor_read(conn, ctx, item_id)
    values = conversation_start.sprint_item_supervisor_resolve(
        sprints_data.read_item(conn, item_id).item
    )
    return {
        "backend_key": values.backend_key.value,
        "model": values.model,
        "reasoning_effort": values.reasoning_effort,
    }


def _supervisor_ticket_field(conn: DbConn, ticket_id: str) -> tuple[str, Any]:
    ticket = tickets_data.read_ticket(conn, ticket_id)
    worker_type_definition = configured_worker_type_registry().require(ticket.worker_type)
    field = worker_type_definition.gating_field(ticket.stage)
    if field is None:
        raise PlannerError(
            ErrorCode.validation,
            "ticket has no review field",
            {"ticket_id": ticket_id, "stage": ticket.stage},
        )
    return field, worker_type_definition


@router.post("/items/{item_id}/supervisor/tickets/{ticket_id}/approve")
async def supervisor_approve_ticket(
    item_id: str,
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_sprint_item_supervisor_ticket_write(conn, ctx, item_id, ticket_id)
    body = _marshal_accept(raw)
    field, worker_type_definition = _supervisor_ticket_field(conn, ticket_id)
    now = clk.now_unix()
    ticket = tickets_data.accept_proposal(
        conn,
        ticket_id,
        field=field,
        actor=ctx.actor,
        now=now,
        edited_body=body["edited_body"],
        next_ceiling=_parse_next_ceiling(body["next_ceiling"], worker_type_definition),
        at_cap=_parse_scope_at_cap(body["at_cap"]),
        supervisor_sprint_item_id=item_id,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/items/{item_id}/supervisor/tickets/{ticket_id}/reject")
async def supervisor_reject_ticket(
    item_id: str,
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
    worker_context: WorkerContext,
) -> JsonDict:
    require_sprint_item_supervisor_ticket_write(conn, ctx, item_id, ticket_id)
    message = body_str(raw, "message")
    now = clk.now_unix()
    ticket = await tickets_actions.return_ticket_for_revision(
        conversations,
        worker_context,
        conn,
        ticket_id,
        message=message,
        actor=ctx.actor,
        now=now,
        supervisor_sprint_item_id=item_id,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/items/{item_id}/supervisor/tickets/{ticket_id}/transfer-to-user-review")
async def supervisor_transfer_ticket_to_user_review(
    item_id: str,
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_sprint_item_supervisor_ticket_write(conn, ctx, item_id, ticket_id)
    if raw:
        raise PlannerError(ErrorCode.validation, "transfer body must be empty", {})
    now = clk.now_unix()
    ticket = tickets_data.transfer_proposal_to_user_review(
        conn,
        ticket_id,
        actor=ctx.actor,
        now=now,
        supervisor_sprint_item_id=item_id,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/items/{item_id}/supervisor/conversation/send")
async def send_to_item_supervisor(
    item_id: str,
    body: OwnerSendBody,
    conn: DbConn,
    ctx: Ctx,
    conversations: Conversations,
    message_files: MessageFiles,
    clk: Clk,
) -> JsonDict:
    require_direct_write(ctx)
    async with sprints_service.supervisor_lifecycle_lock(item_id):
        return await _send_to_item_supervisor(
            item_id, body, conn, ctx, conversations, message_files, clk
        )


async def _send_to_item_supervisor(
    item_id: str,
    body: OwnerSendBody,
    conn: DbConn,
    ctx: Ctx,
    conversations: Conversations,
    message_files: MessageFiles,
    clk: Clk,
) -> JsonDict:
    item = sprints_data.read_item(conn, item_id).item
    created_conversation_id = conversation_start.new_conversation_id()
    overrides = ConversationStartOverrides(
        backend_key=(
            None if body.backend_key is None else require_conversation_backend_key(body.backend_key)
        ),
        model=body.model,
        reasoning_effort=body.reasoning_effort,
    )
    current = conversation_start.read_agent_conversation(conn, item.supervisor_agent_key)
    if (
        current is not None
        and overrides.backend_key is not None
        and overrides.backend_key != item.supervisor_launch_configuration.employee_backend
    ):
        raise PlannerError(
            ErrorCode.validation,
            "an existing supervisor conversation cannot change backend",
            {"conversation_id": current, "backend_key": overrides.backend_key.value},
        )
    resolved_start = conversation_start.sprint_item_supervisor_resolve(item, overrides)
    delivered = await conversation_start.send_to_agent_conversation(
        conversations,
        conn,
        item.supervisor_agent_key,
        await conversation_message_content(
            message_files,
            body.conversation_id or current or created_conversation_id,
            body.content,
        ),
        resolved_start,
        conversation_id=body.conversation_id,
        created_conversation_id=created_conversation_id,
        runs_under=overrides,
        sender_label=body.sender_label,
        mode=body.mode,
        sender_message_id=body.sender_message_id,
        sent_at_unix_milliseconds=body.sent_at_unix_milliseconds,
        required_sprint_item_id=item_id,
    )
    created_here = current is None and delivered.conversation_id == created_conversation_id
    if created_here or isinstance(delivered.fate, PromptDeliveryStarted):
        sprints_data.update_supervisor_launch_configuration(
            conn,
            item_id,
            SprintItemSupervisorLaunchConfiguration(
                employee_backend=resolved_start.backend_key,
                employee_launch_model=resolved_start.model,
                employee_launch_reasoning_effort=resolved_start.reasoning_effort,
            ),
            actor=ctx.actor,
            clock=clk,
        )
    return {"conversation_id": delivered.conversation_id, **delivery_fate_json(delivered.fate)}


@router.post("/items/{item_id}/supervisor/conversation/reset")
async def reset_item_supervisor(
    item_id: str, conn: DbConn, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    require_direct_write(ctx)
    async with sprints_service.supervisor_lifecycle_lock(item_id):
        item = sprints_data.read_item(conn, item_id).item
        await conversation_start.reset_agent_conversation(
            conversations, conn, item.supervisor_agent_key
        )
    return {"conversation_id": None}


@router.delete("/items/{item_id}")
async def delete_item(
    item_id: str, conn: DbConn, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    require_direct_write(ctx)
    deleted = await sprints_service.delete_item(conversations, conn, item_id, actor=ctx.actor)
    return {
        "ok": True,
        "sprint_item_id": deleted.sprint_item_id,
        "title": deleted.title,
        "sprint_ids": list(deleted.sprint_ids),
        "linked_entity_ids": list(deleted.linked_entity_ids),
    }


@router.post("/items/{item_id}/tickets")
async def move_item_ticket(
    item_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    body = MoveItemTicketBody(ticket_id=body_str(raw, "ticket_id"))
    tickets_data.move_ticket_to_sprint_item(
        conn,
        body["ticket_id"],
        sprint_item_id=item_id,
        actor=ctx.actor,
        now=clk.now_unix(),
        admit=lambda: require_ticket_worker_write(conn, ctx),
    )
    return sprints_views.item_detail(conn, item_id)


@router.delete("/items/{item_id}/tickets/{ticket_id}")
async def move_item_ticket_to_backlog(
    item_id: str, ticket_id: str, conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    tickets_data.move_ticket_to_backlog(
        conn,
        ticket_id,
        sprint_item_id=item_id,
        actor=ctx.actor,
        now=clk.now_unix(),
        admit=lambda: require_ticket_worker_write(conn, ctx),
    )
    return sprints_views.item_detail(conn, item_id)


@router.patch("/items/{item_id}")
async def patch_item(
    item_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    recognized = set(_ITEM_PLAIN_FIELDS) | {"project", "sprint_id"}
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown item field", {"field": key})
    if not body:
        raise PlannerError(ErrorCode.validation, "no item fields to update", {})
    # Sprint item status is derived from child tickets and blocking links; item patching is
    # only for plain fields and sprint placement.
    if ctx.is_attributed and not ctx.is_chief and ctx.actor != "worker":
        reject_agent_fields(ctx, body, recognized)
    edits: dict[str, str | None] = {}
    for field in _ITEM_PLAIN_FIELDS:
        if field in body:
            if field == "project_id":
                continue
            if field == "deadline":
                value = body_opt_str(body, "deadline")
                _marshal_item_deadline(value)
            elif field in ("title", "body", "priority"):
                value = body_str(body, field)
            else:
                value = body[field]
            edits[field] = value
    if "project" in body or "project_id" in body:
        project = projects_data.resolve_project(
            conn,
            project_id=body_opt_str(body, "project_id"),
            project_name=body_opt_str(body, "project"),
            required=True,
        )
        assert project is not None
        edits["project_id"] = project.id
    sprints_data.update_item(
        conn,
        item_id,
        edits=edits,
        set_sprint="sprint_id" in body,
        sprint_id=body_opt_str(body, "sprint_id"),
        clock=clk,
        admit=lambda: require_planning_write(conn, ctx, "planning-sprint"),
    )
    return sprints_views.item_detail(conn, item_id)


# --- sprint routes -------------------------------------------------------------


@router.post("/sprints")
async def create_sprint(raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    body = _marshal_create_sprint(raw)
    for label, value in (
        ("date_start", body["date_start"]),
        ("date_end", body["date_end"]),
    ):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise PlannerError(ErrorCode.validation, f"invalid {label}", {label: value}) from None
    sprint = sprints_data.create_sprint(
        conn,
        name=body["name"],
        date_start=body["date_start"],
        date_end=body["date_end"],
        limiting_factor=body["limiting_factor"],
        primary_bet=body["primary_bet"],
        supports=body["supports"],
        premortem=body["premortem"],
        clock=clk,
        admit=lambda: require_planning_write(conn, ctx, "planning-sprint"),
    )
    return sprints_views.sprint_json(sprint)


@router.get("/sprints")
async def list_sprints(conn: DbConn) -> JsonDict:
    return {"sprints": sprints_views.list_sprints(conn)}


@router.get("/sprint-summaries")
async def list_sprint_summaries(
    conn: DbConn, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0
) -> JsonDict:
    page = sprints_views.list_sprint_summaries(
        conn, page_request=ListPageRequest(limit=limit, offset=offset)
    )
    return page.response("sprints")


@router.get("/sprints/{sprint_id}")
async def get_sprint(sprint_id: str, conn: DbConn) -> JsonDict:
    return sprints_views.sprint_json(sprints_data.read_sprint(conn, sprint_id))


@router.patch("/sprints/{sprint_id}")
async def patch_sprint(
    sprint_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    recognized = set(_SPRINT_TEXT_FIELDS) | {"date_start", "date_end"}
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown sprint field", {"field": key})
    # Marshal each field to a string, mirroring the Day PATCH: a null or non-string
    # value raises the validation envelope instead of hitting the NOT NULL constraint
    # or a raw SQLite binding error. A null is treated as absent (skipped).
    edits: dict[str, str] = {}
    for field in _SPRINT_TEXT_FIELDS:
        value = body_opt_str(body, field)
        if value is not None:
            edits[field] = value
    setting_dates = "date_start" in body or "date_end" in body
    if not edits and not setting_dates:
        raise PlannerError(ErrorCode.validation, "no sprint fields to update", {})
    sprint = sprints_data.update_sprint(
        conn,
        sprint_id,
        text_edits=edits,
        set_dates=setting_dates,
        date_start=body_opt_str(body, "date_start"),
        date_end=body_opt_str(body, "date_end"),
        clock=clk,
        admit=lambda: require_planning_write(conn, ctx, "planning-sprint"),
    )
    return sprints_views.sprint_json(sprint)


@router.get("/sprint/current")
async def current_sprint(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    planning_date_iso = planning_date(clk.now(), cfg.boundary_hour).isoformat()
    return sprints_views.sprint_current_view(conn, planning_date_iso)


# --- idea routes ---------------------------------------------------------------


@router.post("/ideas")
async def create_idea(raw: dict[str, Any], conn: DbConn, clk: Clk) -> JsonDict:
    body = _marshal_create_idea(raw)
    project = projects_data.resolve_project(
        conn, project_id=body["project_id"], project_name=body["project"]
    )
    idea = sprints_data.create_idea(
        conn,
        title=body["title"],
        body=body["body"],
        project_id=project.id if project is not None else None,
        now=clk.now_unix(),
    )
    return sprints_views.idea_json(idea)


@router.get("/ideas")
async def list_ideas(
    conn: DbConn, project: str | None = None, project_id: str | None = None
) -> JsonDict:
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    return {
        "ideas": sprints_views.list_ideas(
            conn,
            project_id=resolved_project.id if resolved_project is not None else None,
        )
    }
