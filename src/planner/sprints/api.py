"""Sprint-item, sprint, current-sprint view, and idea routes (§9). Thin HTTP shells
over the stage-3 sprint writers and the sprint read views. Ideas are homed here
because the Idea shape lives in this domain's contracts. Sprint writers take a
`clock: Clock`, not `now: int` — do not mix them up."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from planner.conversation.api import OwnerSendBody, conversation_message_content, delivery_fate_json
from planner.conversation.contracts import require_conversation_backend_key
from planner.core import authority
from planner.core.authority import require_above, require_above_or_self
from planner.core.contracts import JsonDict, Principal, PrincipalKind, Priority
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date, resolve_day_id
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.list_reads.contracts import ListPageRequest
from planner.list_reads.detail import (
    ReadDetail,
    parse_read_detail,
    reject_parameters,
    require_full_for_one,
)
from planner.message_delivery import service as message_delivery_service
from planner.projects import data as projects_data
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides
from planner.runtime.worker_step_readiness_loop import start_ready_worker_step
from planner.sprints import commitments, supervisor_service
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.sprints import views as sprints_views
from planner.sprints.contracts import (
    SPRINT_DOCUMENT_FIELDS,
    CreateIdeaBody,
    CreateItemBody,
    CreateSprintBody,
)
from planner.tickets.api import (
    Cfg,
    Clk,
    ConversationRecord,
    Conversations,
    Ctx,
    DbConn,
    MessageFiles,
    body_opt_str,
    body_str,
    body_str_list,
    parse_enum,
    resolve_employee_configuration,
    write_resolved_employee_configuration,
)
from planner.work_attention import add_work_attention
from planner.worker_types.configuration import configured_worker_type_registry

router = APIRouter()

_SPRINT_TEXT_FIELDS = ("name", "primary_bet") + SPRINT_DOCUMENT_FIELDS
_ITEM_PLAIN_FIELDS = ("title", "body", "priority", "deadline", "project_id")


# --- request-body marshallers (contract shapes in sprints/contracts.py) ---------


def _marshal_create_item(raw: JsonDict) -> CreateItemBody:
    if "sprint_id" in raw:
        raise PlannerError(ErrorCode.validation, "Outcome has no single Sprint; add a commitment")
    return CreateItemBody(
        title=body_str(raw, "title"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
        body=body_str(raw, "body"),
        priority=body_opt_str(raw, "priority"),
        deadline=body_opt_str(raw, "deadline"),
    )


def _marshal_create_sprint(raw: JsonDict) -> CreateSprintBody:
    recognized = set(_SPRINT_TEXT_FIELDS) | {"date_start", "date_end"}
    for field in raw:
        if field not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown sprint field", {"field": field})
    return CreateSprintBody(
        name=body_str(raw, "name"),
        date_start=body_str(raw, "date_start"),
        date_end=body_str(raw, "date_end"),
        primary_bet=body_str(raw, "primary_bet"),
        kickoff=body_str(raw, "kickoff"),
        checkpoint=body_str(raw, "checkpoint"),
        review=body_str(raw, "review"),
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
        clock=clk,
    )
    return sprints_views.item_detail(conn, item.id)


@router.get("/items")
async def read_items(
    conn: DbConn,
    conversations: Conversations,
    conversation_record: ConversationRecord,
    detail: str | None = None,
    object_id: Annotated[str | None, Query(alias="id")] = None,
    search: str | None = None,
    project: str | None = None,
    project_id: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> JsonDict:
    """Read Outcomes at the level the caller asks for, or one Outcome by id."""
    level = parse_read_detail(detail)
    if object_id is not None:
        require_full_for_one(level)
        reject_parameters(
            "id",
            {
                "search": search,
                "project": project,
                "project_id": project_id,
                "limit": limit,
                "offset": offset,
            },
        )
        item = sprints_views.item_detail(conn, object_id)
        await add_work_attention(conn, conversations, conversation_record, sprint_items=(item,))
        return item
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    resolved_project_id = resolved_project.id if resolved_project is not None else None
    if level is ReadDetail.full:
        reject_parameters(
            "detail=full", {"search": search, "limit": limit, "offset": offset}
        )
        rows = sprints_views.list_items(conn, project_id=resolved_project_id)
        await add_work_attention(conn, conversations, conversation_record, sprint_items=rows)
        return {"items": rows}
    page = sprints_views.list_item_summaries(
        conn,
        page_request=ListPageRequest(
            limit=DEFAULT_LIST_LIMIT if limit is None else limit,
            offset=0 if offset is None else offset,
        ),
        project_id=resolved_project_id,
        search=search,
    )
    await add_work_attention(conn, conversations, conversation_record, sprint_items=page.rows)
    return page.response("items")


@router.get("/items/{item_id}/workspace")
async def get_item_workspace(
    item_id: str,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    """Return the page facts without creating a second action surface."""
    require_above_or_self(conn, ctx.principal, authority.outcome(item_id))
    planning_day_id = resolve_day_id("today", clk.now(), cfg.boundary_hour)
    result = sprints_views.item_workspace(conn, item_id, planning_day_id)
    await add_work_attention(
        conn,
        conversations,
        conversation_record,
        tickets=result["tickets"],
        sprint_items=(result,),
    )
    result["artifacts"] = supervisor_service.list_artifact_details(conn, ctx, item_id, cfg.db_path)
    return result


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
    require_above_or_self(conn, ctx.principal, authority.outcome(item_id))
    return _supervisor_json(conn, item_id)


@router.get("/items/{item_id}/supervisor/context")
async def get_item_supervisor_context(
    item_id: str,
    conn: DbConn,
    ctx: Ctx,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    """The Item overview: the Item, its supervisor, and one line per child Ticket. The
    supervisor drills into a Ticket through its own ticket-context route."""
    require_above_or_self(conn, ctx.principal, authority.outcome(item_id))
    item = sprints_data.read_item(conn, item_id).item
    tickets = sprints_views.item_ticket_overview(conn, item_id)
    response = {
        "sprint_item": {
            "id": item.id,
            "title": item.title,
            "body": item.body,
            "priority": item.priority.value,
            "project_id": item.project_id,
        },
        "supervisor": _supervisor_json(conn, item_id),
        "tickets": tickets,
    }
    await add_work_attention(
        conn,
        conversations,
        conversation_record,
        tickets=tickets,
        approval_holder=Principal(PrincipalKind.sprint_item, item_id),
    )
    return response


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


@router.post("/items/{item_id}/supervisor/tickets/{ticket_id}/restart-worker")
async def supervisor_restart_worker(
    item_id: str,
    ticket_id: str,
    raw: dict[str, Any],
    request: Request,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    """Start this child Ticket's worker step again, optionally on a named configuration.

    The configuration is resolved before the service is called, so a backend or model
    this Ticket cannot launch on is a plain refusal rather than a killed conversation.
    Item scope is proved before even that: resolving asks the backends what they offer,
    and no caller reaches a question about a Ticket that is not its own.
    """
    supervisor_service.require_current_child(conn, ctx, item_id, ticket_id)
    backend = body_opt_str(raw, "employee_backend")
    model = body_opt_str(raw, "employee_launch_model")
    if (backend is None) != (model is None):
        raise PlannerError(
            ErrorCode.validation,
            "a launch configuration needs both a backend and a model",
            {},
        )
    write_configuration: Callable[[sqlite3.Connection], None] | None = None
    if backend is not None and model is not None:
        resolved = await resolve_employee_configuration(
            request,
            conn,
            ticket_id,
            employee_backend=backend,
            employee_launch_model=model,
            employee_launch_reasoning_effort=body_opt_str(raw, "employee_launch_reasoning_effort"),
        )

        def write_the_resolved_configuration(open_conn: sqlite3.Connection) -> None:
            write_resolved_employee_configuration(
                open_conn, ticket_id, resolved, now=clk.now_unix()
            )

        write_configuration = write_the_resolved_configuration

    planning_day_id = resolve_day_id("today", clk.now(), cfg.boundary_hour)

    async def start_worker_step() -> bool:
        return await start_ready_worker_step(
            ticket_id,
            connect_database=lambda: connect(cfg.db_path, cfg.db_busy_timeout_ms),
            conversation_system=conversations,
            worker_type_registry=configured_worker_type_registry(),
            planning_day_id_resolver=lambda: planning_day_id,
            now=clk.now_unix,
        )

    return await supervisor_service.restart_worker(
        conversations,
        conn,
        ctx,
        item_id,
        ticket_id,
        write_employee_configuration=write_configuration,
        start_worker_step=start_worker_step,
        planning_day_id=planning_day_id,
        now=clk.now_unix(),
    )


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
    require_above_or_self(conn, ctx.principal, authority.outcome(item_id))
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
    require_above(conn, ctx.principal, authority.outcome(item_id))
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
    delivered = await message_delivery_service.send_message(
        conversations,
        conn,
        clk,
        ctx,
        Principal(PrincipalKind.sprint_item, item_id),
        partial(conversation_message_content, message_files, sent=body.content),
        conversation_id=body.conversation_id,
        created_conversation_id=created_conversation_id,
        runs_under=overrides,
        mode=body.mode,
        sender_message_id=body.sender_message_id,
        sent_at_unix_milliseconds=body.sent_at_unix_milliseconds,
    )
    return {"conversation_id": delivered.conversation_id, **delivery_fate_json(delivered.fate)}


@router.post("/items/{item_id}/supervisor/conversation/reset")
async def reset_item_supervisor(
    item_id: str, conn: DbConn, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    require_above(conn, ctx.principal, authority.outcome(item_id))
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
    require_above(conn, ctx.principal, authority.outcome(item_id))
    deleted = await sprints_service.delete_item(
        conversations, conn, item_id, principal=ctx.principal
    )
    return {
        "ok": True,
        "sprint_item_id": deleted.sprint_item_id,
        "title": deleted.title,
        "sprint_ids": list(deleted.sprint_ids),
    }


@router.patch("/items/{item_id}")
async def patch_item(
    item_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    recognized = set(_ITEM_PLAIN_FIELDS) | {"project"}
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown item field", {"field": key})
    if not body:
        raise PlannerError(ErrorCode.validation, "no item fields to update", {})
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
        clock=clk,
        admit=lambda: require_above_or_self(conn, ctx.principal, authority.outcome(item_id)),
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
        primary_bet=body["primary_bet"],
        kickoff=body["kickoff"],
        checkpoint=body["checkpoint"],
        review=body["review"],
        clock=clk,
        admit=lambda: require_above(conn, ctx.principal, authority.plan("sprint")),
    )
    return dict(sprints_views.sprint_json(sprint))


@router.get("/sprints")
async def read_sprints(
    conn: DbConn,
    detail: str | None = None,
    object_id: Annotated[str | None, Query(alias="id")] = None,
    limit: int | None = None,
    offset: int | None = None,
) -> JsonDict:
    """Read Sprints at the level the caller asks for, or one Sprint by id."""
    level = parse_read_detail(detail)
    if object_id is not None:
        require_full_for_one(level)
        reject_parameters("id", {"limit": limit, "offset": offset})
        return dict(sprints_views.sprint_json(sprints_data.read_sprint(conn, object_id)))
    if level is ReadDetail.full:
        reject_parameters("detail=full", {"limit": limit, "offset": offset})
        return {"sprints": sprints_views.list_sprints(conn)}
    page = sprints_views.list_sprint_summaries(
        conn,
        page_request=ListPageRequest(
            limit=DEFAULT_LIST_LIMIT if limit is None else limit,
            offset=0 if offset is None else offset,
        ),
    )
    return page.response("sprints")


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
        admit=lambda: require_above(conn, ctx.principal, authority.plan("sprint")),
    )
    return dict(sprints_views.sprint_json(sprint))


@router.get("/sprint/current")
async def current_sprint(
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    planning_date_iso = planning_date(clk.now(), cfg.boundary_hour).isoformat()
    result = dict(sprints_views.sprint_current_view(conn, planning_date_iso))
    await _add_tracking_attention(conn, conversations, conversation_record, result)
    return result


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


@router.post("/sprints/{source_id}/outcomes/{outcome_id}/carry")
async def carry_outcome(
    source_id: str, outcome_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    if set(body) != {"target_sprint_id", "ticket_ids"}:
        raise PlannerError(
            ErrorCode.validation, "carry requires target_sprint_id and explicit ticket_ids"
        )
    return dict(
        commitments.carry_outcome(
            conn,
            source_id,
            body_str(body, "target_sprint_id"),
            outcome_id,
            body_str_list(body, "ticket_ids"),
            principal=ctx.principal,
            now=clk.now_unix(),
            admit=lambda: require_above(
                conn, ctx.principal, authority.plan("sprint_outcomes")
            ),
        )
    )


@router.get("/sprints/{sprint_id}/tracking")
async def sprint_tracking(
    sprint_id: str,
    conn: DbConn,
    clk: Clk,
    cfg: Cfg,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    result = dict(
        sprints_views.sprint_tracking_view(
            conn, sprint_id, planning_date(clk.now(), cfg.boundary_hour).isoformat()
        )
    )
    await _add_tracking_attention(conn, conversations, conversation_record, result)
    return result


async def _add_tracking_attention(
    conn: DbConn,
    conversations: Conversations,
    conversation_record: ConversationRecord,
    result: JsonDict,
) -> None:
    groups = result.get("outcome_groups", [])
    items = [group["outcome"] for group in groups]
    tickets = [ticket for group in groups for ticket in group["tickets"]]
    tickets.extend(result.get("unclassified_tickets", []))
    await add_work_attention(
        conn,
        conversations,
        conversation_record,
        tickets=tickets,
        sprint_items=items,
    )
