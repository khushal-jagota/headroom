"""Manager, Manager conversation, and Idea HTTP routes."""

from __future__ import annotations

from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response

from planner.conversation.api import (
    OwnerSendBody,
    conversation_message_content,
    delivery_fate_json,
    video_send_admission,
)
from planner.conversation.contracts import require_conversation_backend_key
from planner.conversation.video_uploads.contracts import UploadDestination
from planner.core.authctx import require_owner_or_chief
from planner.core.contracts import JsonDict, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.files import manager_files
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.list_reads.contracts import ListPageRequest
from planner.list_reads.detail import (
    ReadDetail,
    parse_read_detail,
    reject_parameters,
    require_full_for_one,
)
from planner.managers import data as managers_data
from planner.managers import service as managers_service
from planner.managers import startup as managers_startup
from planner.managers import views as managers_views
from planner.managers.contracts import (
    CreateManagerBody,
    ManagerWorkspaceContent,
)
from planner.managers.launch_settings import (
    launch_configuration_json,
    parse_launch_configuration,
    validate_launch_configuration,
)
from planner.message_delivery import service as message_delivery_service
from planner.projects import data as projects_data
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides
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
)
from planner.work_attention import add_work_attention

router = APIRouter()

_ITEM_PLAIN_FIELDS = ("title", "goal", "recap", "project_id")


# --- request-body marshallers (contract shapes in sprints/contracts.py) ---------


