"""Sprint-item, sprint, current-sprint view, and idea routes (§9). Thin HTTP shells
over the stage-3 sprint writers and the sprint read views. Ideas are homed here
because the Idea shape lives in this domain's contracts. Sprint writers take a
`clock: Clock`, not `now: int` — do not mix them up."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter

from planner.conversation.api import OwnerSendBody, conversation_message_content, delivery_fate_json
from planner.conversation.contracts import (
    PromptDeliveryRefused,
    require_conversation_backend_key,
)
from planner.core.authctx import (
    reject_agent_fields,
    require_direct_write,
    require_planning_write,
    require_sprint_item_supervisor_read,
    require_ticket_worker_write,
)
from planner.core.contracts import JsonDict, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.list_reads.contracts import ListPageRequest
from planner.projects import data as projects_data
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides
from planner.sprints import data as sprints_data
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
from planner.tickets import data as tickets_data
from planner.tickets.api import (
    Cfg,
    Clk,
    Conversations,
    Ctx,
    DbConn,
    MessageFiles,
    body_opt_str,
    body_str,
    parse_enum,
)

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


@router.get("/items/{item_id}/supervisor/context")
async def get_item_supervisor_context(item_id: str, conn: DbConn, ctx: Ctx) -> JsonDict:
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
        "tickets": sprints_views.item_tickets(conn, item_id),
    }


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
    )
    if not isinstance(delivered.fate, PromptDeliveryRefused):
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
    item = sprints_data.read_item(conn, item_id).item
    await conversation_start.reset_agent_conversation(
        conversations, conn, item.supervisor_agent_key
    )
    return {"conversation_id": None}


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, conn: DbConn, ctx: Ctx) -> JsonDict:
    require_direct_write(ctx)
    deleted = sprints_data.delete_item(conn, item_id, actor=ctx.actor)
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