def _marshal_create_manager(raw: JsonDict) -> CreateManagerBody:
    if "sprint_id" in raw:
        raise PlannerError(ErrorCode.validation, "Sprint placement is removed")
    return CreateManagerBody(
        title=body_str(raw, "title"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
        goal=body_str(raw, "goal"),
    )


# --- item routes ---------------------------------------------------------------


@router.get("/managers/launch-defaults")
async def get_manager_launch_defaults(conn: DbConn, ctx: Ctx) -> JsonDict:
    require_owner_or_chief(ctx)
    return launch_configuration_json(managers_data.read_launch_defaults(conn))


@router.put("/managers/launch-defaults")
async def put_manager_launch_defaults(raw: dict[str, Any], conn: DbConn, ctx: Ctx) -> JsonDict:
    require_owner_or_chief(ctx)
    configuration = parse_launch_configuration(raw)
    validate_launch_configuration(conn, configuration)
    return launch_configuration_json(
        managers_data.update_launch_defaults(conn, configuration, principal=ctx.principal)
    )


@router.get("/managers/{item_id}/launch-defaults")
async def get_manager_launch_configuration(item_id: str, conn: DbConn, ctx: Ctx) -> JsonDict:
    return launch_configuration_json(
        managers_data.read_manager(conn, item_id).item.manager_launch_configuration
    )


@router.put("/managers/{item_id}/launch-defaults")
async def put_manager_launch_configuration(
    item_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    configuration = parse_launch_configuration(raw)
    async with managers_service.manager_lifecycle_lock(item_id):
        validate_launch_configuration(conn, configuration)
        item = managers_data.read_manager(conn, item_id).item
        current = conversation_start.read_agent_conversation(conn, item.manager_agent_key)
        if current is not None and (
            configuration.employee_backend != item.manager_launch_configuration.employee_backend
        ):
            raise PlannerError(
                ErrorCode.validation,
                "An existing Manager conversation cannot change backend. "
                "Start a new conversation before you choose another backend.",
            )
        saved = managers_data.update_manager_launch_configuration(
            conn, item_id, configuration, principal=ctx.principal, clock=clk, explicit_choice=True
        )
    return launch_configuration_json(saved.manager_launch_configuration)


@router.post("/managers")
async def create_manager(
    raw: dict[str, Any], conn: DbConn, clk: Clk, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    body = _marshal_create_manager(raw)
    project = projects_data.resolve_project(
        conn, project_id=body["project_id"], project_name=body["project"], required=True
    )
    assert project is not None
    item = managers_data.create_manager(
        conn,
        title=body["title"],
        project_id=project.id,
        goal=body["goal"],
        clock=clk,
    )
    startup = await managers_startup.start_created_manager(conversations, conn, clk, ctx, item)
    return {**managers_views.manager_detail(conn, item.id), "startup": startup}


@router.get("/managers")
async def read_managers(
    conn: DbConn,
    conversations: Conversations,
    conversation_record: ConversationRecord,
    detail: str | None = None,
    object_id: Annotated[str | None, Query(alias="id")] = None,
    search: str | None = None,
    settled: bool | None = None,
    project: str | None = None,
    project_id: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> JsonDict:
    """Read Managers at the level the caller asks for, or one Manager by id."""
    level = parse_read_detail(detail)
    if object_id is not None:
        require_full_for_one(level)
        reject_parameters(
            "id",
            {
                "search": search,
                "settled": settled,
                "project": project,
                "project_id": project_id,
                "limit": limit,
                "offset": offset,
            },
        )
        item = managers_views.manager_detail(conn, object_id)
        await add_work_attention(conn, conversations, conversation_record, managers=(item,))
        return item
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    resolved_project_id = resolved_project.id if resolved_project is not None else None
    if level is ReadDetail.full:
        reject_parameters(
            "detail=full", {"search": search, "settled": settled, "limit": limit, "offset": offset}
        )
        rows = managers_views.list_managers(conn, project_id=resolved_project_id)
        await add_work_attention(conn, conversations, conversation_record, managers=rows)
        return {"managers": rows}
    page = managers_views.list_manager_summaries(
        conn,
        page_request=ListPageRequest(
            limit=DEFAULT_LIST_LIMIT if limit is None else limit,
            offset=0 if offset is None else offset,
        ),
        project_id=resolved_project_id,
        search=search,
        settled=settled,
    )
    await add_work_attention(conn, conversations, conversation_record, managers=page.rows)
    return page.response("managers")


@router.get("/managers/{item_id}/workspace-content")
async def get_manager_workspace_content(
    item_id: str, conn: DbConn, ctx: Ctx, response: Response
) -> ManagerWorkspaceContent:
    document = managers_views.manager_workspace_document(conn, item_id)
    result: ManagerWorkspaceContent = {**document}
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/managers/{item_id}/workspace")
async def get_manager_workspace(
    item_id: str,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    """Everything a reader of this Manager needs.

    The two approval words mean the same thing here as on every other route, and the
    projection makes that split itself: ``awaiting_approval`` is Khushal's own queue,
    ``awaiting_agent_approval`` is a proposal parked on an agent. A reader that needs to
    know *which* agent holds one reads ``ceiling_holder`` on the row — the holder is an
    address, and this is the read of it.
    """
    result = managers_views.manager_workspace(conn, item_id)
    await add_work_attention(
        conn,
        conversations,
        conversation_record,
        tickets=result["tickets"],
        managers=(result,),
    )
    result["artifacts"] = manager_files.list_artifact_entries(
        conn, ctx.principal, item_id, cfg.db_path
    )
    return result


@router.get("/managers/{item_id}/conversation/start-values")
async def get_item_conversation_start_values(item_id: str, conn: DbConn, ctx: Ctx) -> JsonDict:
    """What a first message to this Manager's agent would start it on.

    The Manager's conversation door, beside the Ticket's and the Chief's. The prefix it
    lost claimed an authority it never used: this admits Khushal and the Chief, and the
    Manager reading its own start values, exactly as before.
    """
    values = conversation_start.manager_resolve(
        conn, managers_data.read_manager(conn, item_id).item
    )
    return {
        "backend_key": values.backend_key.value,
        "model": values.model,
        "reasoning_effort": values.reasoning_effort,
    }


@router.post("/managers/{item_id}/conversation/send")
async def send_to_item_conversation(
    item_id: str,
    body: OwnerSendBody,
    request: Request,
    conn: DbConn,
    ctx: Ctx,
    conversations: Conversations,
    message_files: MessageFiles,
    clk: Clk,
) -> JsonDict:
    """Talk into this Manager's agent conversation, the way the Ticket door works.

    Not the same operation as POST /messages/send. That command sends one line of text to
    a principal. This is the door a person types at: it carries the browser's own message
    name, the files attached to it, and what the conversation is to run on.
    """
    return await _send_to_manager(
        item_id, body, request, conn, ctx, conversations, message_files, clk
    )


async def _send_to_manager(
    item_id: str,
    body: OwnerSendBody,
    request: Request,
    conn: DbConn,
    ctx: Ctx,
    conversations: Conversations,
    message_files: MessageFiles,
    clk: Clk,
) -> JsonDict:
    item = managers_data.read_manager(conn, item_id).item
    created_conversation_id = conversation_start.new_conversation_id()
    overrides = ConversationStartOverrides(
        backend_key=(
            None if body.backend_key is None else require_conversation_backend_key(body.backend_key)
        ),
        model=body.model,
        reasoning_effort=body.reasoning_effort,
        reasoning_effort_is_set="reasoning_effort" in body.model_fields_set,
    )
    current = conversation_start.read_agent_conversation(conn, item.manager_agent_key)
    if (
        current is not None
        and overrides.backend_key is not None
        and overrides.backend_key != item.manager_launch_configuration.employee_backend
    ):
        raise PlannerError(
            ErrorCode.validation,
            "an existing Manager conversation cannot change backend",
            {"conversation_id": current, "backend_key": overrides.backend_key.value},
        )
    admission = video_send_admission(
        request,
        message_files,
        body.content,
        body.sender_message_id,
        UploadDestination(
            kind="manager", id=item_id, expected_conversation_id=body.conversation_id
        ),
        Principal(PrincipalKind.manager, item_id),
        ctx.principal,
    )
    delivered = await message_delivery_service.send_message(
        conversations,
        conn,
        clk,
        ctx,
        Principal(PrincipalKind.manager, item_id),
        admission
        or partial(
            conversation_message_content,
            message_files,
            sent=body.content,
            sender_message_id=body.sender_message_id,
        ),
        admission=admission,
        conversation_id=body.conversation_id,
        created_conversation_id=created_conversation_id,
        runs_under=overrides,
        mode=body.mode,
        owner_read_through_sequence=body.owner_read_through_sequence,
        sender_message_id=body.sender_message_id,
        sent_at_unix_milliseconds=body.sent_at_unix_milliseconds,
    )
    return {"conversation_id": delivered.conversation_id, **delivery_fate_json(delivered.fate)}


@router.post("/managers/{item_id}/conversation/reset")
async def reset_item_conversation(
    item_id: str, conn: DbConn, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    """Cut this Manager's agent loose from its conversation. This is what New does."""
    async with managers_service.manager_lifecycle_lock(item_id):
        item = managers_data.read_manager(conn, item_id).item
        await conversation_start.reset_agent_conversation(
            conversations, conn, item.manager_agent_key
        )
    return {"conversation_id": None}


@router.delete("/managers/{item_id}")
async def delete_manager(
    item_id: str, conn: DbConn, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    deleted = await managers_service.delete_manager(
        conversations, conn, item_id, principal=ctx.principal
    )
    return {
        "ok": True,
        "manager_id": deleted.manager_id,
        "title": deleted.title,
    }


@router.patch("/managers/{item_id}")
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
            if field in ("title", "goal", "recap"):
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
    managers_data.update_manager(
        conn,
        item_id,
        edits=edits,
        clock=clk,
    )
    return managers_views.manager_detail(conn, item_id)


# --- sprint routes -------------------------------------------------------------


# --- idea routes ---------------------------------------------------------------


@router.patch("/managers/{item_id}/settled")
async def settle_item(
    item_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    if set(raw) != {"settled"} or not isinstance(raw.get("settled"), bool):
        raise PlannerError(ErrorCode.validation, "settled must be a boolean")
    managers_data.set_manager_settled(
        conn, item_id, settled=raw["settled"], principal=ctx.principal, clock=clk
    )
    return managers_views.manager_detail(conn, item_id)
